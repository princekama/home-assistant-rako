"""Rako platform for light integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
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
    for room in rooms:
        if room.type == "LIGHT":
            room_levels = levels_lookup.get(room.id, None)
            if room_levels != None:
                channel_level = room_levels.get(0, None)
                if channel_level != None:
                    lights.append(
                        RakoLightEntity(
                            hub_client=hub_client,
                            room=room,
                            channel=None,
                            channel_level=channel_level
                        )
                    )
                else:
                    _LOGGER.warning("Cannot find levels for room %s and channel %s", room.id, 0)
                
                for channel in room.channels:
                    channel_level = room_levels.get(channel.id, None)
                    if channel_level != None:
                        lights.append(
                            RakoLightEntity(
                                hub_client=hub_client,
                                room=room,
                                channel=channel,
                                channel_level=channel_level
                            )
                        )
                    else:
                        _LOGGER.warning("Cannot find levels for room %s and channel %s", room.id, channel.id)
            else:
                _LOGGER.warning("Cannot find levels for room %s", room.id)

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
        # Only support brigthness for now
        if not channel or not channel.color_type:
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
        """Run when entity about to be added to hass."""
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
