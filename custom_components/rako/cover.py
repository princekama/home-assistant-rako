"""Rako platform for cover integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
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

    # Get current levels for all channels
    levels_lookup = {}
    levels = await hub_client.get_levels()
    for level in levels:
        levels_lookup[level.room_id] = {}
        for channel_level in level.channel_levels:
            levels_lookup[level.room_id][channel_level.channel_id] = channel_level

    covers: list[Entity] = []

    # Get all rooms and find BLIND type rooms
    rooms = await hub_client.get_rooms()
    for room in rooms:
        if room.type == "BLIND":
            room_levels = levels_lookup.get(room.id, None)
            if room_levels is not None:
                # Create cover entities for each channel in blind rooms
                for channel in room.channels:
                    channel_level = room_levels.get(channel.id, None)
                    if channel_level is not None:
                        covers.append(
                            RakoCoverEntity(
                                hub_client=hub_client,
                                room=room,
                                channel=channel,
                                channel_level=channel_level
                            )
                        )
                    else:
                        _LOGGER.warning(
                            "Cannot find levels for room %s and channel %s", 
                            room.id, channel.id
                        )
            else:
                _LOGGER.warning("Cannot find levels for room %s", room.id)

    async_add_entities(covers, True)


class RakoCoverEntity(CoverEntity):
    """Representation of a Rako Cover (Blind/Curtain)."""

    def __init__(
        self,
        hub_client: HubClient,
        room: Room,
        channel: Channel,
        channel_level: ChannelLevel
    ) -> None:
        """Initialize a RakoCoverEntity."""
        self._hub_client = hub_client
        self._room = room
        self._channel = channel
        
        # Set initial position from current level
        if channel_level.target_level is not None:
            self._current_position = self._rako_to_ha_position(channel_level.target_level)
        else:
            self._current_position = self._rako_to_ha_position(channel_level.current_level)
        
        # Set supported features
        self._attr_supported_features = (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )
        
        # Set device class based on room/channel name
        self._attr_device_class = self._determine_device_class()

    def _determine_device_class(self) -> CoverDeviceClass:
        """Determine the device class based on the channel name."""
        channel_name = self._channel.title.lower()
        if any(word in channel_name for word in ['curtain', 'drape']):
            return CoverDeviceClass.CURTAIN
        elif any(word in channel_name for word in ['shutter']):
            return CoverDeviceClass.SHUTTER
        elif any(word in channel_name for word in ['awning']):
            return CoverDeviceClass.AWNING
        elif any(word in channel_name for word in ['shade']):
            return CoverDeviceClass.SHADE
        else:
            # Default to blind for most cases
            return CoverDeviceClass.BLIND

    @staticmethod
    def _rako_to_ha_position(rako_level: int) -> int:
        """Convert Rako level (0-255) to Home Assistant position (0-100)."""
        return int((rako_level / 255) * 100)

    @staticmethod
    def _ha_to_rako_position(ha_position: int) -> int:
        """Convert Home Assistant position (0-100) to Rako level (0-255)."""
        return int((ha_position / 100) * 255)

    @property
    def current_cover_position(self) -> int:
        """Return current position of cover (0-100)."""
        return self._current_position

    @current_cover_position.setter
    def current_cover_position(self, value: int) -> None:
        """Set the current position. Used when state is updated outside Home Assistant."""
        self._current_position = self._rako_to_ha_position(value)
        self.async_write_ha_state()

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed."""
        return self._current_position == 0

    @property
    def is_open(self) -> bool:
        """Return if the cover is fully open."""
        return self._current_position == 100

    @property
    def name(self) -> str:
        """Return the display name of this cover."""
        return f"{self._room.title} {self._channel.title}"

    @property
    def should_poll(self) -> bool:
        """Entity pushes its state to HA."""
        return False

    @property
    def unique_id(self) -> str:
        """Cover's unique ID."""
        return f"{self._hub_client.hub_id}_{self._room.id}_{self._channel.id}"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await self._hub_client.add_cover(self)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be removed from hass."""
        await self._hub_client.remove_cover(self)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        try:
            # Send scene 1 (fully open - 255 level) 
            await self._hub_client.set_scene(self._room.id, self._channel.id, 1)
            # Update position optimistically
            self._current_position = 100
            self.async_write_ha_state()
        except SendCommandError:
            _LOGGER.error("An error occurred while opening the Rako Cover")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        try:
            # Send scene 0 (closed)
            await self._hub_client.set_scene(self._room.id, self._channel.id, 0)
            # Update position optimistically  
            self._current_position = 0
            self.async_write_ha_state()
        except SendCommandError:
            _LOGGER.error("An error occurred while closing the Rako Cover")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        try:
            # Send stop command using scene 3 (based on Rako documentation)
            await self._hub_client.set_scene(self._room.id, self._channel.id, 3)
        except SendCommandError:
            _LOGGER.error("An error occurred while stopping the Rako Cover")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return

        try:
            # Convert HA position (0-100) to Rako level (0-255)
            rako_level = self._ha_to_rako_position(position)
            
            # Send level command for precise positioning
            await self._hub_client.set_level(self._room.id, self._channel.id, rako_level)
            
            # Update position optimistically
            self._current_position = position
            self.async_write_ha_state()
            
        except SendCommandError:
            _LOGGER.error("An error occurred while setting the Rako Cover position")
