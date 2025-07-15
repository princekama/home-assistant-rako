"""Rako integration client for Hub."""
from asyncio import Task
import asyncio
import contextlib
import logging

from homeassistant.components.light import LightEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.cover import CoverEntity
from homeassistant.core import HomeAssistant
from rakopy.hub import Hub
from rakopy.model import LevelChangedEvent, SceneChangedEvent
from .model import RakoDomainEntryData

_LOGGER = logging.getLogger(__name__)


class HubClient(Hub):
    """Rako Hub Client."""

    def __init__(
        self,
        name: str,
        host: str,
        entry_id: str,
        hass: HomeAssistant,
    ) -> None:
        """Init subclass of rakopy hub."""
        super().__init__(name, host)
        self.entry_id = entry_id
        self.hass = hass

        self._event_listener_task: Task | None = None
        self._light_map: dict[str, LightEntity] = {}
        self._scene_map: dict[str, SelectEntity] = {}
        self._cover_map: dict[str, CoverEntity] = {}

    @property
    def hub_id(self) -> str:
        """Return Hub Id."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        rako_domain_entry_data: RakoDomainEntryData = entry.runtime_data

        return rako_domain_entry_data['hub_id']

    async def add_light(self, light: LightEntity) -> None:
        """Register a light to listen for state updates."""
        self._light_map[light.unique_id] = light
        self._try_start_event_listener_task()

    async def add_scene(self, select: SelectEntity) -> None:
        """Register a select to listen for state updates."""
        self._scene_map[select.unique_id] = select
        self._try_start_event_listener_task()

    async def add_cover(self, cover: CoverEntity) -> None:
        """Register a cover to listen for state updates."""
        self._cover_map[cover.unique_id] = cover
        self._try_start_event_listener_task()

    async def remove_light(self, light: LightEntity) -> None:
        """Deregister a light to listen for state updates."""
        if light.unique_id in self._light_map:
            del self._light_map[light.unique_id]
            self._try_cancel_event_listener_task()

    async def remove_scene(self, select: SelectEntity) -> None:
        """Deregister a select to listen for state updates."""
        if select.unique_id in self._scene_map:
            del self._scene_map[select.unique_id]
            self._try_cancel_event_listener_task()

    async def remove_cover(self, cover: CoverEntity) -> None:
        """Deregister a cover to listen for state updates."""
        if cover.unique_id in self._cover_map:
            del self._cover_map[cover.unique_id]
            self._try_cancel_event_listener_task()

    def _try_start_event_listener_task(self) -> None:
        """Start the event listener task."""
        total_entities = len(self._light_map) + len(self._scene_map) + len(self._cover_map)
        if total_entities == 1:
            self._event_listener_task: Task = asyncio.create_task(
                subscribe_to_events(self), name=f"rako_{self.hub_id}_event_listener_task"
            )

    async def _try_cancel_event_listener_task(self) -> None:
        """Try to cancel event listener task."""
        total_entities = len(self._light_map) + len(self._scene_map) + len(self._cover_map)
        if total_entities == 0:
            if event_listener_task := self._event_listener_task:
                event_listener_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await event_listener_task


async def subscribe_to_events(hub_client: HubClient) -> None:
    """Subscribe to events method."""
    async for event in hub_client.get_events():
        try:
            if event and isinstance(event, LevelChangedEvent):
                unique_id = f"{hub_client.hub_id}_{event.room_id}_{event.channel_id}"
                
                # Handle light entities
                if unique_id in hub_client._light_map:
                    if event.target_level is not None:
                        hub_client._light_map[unique_id].brightness = event.target_level
                    else:
                        hub_client._light_map[unique_id].brightness = event.current_level
                
                # Handle cover entities (blinds use level for position)
                if unique_id in hub_client._cover_map:
                    if event.target_level is not None:
                        hub_client._cover_map[unique_id].current_cover_position = event.target_level
                    else:
                        hub_client._cover_map[unique_id].current_cover_position = event.current_level
                        
            elif event and isinstance(event, SceneChangedEvent):
                unique_id = f"{hub_client.hub_id}_{event.room_id}"
                if unique_id in hub_client._scene_map:
                    hub_client._scene_map[unique_id].current_option = event.active_scene_id
                    
        except Exception as e:
            _LOGGER.exception("Unexpected exception: %s", repr(e))
