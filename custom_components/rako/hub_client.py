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

# rakopy reads replies with no timeout while holding its connection lock, so a
# query the hub never answers would block every later command
REFRESH_TIMEOUT = 5


def effective_level(level) -> int:
    """Return a channel's target level if it has one, otherwise its current level."""
    if level.target_level is not None:
        return level.target_level
    return level.current_level


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

    async def remove_scene(self, select: SelectEntity) -> None:
        """Deregister a select to listen for state updates."""
        if select.unique_id in self._scene_map:
            del self._scene_map[select.unique_id]
            await self._try_cancel_event_listener_task()

    async def _query(self, query_type: str, func, room_id: int = None):
        """Run a rakopy query, logging the request for debugging."""
        _LOGGER.debug("Querying hub: %s room %s", query_type, room_id)
        result = await super()._query(query_type, func, room_id)
        _LOGGER.debug("Hub answered %s room %s", query_type, room_id)
        return result

    async def _send(self, request) -> None:
        """Send a rakopy command, logging the request for debugging."""
        _LOGGER.debug("Sending to hub: %s", request)
        await super()._send(request)
        _LOGGER.debug("Hub acknowledged: %s", request)

    def _close_connection(self) -> None:
        """Drop the command connection so the next call reconnects.

        After an abandoned query the hub's late reply would otherwise be read
        as the reply to the next command.
        """
        if self._writer is not None:
            self._writer.close()
        self._reader = None
        self._writer = None

    async def refresh_room(self, room_id: int) -> None:
        """Re-read a room's channel levels from the hub and push them to its lights and covers."""
        try:
            # Query all rooms rather than get_levels(room_id): the all-rooms
            # query is the one the integration has always used at setup
            levels = await asyncio.wait_for(self.get_levels(), REFRESH_TIMEOUT)
        except TimeoutError:
            _LOGGER.warning("Timed out refreshing levels for room %s, reconnecting", room_id)
            self._close_connection()
            return
        except (OSError, ValueError) as e:
            _LOGGER.warning("Could not refresh levels for room %s: %s", room_id, repr(e))
            return

        hub_id = self.hub_id
        for level in levels:
            if level.room_id != room_id:
                continue
            for channel_level in level.channel_levels:
                unique_id = f"{hub_id}_{room_id}_{channel_level.channel_id}"
                light = self._light_map.get(unique_id)
                if light is not None and getattr(light, "hass", None) is not None:
                    light.brightness = effective_level(channel_level)
                cover = self._cover_map.get(unique_id)
                if cover is not None and getattr(cover, "hass", None) is not None:
                    cover.current_cover_position = effective_level(channel_level)
            break

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

                # Handle cover entities (blinds use level for position)
                if unique_id in hub_client._cover_map:
                    hub_client._cover_map[unique_id].current_cover_position = effective_level(event)

                # Handle light entities
                if unique_id in hub_client._light_map:
                    hub_client._light_map[unique_id].brightness = effective_level(event)

            elif event and isinstance(event, SceneChangedEvent):
                unique_id = f"{hub_client.hub_id}_{event.room_id}"
                if unique_id in hub_client._scene_map:
                    hub_client._scene_map[unique_id].current_option = event.active_scene_id

                # Scene 0 is "off" (e.g. from a keypad), and the hub does not
                # push level events for it, so turn off the room's lights here
                if event.active_scene_id == 0:
                    light_prefix = f"{unique_id}_"
                    for light_unique_id, light in hub_client._light_map.items():
                        if light_unique_id.startswith(light_prefix):
                            light.brightness = 0

        except Exception as e:
            _LOGGER.exception("Unexpected exception: %s", repr(e))
