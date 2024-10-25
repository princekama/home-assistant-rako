"""Rako platform for select integration."""
from __future__ import annotations

import logging
from typing import List

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from rakopy.model import Room
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
        levels_lookup[level.room_id] = level.current_scene_id

    scenes: list[Entity] = []

    rooms = await hub_client.get_rooms()
    for room in rooms:
        scenes.append(
            RakoSceneEntity(hub_client, room, levels_lookup[room.id])
        )

    async_add_entities(scenes, True)

class RakoSceneEntity(SelectEntity):
    """Representation of a Rako Scene."""

    def __init__(
        self,
        hub_client: HubClient,
        room: Room,
        current_scene_id: int
    ) -> None:
        """Initialize the RakoSceneEntity."""
        self._hub_client = hub_client
        self._room = room
        self._current_scene_id = current_scene_id
        
        self._lookup = {}
        self._reverse_lookup = {}
        for scene in room.scenes:
            self._lookup[scene.id] = scene.title
            self._reverse_lookup[scene.title] = scene.id

    @property
    def current_option(self) -> str:
        """Scenes's current option."""
        if self._current_scene_id in self._lookup:
            return self._lookup[self._current_scene_id]
        return self._lookup[0]

    @current_option.setter
    def current_option(self, value: int) -> None:
        """Set the current option. Used when state is updated outside Home Assistant."""
        self._current_scene_id = value
        for scene in self._room.scenes:
            if scene.id == value:
                self._attr_current_option = scene.title
                self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the display name of this scene."""
        return self._room.title

    @property
    def options(self) -> List[str]:
        """Scenes's list of options."""
        result: List[str] = []
        for scene in self._room.scenes:
            result.append(scene.title)
        return result

    @property
    def should_poll(self) -> bool:
        """Entity pushes its state to HA."""
        return False

    @property
    def unique_id(self) -> str:
        """Scenes's unique ID."""
        return f"{self._hub_client.hub_id}_{self._room.id}"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await self._hub_client.add_scene(self)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await self._hub_client.remove_scene(self)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._hub_client.set_scene(self._room.id, 0, self._reverse_lookup[option])
