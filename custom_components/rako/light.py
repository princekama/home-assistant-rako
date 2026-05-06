"""Rako platform for light integration."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from rakopy.errors import SendCommandError
from rakopy.model import Channel, ChannelLevel, Room
from .hub_client import HubClient
from .model import RakoDomainEntryData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the config entry."""
    rako_domain_entry_data: RakoDomainEntryData = entry.runtime_data
    hub_client = rako_domain_entry_data["hub_client"]

    levels_lookup = {}
    levels = await hub_client.get_levels()
    for level in levels:
        levels_lookup[level.room_id] = {}
        for channel_level in level.channel_levels:
            levels_lookup[level.room_id][channel_level.channel_id] = channel_level

    lights: list[Entity] = []

    rooms = await hub_client.get_rooms()

    # First pass: collect RGBW channel groups keyed by (room_id, color_title).
    # A group is complete when it has all four components.
    rgbw_groups: dict[tuple[int, str], dict[str, tuple[Room, Channel, ChannelLevel]]] = defaultdict(dict)
    rgbw_channel_ids: set[tuple[int, int]] = set()

    for room in rooms:
        if room.type != "LIGHT":
            continue
        room_levels = levels_lookup.get(room.id)
        if room_levels is None:
            continue
        for channel in room.channels:
            if not channel.multi_channel_component:
                continue
            channel_level = room_levels.get(channel.id)
            if channel_level is None:
                continue
            key = (room.id, channel.color_title)
            rgbw_groups[key][channel.multi_channel_component] = (room, channel, channel_level)
            rgbw_channel_ids.add((room.id, channel.id))

    for (room_id, color_title), components in rgbw_groups.items():
        required = {"RED", "GREEN", "BLUE", "WHITE"}
        missing = required - components.keys()
        if missing:
            _LOGGER.warning(
                "Incomplete RGBW group '%s' in room %s — missing: %s",
                color_title, room_id, missing
            )
            continue
        room = components["RED"][0]
        lights.append(RakoRGBWLightEntity(hub_client, room, color_title, components))

    # Second pass: create individual brightness entities, skipping RGBW components.
    for room in rooms:
        if room.type != "LIGHT":
            continue
        room_levels = levels_lookup.get(room.id)
        if room_levels is None:
            _LOGGER.warning("Cannot find levels for room %s", room.id)
            continue

        channel_level = room_levels.get(0)
        if channel_level is not None:
            lights.append(
                RakoLightEntity(hub_client=hub_client, room=room, channel=None, channel_level=channel_level)
            )
        else:
            _LOGGER.warning("Cannot find levels for room %s and channel %s", room.id, 0)

        for channel in room.channels:
            if (room.id, channel.id) in rgbw_channel_ids:
                continue
            channel_level = room_levels.get(channel.id)
            if channel_level is not None:
                lights.append(
                    RakoLightEntity(hub_client=hub_client, room=room, channel=channel, channel_level=channel_level)
                )
            else:
                _LOGGER.warning("Cannot find levels for room %s and channel %s", room.id, channel.id)

    async_add_entities(lights, True)


class RakoLightEntity(LightEntity):
    """Representation of a Rako Light."""

    def __init__(
            self,
            hub_client: HubClient,
            room: Room,
            channel: Channel,
            channel_level: ChannelLevel
        ) -> None:
        """Initialize a RakoLightEntity."""
        self._hub_client = hub_client
        self._room = room
        self._channel = channel
        if channel_level.target_level != None:
            self._brightness = channel_level.target_level
        else:
            self._brightness = channel_level.current_level
        self.supported_color_modes = {ColorMode.BRIGHTNESS}
        self.color_mode = ColorMode.BRIGHTNESS

    @property
    def brightness(self) -> int:
        """Return the brightness of the light."""
        return self._brightness

    @brightness.setter
    def brightness(self, value: int) -> None:
        """Set the brightness. Used when state is updated outside Home Assistant."""
        self._brightness = value
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self.brightness > 0

    @property
    def name(self) -> str:
        """Return the display name of this light."""
        if not self._channel:
            return self._room.title
        return self._channel.title

    @property
    def should_poll(self) -> bool:
        """Entity pushes its state to HA."""
        return False

    @property
    def unique_id(self) -> str:
        """Light's unique ID."""
        if not self._channel:
            return f"{self._hub_client.hub_id}_{self._room.id}_0"
        return f"{self._hub_client.hub_id}_{self._room.id}_{self._channel.id}"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await self._hub_client.add_light(self)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be removed from hass."""
        await self._hub_client.remove_light(self)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        if not self._channel:
            await self._hub_client.set_scene(self._room.id, 0, 0)
        else:
            await self.async_turn_on(brightness=0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        try:
            if self._channel:
                await self._hub_client.set_level(self._room.id, self._channel.id, brightness)
            else:
                await self._hub_client.set_level(self._room.id, 0, brightness)
            self.brightness = brightness

        except (SendCommandError):
            _LOGGER.error("An error occurred while updating the Rako Light")


class RakoRGBWLightEntity(LightEntity):
    """Representation of a Rako RGBW channel group as a single colour light."""

    def __init__(
            self,
            hub_client: HubClient,
            room: Room,
            color_title: str,
            components: dict[str, tuple[Room, Channel, ChannelLevel]],
        ) -> None:
        """Initialize a RakoRGBWLightEntity."""
        self._hub_client = hub_client
        self._room = room
        self._color_title = color_title
        # Map component name → Channel object
        self._channels: dict[str, Channel] = {
            comp: channel for comp, (_, channel, _) in components.items()
        }
        # Initial levels from hub
        def _level(comp: str) -> int:
            _, _, cl = components[comp]
            return cl.target_level if cl.target_level is not None else cl.current_level
        self._r = _level("RED")
        self._g = _level("GREEN")
        self._b = _level("BLUE")
        self._w = _level("WHITE")

        self.supported_color_modes = {ColorMode.RGBW}
        self.color_mode = ColorMode.RGBW

    @property
    def name(self) -> str:
        return self._color_title

    @property
    def unique_id(self) -> str:
        # Anchored to the RED channel id (the primary/colorType channel)
        return f"{self._hub_client.hub_id}_{self._room.id}_{self._channels['RED'].id}_rgbw"

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def is_on(self) -> bool:
        return any(v > 0 for v in (self._r, self._g, self._b, self._w))

    @property
    def rgbw_color(self) -> tuple[int, int, int, int]:
        return (self._r, self._g, self._b, self._w)

    def update_channel_level(self, channel_id: int, level: int) -> None:
        """Update a single component level from a hub push event."""
        for comp, channel in self._channels.items():
            if channel.id == channel_id:
                if comp == "RED":
                    self._r = level
                elif comp == "GREEN":
                    self._g = level
                elif comp == "BLUE":
                    self._b = level
                elif comp == "WHITE":
                    self._w = level
                break
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await self._hub_client.add_rgbw_light(self)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be removed from hass."""
        await self._hub_client.remove_rgbw_light(self)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all RGBW channels."""
        try:
            for channel in self._channels.values():
                await self._hub_client.set_level(self._room.id, channel.id, 0)
            self._r = self._g = self._b = self._w = 0
            self.async_write_ha_state()
        except SendCommandError:
            _LOGGER.error("An error occurred while turning off RGBW light %s", self._color_title)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the RGBW light, optionally setting colour and/or brightness."""
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if rgbw is not None:
            r, g, b, w = rgbw
        else:
            r, g, b, w = self._r, self._g, self._b, self._w
            if not any((r, g, b, w)):
                # Was fully off with no stored colour — default to warm white
                w = 255

        if brightness is not None:
            scale = brightness / 255
            r = round(r * scale)
            g = round(g * scale)
            b = round(b * scale)
            w = round(w * scale)

        try:
            levels = {"RED": r, "GREEN": g, "BLUE": b, "WHITE": w}
            for comp, level in levels.items():
                await self._hub_client.set_level(self._room.id, self._channels[comp].id, level)
            self._r, self._g, self._b, self._w = r, g, b, w
            self.async_write_ha_state()
        except SendCommandError:
            _LOGGER.error("An error occurred while updating RGBW light %s", self._color_title)
