"""Rako integration client for Hub."""
from asyncio import Task
import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import socket

from homeassistant.components.cover import CoverEntity
from homeassistant.components.light import LightEntity
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from rakopy.hub import Hub
from rakopy.model import Level, LevelChangedEvent, SceneChangedEvent
from .model import RakoDomainEntryData

_LOGGER = logging.getLogger(__name__)

# rakopy reads replies with no timeout while holding its connection lock, so a
# query the hub never answers would block every later command
REFRESH_TIMEOUT = 5

EVENT_CONNECT_TIMEOUT = 10
# Re-read every room from the hub when the event stream has been quiet this long
EVENT_RESYNC_INTERVAL = 300
EVENT_RECONNECT_DELAY = 5
EVENT_RECONNECT_DELAY_MAX = 60
# A NAT or stateful firewall between Home Assistant and the hub forgets an idle
# connection; the hub's events then stop arriving with no error on our side.
# Keepalive probes keep the mapping alive and surface a dead connection as an
# error within a few minutes.
KEEPALIVE_IDLE = 60
KEEPALIVE_INTERVAL = 10
KEEPALIVE_COUNT = 3
# A room in a scene is on, but a hub without scene levels in its project
# reports every channel at 0, so such a room's lights are shown at this level
ASSUMED_ON_LEVEL = 255


@dataclass
class ResyncRequested:
    """Yielded by get_events when the entities should be re-read from the hub."""


def effective_level(level) -> int:
    """Return a channel's target level if it has one, otherwise its current level."""
    if level.target_level is not None:
        return level.target_level
    return level.current_level


def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Turn on TCP keepalive probes for a stream's socket."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (
            ("TCP_KEEPIDLE", KEEPALIVE_IDLE),
            ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL),
            ("TCP_KEEPCNT", KEEPALIVE_COUNT),
        ):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
    except OSError as e:
        _LOGGER.debug("Could not enable keepalive on the hub event stream: %s", repr(e))


def _parse_event(line: bytes):
    """Turn one line of the hub's tracker stream into an event, or None."""
    try:
        json_data = json.loads(line)
    except ValueError:
        _LOGGER.debug("Ignoring unparseable line from the hub: %r", line[:200])
        return None
    if json_data.get("name") != "tracker":
        return None
    payload = json_data.get("payload", {})
    try:
        if json_data.get("type") == "scene":
            return SceneChangedEvent(
                room_id=payload["roomId"],
                channel_id=payload["channelId"],
                scene_id=payload["scene"],
                active_scene_id=payload["activeScene"],
            )
        if json_data.get("type") == "level":
            return LevelChangedEvent(
                room_id=payload["roomId"],
                channel_id=payload["channelId"],
                current_level=payload["currentLevel"],
                target_level=payload["targetLevel"],
                time_to_take=payload["timeToTake"],
                temporary=payload["temporary"],
            )
    except KeyError as e:
        _LOGGER.debug("Ignoring tracker event with missing field %s: %r", e, line[:200])
    return None


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
        # Each room's current scene as the hub reports it: 0 off, -1 custom
        # levels, otherwise the scene number
        self._room_scene: dict[int, int] = {}

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

    def apply_scene(
        self, room_id: int, channel_id: int, scene_id: int, active_scene_id: int | None = None
    ) -> None:
        """Reflect a scene change locally on the affected select, lights and covers.

        Used for commands sent from HA and for scene events from the hub,
        because the hub doesn't push level events for scene 0 or for commands
        sent from HA. No hub query is made, so this can't block commands.

        scene_id is what the room or channel was set to (-1 for explicit
        levels); active_scene_id is the scene the hub still reports the room
        in, which is what the select shows. It defaults to scene_id.

        - Scene 0 on channel 0 turns the whole room off: the room's select goes
          to Off, and every light and cover in the room goes to 0 (closed).
        - Scene 0 on another channel turns off only that channel's light or cover.
        - Scene -1 on channel 0 is explicit levels: the select stays on the
          active scene and the level events that follow carry the state.
        - Any other scene on channel 0 updates the room's select and shows the
          room's lights on (RakoLightEntity.assume_on), because a hub without
          scene levels in its project reports every channel at 0. Callers that
          need channel levels should follow up with refresh_room.
        - Any other scene on another channel (e.g. a blind's open or stop) is
          ignored: it isn't a room scene, so the select must not change.
        """
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            # The entry is being unloaded; there are no entities to update
            return
        hub_id = entry.runtime_data["hub_id"]
        if active_scene_id is None:
            active_scene_id = scene_id

        if channel_id != 0:
            if scene_id != 0:
                return
            unique_id = f"{hub_id}_{room_id}_{channel_id}"
            if (light := self._light_map.get(unique_id)) is not None:
                light.brightness = 0
            if (cover := self._cover_map.get(unique_id)) is not None:
                cover.current_cover_position = 0
            return

        # A room scene: remember it so level events can be read in its light
        self._room_scene[room_id] = scene_id
        scene = self._scene_map.get(f"{hub_id}_{room_id}")
        if scene is not None:
            scene.current_option = active_scene_id
        if scene_id == -1:
            return

        prefix = f"{hub_id}_{room_id}_"
        for unique_id, light in self._light_map.items():
            if not unique_id.startswith(prefix):
                continue
            if scene_id == 0:
                light.brightness = 0
            else:
                light.assume_on()
        if scene_id == 0:
            # Room-wide scene 0 closes blinds and curtains
            for unique_id, cover in self._cover_map.items():
                if unique_id.startswith(prefix):
                    cover.current_cover_position = 0

    async def get_events(self):
        """Subscribe to the hub's tracker stream and yield events from it.

        Replaces rakopy's generator: that one reads with no timeout, dies on a
        closed stream (an empty line is not JSON) and only reconnects on a
        ConnectionError. This one keeps the connection probed, reconnects with
        a backoff on any failure, and yields ResyncRequested after each
        (re)connect and whenever the stream has been quiet for a while, so the
        entities are re-read from the hub.
        """
        delay = EVENT_RECONNECT_DELAY
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=1048576),
                    EVENT_CONNECT_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as e:
                _LOGGER.warning(
                    "Could not connect to the hub event stream (%s), retrying in %s s",
                    repr(e), delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, EVENT_RECONNECT_DELAY_MAX)
                continue

            _enable_keepalive(writer)
            try:
                payload = {
                    "version": 2,
                    "client_name": self.client_name,
                    "subscriptions": ["TRACKER"],
                }
                writer.write(str.encode(f"SUB,JSON,{json.dumps(payload)}\r\n"))
                await writer.drain()
                _LOGGER.debug("Subscribed to the hub event stream")
                delay = EVENT_RECONNECT_DELAY
                yield ResyncRequested()

                while True:
                    try:
                        line = await asyncio.wait_for(reader.readline(), EVENT_RESYNC_INTERVAL)
                    except asyncio.TimeoutError:
                        yield ResyncRequested()
                        continue
                    if not line:
                        raise ConnectionResetError("the hub closed the event stream")
                    event = _parse_event(line)
                    if event is not None:
                        yield event
            except (OSError, asyncio.TimeoutError) as e:
                _LOGGER.warning(
                    "Lost the hub event stream (%s), reconnecting in %s s", repr(e), delay
                )
            finally:
                writer.close()

            await asyncio.sleep(delay)
            delay = min(delay * 2, EVENT_RECONNECT_DELAY_MAX)

    def _apply_room_level(self, level: Level) -> None:
        """Push one room's scene and channel levels, as read from the hub, to its entities."""
        hub_id = self.hub_id
        room_id = level.room_id
        scene_id = level.current_scene_id
        self._room_scene[room_id] = scene_id

        scene = self._scene_map.get(f"{hub_id}_{room_id}")
        if scene is not None and scene_id >= 0:
            scene.current_option = scene_id

        # A hub without scene levels in its project reports 0 for every channel
        # of a room that is in a scene; the room is on, at an unknown level
        levels_known = scene_id <= 0 or any(
            effective_level(channel_level) > 0 for channel_level in level.channel_levels
        )
        for channel_level in level.channel_levels:
            unique_id = f"{hub_id}_{room_id}_{channel_level.channel_id}"
            value = effective_level(channel_level)
            if (light := self._light_map.get(unique_id)) is not None:
                if levels_known:
                    light.brightness = value
                else:
                    light.assume_on()
            if levels_known and (cover := self._cover_map.get(unique_id)) is not None:
                cover.current_cover_position = value

    async def _read_levels(self, what: str):
        """Read every room's levels from the hub, or None when the hub did not answer."""
        try:
            return await asyncio.wait_for(self.get_levels(), REFRESH_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out reading levels for %s, reconnecting", what)
            self._close_connection()
        except (OSError, ValueError) as e:
            _LOGGER.warning("Could not read levels for %s: %s", what, repr(e))
        return None

    async def refresh_room(self, room_id: int) -> None:
        """Re-read a room's channel levels from the hub and push them to its lights and covers."""
        # Query all rooms rather than get_levels(room_id): the all-rooms
        # query is the one the integration has always used at setup
        levels = await self._read_levels(f"room {room_id}")
        if levels is None:
            return
        for level in levels:
            if level.room_id == room_id:
                self._apply_room_level(level)
                break

    async def resync(self) -> None:
        """Re-read every room from the hub and push its state to the entities."""
        levels = await self._read_levels("every room")
        if levels is None:
            return
        for level in levels:
            self._apply_room_level(level)

    def set_channel_level(self, room_id: int, channel_id: int, value: int) -> None:
        """Reflect a level the hub reported for a channel on its light or cover."""
        # After a scene recall a hub without scene levels resets its channel
        # levels to 0 while the lights stay on in the scene
        if value == 0 and self._room_scene.get(room_id, -1) > 0:
            return
        unique_id = f"{self.hub_id}_{room_id}_{channel_id}"
        cover = self._cover_map.get(unique_id)
        if cover is not None:
            cover.current_cover_position = value
        light = self._light_map.get(unique_id)
        if light is not None:
            light.brightness = value

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
            if isinstance(event, LevelChangedEvent):
                hub_client.set_channel_level(
                    event.room_id, event.channel_id, effective_level(event)
                )
            elif isinstance(event, SceneChangedEvent):
                # The hub doesn't push level events for scene 0 (e.g. Off from
                # a keypad), so apply the scene to the affected entities here
                hub_client.apply_scene(
                    event.room_id, event.channel_id, event.scene_id, event.active_scene_id
                )
            elif isinstance(event, ResyncRequested):
                await hub_client.resync()

        except Exception as e:
            _LOGGER.exception("Unexpected exception: %s", repr(e))
