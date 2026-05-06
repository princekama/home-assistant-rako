"""Rako integration client for Hub."""
from asyncio import Task
import asyncio
import contextlib
import logging

from homeassistant.components.cover import CoverEntity
from homeassistant.components.light import LightEntity
from homeassistant.components.select import SelectEntity
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
        self._cover_map: dict[str, CoverEntity] = {}
        self._light_map: dict[str, LightEntity] = {}
        self._scene_map: dict[str, SelectEntity] = {}
        # Maps each component channel's unique_id to its RGBW entity.
        # One RGBW entity registers four entries (R, G, B, W) here.
        self._rgbw_map: dict[str, any] = {}
        # Tracks distinct RGBW entity objects for accurate entity counting.
        self._rgbw_entities: set = set()

    @property
    def hub_id(self) -> str:
        """Return Hub Id."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        rako_domain_entry_data: RakoDomainEntryData = entry.runtime_data

        return rako_domain_entry_data['hub_id']

    async def add_cover(self, cover: CoverEntity) -> None:
        """Register a cover to listen for state updates."""
        self._cover_map[cover.unique_id] = cover
        self._try_start_event_listener_task()

    async def add_light(self, light: LightEntity) -> None:
        """Register a light to listen for state updates."""
        self._light_map[light.unique_id] = light
        self._try_start_event_listener_task()

    async def add_rgbw_light(self, light) -> None:
        """Register an RGBW light, mapping each component channel to the entity."""
        for comp, channel in light._channels.items():
            uid = f"{self.hub_id}_{light._room.id}_{channel.id}"
            self._rgbw_map[uid] = light
        self._rgbw_entities.add(light)
        self._try_start_event_listener_task()

    async def add_scene(self, select: SelectEntity) -> None:
        """Register a select to listen for state updates."""
        self._scene_map[select.unique_id] = select
        self._try_start_event_listener_task()

    async def remove_cover(self, cover: CoverEntity) -> None:
        """Deregister a cover to listen for state updates."""
        if cover.unique_id in self._cover_map:
            del self._cover_map[cover.unique_id]
            await self._try_cancel_event_listener_task()

    async def remove_light(self, light: LightEntity) -> None:
        """Deregister a light to listen for state updates."""
        if light.unique_id in self._light_map:
            del self._light_map[light.unique_id]
            await self._try_cancel_event_listener_task()

    async def remove_rgbw_light(self, light) -> None:
        """Deregister an RGBW light."""
        for comp, channel in light._channels.items():
            uid = f"{self.hub_id}_{light._room.id}_{channel.id}"
            self._rgbw_map.pop(uid, None)
        self._rgbw_entities.discard(light)
        await self._try_cancel_event_listener_task()

    async def remove_scene(self, select: SelectEntity) -> None:
        """Deregister a select to listen for state updates."""
        if select.unique_id in self._scene_map:
            del self._scene_map[select.unique_id]
            await self._try_cancel_event_listener_task()

    def _try_start_event_listener_task(self) -> None:
        """Start the event listener task."""
        total_entities = (
            len(self._light_map) +
            len(self._scene_map) +
            len(self._cover_map) +
            len(self._rgbw_entities)
        )
        if total_entities == 1:
            self._event_listener_task: Task = asyncio.create_task(
                subscribe_to_events(self), name=f"rako_{self.hub_id}_event_listener_task"
            )

    async def _try_cancel_event_listener_task(self) -> None:
        """Try to cancel event listener task."""
        total_entities = (
            len(self._light_map) +
            len(self._scene_map) +
            len(self._cover_map) +
            len(self._rgbw_entities)
        )
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
                level = event.target_level if event.target_level is not None else event.current_level

                # Handle cover entities (blinds use level for position)
                if unique_id in hub_client._cover_map:
                    hub_client._cover_map[unique_id].current_cover_position = level

                # Handle individual brightness light entities
                if unique_id in hub_client._light_map:
                    hub_client._light_map[unique_id].brightness = level

                # Handle RGBW group entities
                if unique_id in hub_client._rgbw_map:
                    hub_client._rgbw_map[unique_id].update_channel_level(event.channel_id, level)

            elif event and isinstance(event, SceneChangedEvent):
                unique_id = f"{hub_client.hub_id}_{event.room_id}"
                if unique_id in hub_client._scene_map:
                    hub_client._scene_map[unique_id].current_option = event.active_scene_id

        except Exception as e:
            _LOGGER.exception("Unexpected exception: %s", repr(e))
