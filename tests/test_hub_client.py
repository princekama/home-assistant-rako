"""Tests for hub_client.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant

from rakopy.model import Level, LevelChangedEvent, SceneChangedEvent

import socket

from custom_components.rako.hub_client import (
    EVENT_RECONNECT_DELAY,
    HubClient,
    ResyncRequested,
    _enable_keepalive,
    subscribe_to_events,
)
from tests.conftest import MOCK_HOST, MOCK_HUB_ID, MOCK_NAME, make_channel_level


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hub_client(hass: HomeAssistant) -> HubClient:
    """Create a HubClient with the rakopy Hub.__init__ patched out."""
    with patch("custom_components.rako.hub_client.Hub.__init__", return_value=None):
        client = HubClient(
            name=MOCK_NAME,
            host=MOCK_HOST,
            entry_id="test_entry_id",
            hass=hass,
        )
    return client


def _make_live_client(hass: HomeAssistant) -> HubClient:
    """HubClient whose hub_id resolves, for driving the event handlers for real."""
    client = _make_hub_client(hass)
    mock_entry = MagicMock()
    mock_entry.runtime_data = {"hub_id": MOCK_HUB_ID}
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
    client._light_map = {}
    client._cover_map = {}
    client._scene_map = {}
    return client


def _mock_entity(unique_id: str) -> MagicMock:
    """Return a mock entity with a unique_id."""
    entity = MagicMock()
    entity.unique_id = unique_id
    return entity


# ---------------------------------------------------------------------------
# HubClient.hub_id
# ---------------------------------------------------------------------------

async def test_hub_id_property(hass: HomeAssistant) -> None:
    """Test that hub_id reads from the config entry runtime data."""
    client = _make_hub_client(hass)

    mock_entry = MagicMock()
    mock_entry.runtime_data = {"hub_id": MOCK_HUB_ID}
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

    assert client.hub_id == MOCK_HUB_ID
    hass.config_entries.async_get_entry.assert_called_once_with("test_entry_id")


# ---------------------------------------------------------------------------
# add / remove entities & event listener lifecycle
# ---------------------------------------------------------------------------

async def test_add_light_starts_event_listener(hass: HomeAssistant) -> None:
    """Adding the first entity should start the event listener task."""
    client = _make_hub_client(hass)

    with patch.object(client, "_try_start_event_listener_task") as mock_start:
        await client.add_light(_mock_entity("light_1"))

    assert "light_1" in client._light_map
    mock_start.assert_called_once()


async def test_add_cover_starts_event_listener(hass: HomeAssistant) -> None:
    """Adding the first cover should start the event listener."""
    client = _make_hub_client(hass)

    with patch.object(client, "_try_start_event_listener_task") as mock_start:
        await client.add_cover(_mock_entity("cover_1"))

    assert "cover_1" in client._cover_map
    mock_start.assert_called_once()


async def test_add_scene_starts_event_listener(hass: HomeAssistant) -> None:
    """Adding the first scene should start the event listener."""
    client = _make_hub_client(hass)

    with patch.object(client, "_try_start_event_listener_task") as mock_start:
        await client.add_scene(_mock_entity("scene_1"))

    assert "scene_1" in client._scene_map
    mock_start.assert_called_once()


async def test_remove_light_cancels_listener_when_last(hass: HomeAssistant) -> None:
    """Removing the last entity should cancel the event listener task."""
    client = _make_hub_client(hass)
    entity = _mock_entity("light_1")
    client._light_map["light_1"] = entity

    with patch.object(client, "_try_cancel_event_listener_task", new_callable=AsyncMock) as mock_cancel:
        await client.remove_light(entity)

    assert "light_1" not in client._light_map
    mock_cancel.assert_awaited_once()


async def test_remove_cover_cancels_listener_when_last(hass: HomeAssistant) -> None:
    """Removing the last cover should cancel the event listener task."""
    client = _make_hub_client(hass)
    entity = _mock_entity("cover_1")
    client._cover_map["cover_1"] = entity

    with patch.object(client, "_try_cancel_event_listener_task", new_callable=AsyncMock) as mock_cancel:
        await client.remove_cover(entity)

    assert "cover_1" not in client._cover_map
    mock_cancel.assert_awaited_once()


async def test_remove_scene_cancels_listener_when_last(hass: HomeAssistant) -> None:
    """Removing the last scene should cancel the event listener task."""
    client = _make_hub_client(hass)
    entity = _mock_entity("scene_1")
    client._scene_map["scene_1"] = entity

    with patch.object(client, "_try_cancel_event_listener_task", new_callable=AsyncMock) as mock_cancel:
        await client.remove_scene(entity)

    assert "scene_1" not in client._scene_map
    mock_cancel.assert_awaited_once()


async def test_remove_unknown_entity_is_noop(hass: HomeAssistant) -> None:
    """Removing an entity that was never added should be a no-op."""
    client = _make_hub_client(hass)
    entity = _mock_entity("nonexistent")

    # Should not raise
    await client.remove_light(entity)
    await client.remove_cover(entity)
    await client.remove_scene(entity)


async def test_try_start_only_on_first_entity(hass: HomeAssistant) -> None:
    """Event listener task is only created when the first entity is added."""
    client = _make_hub_client(hass)

    # Patch hub_id so asyncio.create_task name works
    mock_entry = MagicMock()
    mock_entry.runtime_data = {"hub_id": MOCK_HUB_ID}
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

    # Patch subscribe_to_events to avoid real coroutine
    with patch("custom_components.rako.hub_client.subscribe_to_events", new_callable=AsyncMock):
        await client.add_light(_mock_entity("l1"))
        task = client._event_listener_task
        assert task is not None

        await client.add_light(_mock_entity("l2"))
        # Task should not have been replaced
        assert client._event_listener_task is task


async def test_try_cancel_only_when_empty(hass: HomeAssistant) -> None:
    """Event listener task is only cancelled when all entities are removed."""
    client = _make_hub_client(hass)

    # Create an actual asyncio task that we can cancel
    async def noop():
        await asyncio.sleep(3600)

    task = asyncio.create_task(noop())
    client._event_listener_task = task
    client._light_map = {"l1": _mock_entity("l1"), "l2": _mock_entity("l2")}

    await client.remove_light(_mock_entity("l1"))
    # Still one entity left — task should still be running
    assert not task.cancelled()

    await client.remove_light(_mock_entity("l2"))
    # All entities removed — task should be cancelled
    assert task.cancelled()


# ---------------------------------------------------------------------------
# subscribe_to_events
# ---------------------------------------------------------------------------

async def _run_subscribe_with_events(hub_client, events):
    """Helper to run subscribe_to_events with a list of mocked events."""

    async def mock_get_events():
        for event in events:
            yield event

    hub_client.get_events = mock_get_events

    await subscribe_to_events(hub_client)


async def test_subscribe_level_changed_updates_light(hass: HomeAssistant) -> None:
    """LevelChangedEvent should update the matching light's brightness."""
    client = _make_live_client(hass)

    light = MagicMock()
    uid = f"{MOCK_HUB_ID}_1_1"
    client._light_map = {uid: light}
    client._cover_map = {}
    client._scene_map = {}

    event = LevelChangedEvent(
        room_id=1, channel_id=1, current_level=100, target_level=200, time_to_take=0, temporary=False
    )
    await _run_subscribe_with_events(client, [event])

    assert light.brightness == 200


async def test_subscribe_level_changed_uses_current_when_no_target(hass: HomeAssistant) -> None:
    """LevelChangedEvent with target_level=None should use current_level."""
    client = _make_live_client(hass)

    light = MagicMock()
    uid = f"{MOCK_HUB_ID}_1_1"
    client._light_map = {uid: light}
    client._cover_map = {}
    client._scene_map = {}

    event = LevelChangedEvent(
        room_id=1, channel_id=1, current_level=150, target_level=None, time_to_take=0, temporary=False
    )
    await _run_subscribe_with_events(client, [event])

    assert light.brightness == 150


async def test_subscribe_level_changed_updates_cover(hass: HomeAssistant) -> None:
    """LevelChangedEvent should update the matching cover's position."""
    client = _make_live_client(hass)

    cover = MagicMock()
    uid = f"{MOCK_HUB_ID}_2_1"
    client._cover_map = {uid: cover}
    client._light_map = {}
    client._scene_map = {}

    event = LevelChangedEvent(
        room_id=2, channel_id=1, current_level=0, target_level=255, time_to_take=0, temporary=False
    )
    await _run_subscribe_with_events(client, [event])

    assert cover.current_cover_position == 255


async def test_subscribe_scene_changed_applies_scene(hass: HomeAssistant) -> None:
    """SceneChangedEvent should be applied locally via apply_scene."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

    client.refresh_room = AsyncMock()

    scene = MagicMock()
    uid = f"{MOCK_HUB_ID}_1"
    client._scene_map = {uid: scene}
    client._light_map = {}
    client._cover_map = {}

    event = SceneChangedEvent(
        room_id=1, channel_id=0, scene_id=2, active_scene_id=2
    )
    await _run_subscribe_with_events(client, [event])

    client.apply_scene.assert_called_once_with(1, 0, 2, 2)


async def test_subscribe_scene_off_passes_channel(hass: HomeAssistant) -> None:
    """SceneChangedEvent for scene 0 should pass its channel so only that scope goes off."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID
    client.refresh_room = AsyncMock()
    client._light_map = {}
    client._scene_map = {}
    client._cover_map = {}

    event = SceneChangedEvent(
        room_id=1, channel_id=2, scene_id=0, active_scene_id=0
    )
    await _run_subscribe_with_events(client, [event])

    client.apply_scene.assert_called_once_with(1, 2, 0, 0)
    # Scene 0 is handled locally; re-reading levels could return stale values
    client.refresh_room.assert_not_awaited()


async def test_subscribe_scene_custom_levels_passes_both_scene_ids(hass: HomeAssistant) -> None:
    """Explicit levels arrive as scene -1 with the room's active scene; both reach apply_scene."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

    event = SceneChangedEvent(
        room_id=1, channel_id=0, scene_id=-1, active_scene_id=2
    )
    await _run_subscribe_with_events(client, [event])

    client.apply_scene.assert_called_once_with(1, 0, -1, 2)


async def test_subscribe_scene_on_does_not_query_hub(hass: HomeAssistant) -> None:
    """SceneChangedEvent for a non-zero scene must not query the hub from the listener.

    A hub query here blocked commands on a live install (issue #29).
    """
    client = _make_live_client(hass)
    client.refresh_room = AsyncMock()

    room_light = MagicMock()
    room_light.brightness = 0
    client._light_map = {f"{MOCK_HUB_ID}_1_0": room_light}
    client._scene_map = {}
    client._cover_map = {}

    event = SceneChangedEvent(
        room_id=1, channel_id=0, scene_id=2, active_scene_id=2
    )
    await _run_subscribe_with_events(client, [event])

    client.refresh_room.assert_not_awaited()
    # The room is on in its scene, at a level the hub does not report
    room_light.assume_on.assert_called_once_with()


async def test_subscribe_handles_exception_gracefully(hass: HomeAssistant) -> None:
    """An exception inside the event loop should be caught and logged."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

    # Light map accessor raises
    client._light_map = MagicMock()
    client._light_map.__contains__ = MagicMock(side_effect=RuntimeError("boom"))
    client._cover_map = {}
    client._scene_map = {}

    event = LevelChangedEvent(
        room_id=1, channel_id=1, current_level=100, target_level=200, time_to_take=0, temporary=False
    )

    # Should not raise
    await _run_subscribe_with_events(client, [event])


async def test_subscribe_ignores_none_events(hass: HomeAssistant) -> None:
    """None events should be silently skipped."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID
    client._light_map = {}
    client._cover_map = {}
    client._scene_map = {}

    # Should not raise
    await _run_subscribe_with_events(client, [None])


async def test_subscribe_ignores_unmatched_events(hass: HomeAssistant) -> None:
    """Events for entities not in any map should be silently skipped."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID
    client._light_map = {}
    client._cover_map = {}
    client._scene_map = {}

    event = LevelChangedEvent(
        room_id=99, channel_id=99, current_level=100, target_level=200, time_to_take=0, temporary=False
    )
    # Should not raise
    await _run_subscribe_with_events(client, [event])


# ---------------------------------------------------------------------------
# refresh_room
# ---------------------------------------------------------------------------

def _make_refresh_client(hass: HomeAssistant, levels) -> HubClient:
    """HubClient whose hub_id resolves and whose get_levels returns `levels`."""
    client = _make_hub_client(hass)
    mock_entry = MagicMock()
    mock_entry.runtime_data = {"hub_id": MOCK_HUB_ID}
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
    client.get_levels = AsyncMock(return_value=levels)
    return client


async def test_refresh_room_updates_room_lights(hass: HomeAssistant) -> None:
    """refresh_room should push the room's current channel levels to its lights."""
    levels = [
        Level(
            room_id=1,
            current_scene_id=1,
            channel_levels=[
                make_channel_level(0, 10, target_level=128),
                make_channel_level(1, 50),
            ],
        ),
    ]
    client = _make_refresh_client(hass, levels)
    room_light = MagicMock()
    channel_light = MagicMock()
    client._light_map = {
        f"{MOCK_HUB_ID}_1_0": room_light,
        f"{MOCK_HUB_ID}_1_1": channel_light,
    }

    await client.refresh_room(1)

    # All-rooms query, the one used at setup; see refresh_room
    client.get_levels.assert_awaited_once_with()
    # target_level is preferred when present, current_level otherwise
    assert room_light.brightness == 128
    assert channel_light.brightness == 50


async def test_refresh_room_updates_room_covers(hass: HomeAssistant) -> None:
    """refresh_room should push the room's channel levels to its covers too."""
    levels = [
        Level(
            room_id=2,
            current_scene_id=1,
            channel_levels=[
                make_channel_level(1, 0, target_level=255),
                make_channel_level(2, 128),
            ],
        ),
    ]
    client = _make_refresh_client(hass, levels)
    blind = MagicMock()
    curtain = MagicMock()
    client._cover_map = {
        f"{MOCK_HUB_ID}_2_1": blind,
        f"{MOCK_HUB_ID}_2_2": curtain,
    }

    await client.refresh_room(2)

    assert blind.current_cover_position == 255
    assert curtain.current_cover_position == 128


async def test_refresh_room_only_touches_requested_room(hass: HomeAssistant) -> None:
    """Lights in other rooms must not be changed."""
    levels = [
        Level(room_id=1, current_scene_id=1, channel_levels=[make_channel_level(0, 200)]),
        Level(room_id=2, current_scene_id=1, channel_levels=[make_channel_level(0, 99)]),
    ]
    client = _make_refresh_client(hass, levels)
    room_1_light = MagicMock()
    room_2_light = MagicMock()
    room_2_light.brightness = 7
    client._light_map = {
        f"{MOCK_HUB_ID}_1_0": room_1_light,
        f"{MOCK_HUB_ID}_2_0": room_2_light,
    }

    await client.refresh_room(1)

    assert room_1_light.brightness == 200
    assert room_2_light.brightness == 7


async def test_refresh_room_updates_unadded_lights(hass: HomeAssistant) -> None:
    """Channels with no registered light are skipped; a light not yet added still gets the level."""
    levels = [
        Level(
            room_id=1,
            current_scene_id=1,
            channel_levels=[make_channel_level(0, 200), make_channel_level(1, 150)],
        ),
    ]
    client = _make_refresh_client(hass, levels)
    not_added = MagicMock()
    not_added.hass = None
    not_added.brightness = 3
    client._light_map = {f"{MOCK_HUB_ID}_1_0": not_added}

    # Channel 1 has no registered light. Should not raise.
    await client.refresh_room(1)

    # The entity's setter skips the HA write itself when hass is None
    assert not_added.brightness == 200


async def test_refresh_room_handles_get_levels_failure(hass: HomeAssistant) -> None:
    """A failure reading levels from the hub should be logged, not raised."""
    client = _make_refresh_client(hass, [])
    client.get_levels = AsyncMock(side_effect=ConnectionResetError("hub unreachable"))
    light = MagicMock()
    light.brightness = 42
    client._light_map = {f"{MOCK_HUB_ID}_1_0": light}

    await client.refresh_room(1)

    assert light.brightness == 42


async def test_refresh_room_propagates_unexpected_errors(hass: HomeAssistant) -> None:
    """Errors other than connection or parse failures should not be swallowed."""
    client = _make_refresh_client(hass, [])
    client.get_levels = AsyncMock(side_effect=AttributeError("bug"))

    with pytest.raises(AttributeError):
        await client.refresh_room(1)


async def test_refresh_room_times_out_and_reconnects(hass: HomeAssistant) -> None:
    """A query the hub never answers should time out and drop the connection."""
    client = _make_refresh_client(hass, [])

    async def never_answers():
        await asyncio.sleep(3600)

    client.get_levels = never_answers
    writer = MagicMock()
    client._reader = MagicMock()
    client._writer = writer
    light = MagicMock()
    light.brightness = 42
    client._light_map = {f"{MOCK_HUB_ID}_1_0": light}

    with patch("custom_components.rako.hub_client.REFRESH_TIMEOUT", 0.01):
        await client.refresh_room(1)

    writer.close.assert_called_once()
    assert client._reader is None
    assert client._writer is None
    assert light.brightness == 42


async def test_close_connection_without_connection(hass: HomeAssistant) -> None:
    """Closing when no connection was ever opened should be a no-op."""
    client = _make_hub_client(hass)
    client._reader = None
    client._writer = None

    client._close_connection()

    assert client._writer is None


# ---------------------------------------------------------------------------
# _query / _send logging wrappers
# ---------------------------------------------------------------------------

async def test_query_delegates_to_rakopy(hass: HomeAssistant) -> None:
    """_query should pass through to rakopy and return its result."""
    client = _make_hub_client(hass)
    func = MagicMock()

    with patch(
        "custom_components.rako.hub_client.Hub._query", new_callable=AsyncMock, return_value=["x"]
    ) as mock_query:
        result = await client._query("LEVEL", func, 3)

    mock_query.assert_awaited_once_with("LEVEL", func, 3)
    assert result == ["x"]


async def test_send_delegates_to_rakopy(hass: HomeAssistant) -> None:
    """_send should pass through to rakopy."""
    client = _make_hub_client(hass)
    request = {"name": "send"}

    with patch("custom_components.rako.hub_client.Hub._send", new_callable=AsyncMock) as mock_send:
        await client._send(request)

    mock_send.assert_awaited_once_with(request)


# ---------------------------------------------------------------------------
# apply_scene
# ---------------------------------------------------------------------------

def _make_apply_client(hass: HomeAssistant) -> HubClient:
    """HubClient with one select, light and cover in room 1 and in room 2."""
    client = _make_hub_client(hass)
    mock_entry = MagicMock()
    mock_entry.runtime_data = {"hub_id": MOCK_HUB_ID}
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
    client._scene_map = {}
    client._light_map = {}
    client._cover_map = {}
    for room in (1, 2):
        scene = MagicMock()
        scene.current_option = 1
        client._scene_map[f"{MOCK_HUB_ID}_{room}"] = scene
        for channel in (0, 1, 2):
            light = MagicMock()
            light.brightness = 99
            client._light_map[f"{MOCK_HUB_ID}_{room}_{channel}"] = light
        cover = MagicMock()
        cover.current_cover_position = 100
        client._cover_map[f"{MOCK_HUB_ID}_{room}_3"] = cover
    return client


def test_apply_scene_room_off(hass: HomeAssistant) -> None:
    """Scene 0 on channel 0 turns off the room's select, lights and covers only."""
    client = _make_apply_client(hass)
    # An entity not yet added to hass still gets the value (its setter skips the write)
    unadded_light = MagicMock()
    unadded_light.hass = None
    unadded_light.brightness = 77
    client._light_map[f"{MOCK_HUB_ID}_1_9"] = unadded_light

    client.apply_scene(1, 0, 0)

    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 0
    for channel in (0, 1, 2, 9):
        assert client._light_map[f"{MOCK_HUB_ID}_1_{channel}"].brightness == 0
    assert client._cover_map[f"{MOCK_HUB_ID}_1_3"].current_cover_position == 0
    assert client._scene_map[f"{MOCK_HUB_ID}_2"].current_option == 1
    for channel in (0, 1, 2):
        assert client._light_map[f"{MOCK_HUB_ID}_2_{channel}"].brightness == 99
    assert client._cover_map[f"{MOCK_HUB_ID}_2_3"].current_cover_position == 100


def test_apply_scene_channel_off_only_affects_that_channel(hass: HomeAssistant) -> None:
    """Scene 0 on a single channel leaves the room's select and other channels alone."""
    client = _make_apply_client(hass)

    client.apply_scene(1, 2, 0)

    assert client._light_map[f"{MOCK_HUB_ID}_1_2"].brightness == 0
    assert client._light_map[f"{MOCK_HUB_ID}_1_0"].brightness == 99
    assert client._light_map[f"{MOCK_HUB_ID}_1_1"].brightness == 99
    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 1
    assert client._cover_map[f"{MOCK_HUB_ID}_1_3"].current_cover_position == 100


def test_apply_scene_channel_off_closes_that_cover(hass: HomeAssistant) -> None:
    """Scene 0 on a blind's channel closes just that blind."""
    client = _make_apply_client(hass)

    client.apply_scene(1, 3, 0)

    assert client._cover_map[f"{MOCK_HUB_ID}_1_3"].current_cover_position == 0
    assert client._light_map[f"{MOCK_HUB_ID}_1_0"].brightness == 99
    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 1


def test_apply_scene_non_zero_updates_select_and_assumes_lights_on(hass: HomeAssistant) -> None:
    """A non-zero scene updates the room's select and shows its lights on at their last level.

    Channel levels are left to refresh_room; covers are not touched.
    """
    client = _make_apply_client(hass)

    client.apply_scene(1, 0, 3)

    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 3
    client._light_map[f"{MOCK_HUB_ID}_1_0"].assume_on.assert_called_once_with()
    assert client._light_map[f"{MOCK_HUB_ID}_1_0"].brightness == 99
    assert client._cover_map[f"{MOCK_HUB_ID}_1_3"].current_cover_position == 100
    assert client._scene_map[f"{MOCK_HUB_ID}_2"].current_option == 1


@pytest.mark.parametrize("scene_id", [1, 3])
def test_apply_scene_non_zero_on_channel_leaves_room_alone(
    hass: HomeAssistant, scene_id: int
) -> None:
    """A blind's open (1) or stop (3) on its channel must not change the room's select."""
    client = _make_apply_client(hass)

    client.apply_scene(1, 3, scene_id)

    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 1
    assert client._cover_map[f"{MOCK_HUB_ID}_1_3"].current_cover_position == 100
    assert client._light_map[f"{MOCK_HUB_ID}_1_0"].brightness == 99


def test_apply_scene_without_config_entry_does_nothing(hass: HomeAssistant) -> None:
    """During unload the config entry can be gone; apply_scene must not raise."""
    client = _make_apply_client(hass)
    hass.config_entries.async_get_entry = MagicMock(return_value=None)

    client.apply_scene(1, 0, 0)

    assert client._light_map[f"{MOCK_HUB_ID}_1_0"].brightness == 99


# ---------------------------------------------------------------------------
# Scene-aware level handling
# ---------------------------------------------------------------------------

def test_set_channel_level_ignores_reset_while_room_in_scene(hass: HomeAssistant) -> None:
    """A level of 0 for a room in a scene is the hub resetting its tracker, not a light going off."""
    client = _make_live_client(hass)
    light = MagicMock()
    light.brightness = 200
    client._light_map = {f"{MOCK_HUB_ID}_1_1": light}
    client._room_scene = {1: 1}

    client.set_channel_level(1, 1, 0)
    assert light.brightness == 200

    # a real level still wins
    client.set_channel_level(1, 1, 90)
    assert light.brightness == 90


def test_set_channel_level_applies_zero_outside_a_scene(hass: HomeAssistant) -> None:
    """With explicit levels (scene -1) or the room off, a 0 level turns the light off."""
    client = _make_live_client(hass)
    light = MagicMock()
    light.brightness = 200
    client._light_map = {f"{MOCK_HUB_ID}_1_1": light}

    client._room_scene = {1: -1}
    client.set_channel_level(1, 1, 0)
    assert light.brightness == 0

    light.brightness = 200
    client._room_scene = {}
    client.set_channel_level(1, 1, 0)
    assert light.brightness == 0


def test_apply_scene_tracks_room_scene_and_updates_entities(hass: HomeAssistant) -> None:
    """A room scene updates the select, turns the room's lights on or off, and is remembered."""
    client = _make_apply_client(hass)
    other_light = client._light_map[f"{MOCK_HUB_ID}_2_0"]

    client.apply_scene(1, 0, 3)
    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 3
    for channel in (0, 1, 2):
        client._light_map[f"{MOCK_HUB_ID}_1_{channel}"].assume_on.assert_called_once_with()
    other_light.assume_on.assert_not_called()
    assert client._room_scene[1] == 3

    client.apply_scene(1, 0, 0)
    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 0
    for channel in (0, 1, 2):
        assert client._light_map[f"{MOCK_HUB_ID}_1_{channel}"].brightness == 0
    assert other_light.brightness == 99
    assert client._room_scene[1] == 0


def test_apply_scene_custom_levels_leaves_lights_to_level_events(hass: HomeAssistant) -> None:
    """Scene -1 (explicit levels) keeps the select on the active scene and touches no light."""
    client = _make_apply_client(hass)
    light = client._light_map[f"{MOCK_HUB_ID}_1_0"]

    client.apply_scene(1, 0, -1, 2)

    assert client._scene_map[f"{MOCK_HUB_ID}_1"].current_option == 2
    light.assume_on.assert_not_called()
    assert light.brightness == 99
    assert client._room_scene[1] == -1


def test_apply_scene_channel_scene_does_not_track_room_scene(hass: HomeAssistant) -> None:
    """A channel's own scene (a blind's open, or one channel off) is not the room's scene."""
    client = _make_apply_client(hass)
    client._room_scene = {1: 2}

    client.apply_scene(1, 3, 1)
    client.apply_scene(1, 2, 0)

    assert client._room_scene[1] == 2


def test_apply_room_level_assumes_on_when_hub_reports_zero_in_scene(hass: HomeAssistant) -> None:
    """A room in a scene whose channels all read 0 is on at an unknown level."""
    client = _make_live_client(hass)
    scene = MagicMock()
    room_light = MagicMock()
    channel_light = MagicMock()
    cover = MagicMock()
    cover.current_cover_position = 40
    client._scene_map = {f"{MOCK_HUB_ID}_1": scene}
    client._light_map = {f"{MOCK_HUB_ID}_1_0": room_light, f"{MOCK_HUB_ID}_1_1": channel_light}
    client._cover_map = {f"{MOCK_HUB_ID}_1_2": cover}

    client._apply_room_level(
        Level(room_id=1, current_scene_id=2, channel_levels=[
            make_channel_level(0, 0), make_channel_level(1, 0), make_channel_level(2, 0),
        ])
    )

    assert scene.current_option == 2
    room_light.assume_on.assert_called_once_with()
    channel_light.assume_on.assert_called_once_with()
    # the hub does not know the cover's position either, so it is left alone
    assert cover.current_cover_position == 40
    assert client._room_scene[1] == 2


def test_apply_room_level_off_and_custom_use_levels(hass: HomeAssistant) -> None:
    """Scene 0 and scene -1 take the hub's levels as they are; -1 does not touch the select."""
    client = _make_live_client(hass)
    scene = MagicMock()
    scene.current_option = 1
    light = MagicMock()
    light.brightness = 200
    client._scene_map = {f"{MOCK_HUB_ID}_1": scene}
    client._light_map = {f"{MOCK_HUB_ID}_1_0": light}

    client._apply_room_level(Level(room_id=1, current_scene_id=0, channel_levels=[make_channel_level(0, 0)]))
    assert scene.current_option == 0
    assert light.brightness == 0

    client._apply_room_level(Level(room_id=1, current_scene_id=-1, channel_levels=[make_channel_level(0, 77)]))
    assert scene.current_option == 0
    assert light.brightness == 77
    assert client._room_scene[1] == -1


async def test_resync_applies_every_room(hass: HomeAssistant) -> None:
    """resync reads all rooms once and pushes each of them."""
    levels = [
        Level(room_id=1, current_scene_id=-1, channel_levels=[make_channel_level(0, 11)]),
        Level(room_id=2, current_scene_id=1, channel_levels=[make_channel_level(0, 0)]),
    ]
    client = _make_refresh_client(hass, levels)
    light_1 = MagicMock()
    light_2 = MagicMock()
    client._light_map = {f"{MOCK_HUB_ID}_1_0": light_1, f"{MOCK_HUB_ID}_2_0": light_2}

    await client.resync()

    client.get_levels.assert_awaited_once_with()
    assert light_1.brightness == 11
    light_2.assume_on.assert_called_once_with()


async def test_resync_handles_get_levels_failure(hass: HomeAssistant) -> None:
    """A hub that cannot be read leaves the entities alone."""
    client = _make_refresh_client(hass, [])
    client.get_levels = AsyncMock(side_effect=ConnectionResetError("hub unreachable"))
    light = MagicMock()
    light.brightness = 42
    client._light_map = {f"{MOCK_HUB_ID}_1_0": light}

    await client.resync()

    assert light.brightness == 42


async def test_subscribe_resync_requested_resyncs(hass: HomeAssistant) -> None:
    """A ResyncRequested marker from the event stream re-reads the hub."""
    client = MagicMock()
    client.resync = AsyncMock()

    await _run_subscribe_with_events(client, [ResyncRequested()])

    client.resync.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------

class _FakeReader:
    """StreamReader stand-in fed from a list of lines; None blocks forever, b"" is EOF."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            await asyncio.sleep(3600)
        line = self._lines.pop(0)
        if line is None:
            await asyncio.sleep(3600)
        return line


def _fake_connection(reader):
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.get_extra_info = MagicMock(return_value=None)

    async def open_connection(*args, **kwargs):
        return reader, writer

    return open_connection, writer


async def _collect_events(client, count):
    events = []
    async for event in client.get_events():
        events.append(event)
        if len(events) == count:
            break
    return events


async def test_get_events_parses_tracker_lines(hass: HomeAssistant) -> None:
    """The stream yields a resync first, then the hub's scene and level events."""
    client = _make_live_client(hass)
    client.client_name = "test"
    client.host = MOCK_HOST
    client.port = 9762
    reader = _FakeReader([
        b'{"name":"connected","payload":{}}\r\n',
        b'{"name":"tracker","type":"scene","payload":{"roomId":10,"channelId":0,"scene":1,"activeScene":1}}\r\n',
        b'not json\r\n',
        b'{"name":"tracker","type":"level","payload":{"roomId":10,"channelId":1,"currentLevel":200,"targetLevel":0,"timeToTake":1248,"temporary":false}}\r\n',
    ])
    open_connection, writer = _fake_connection(reader)

    with patch("custom_components.rako.hub_client.asyncio.open_connection", open_connection):
        events = await _collect_events(client, 3)

    assert isinstance(events[0], ResyncRequested)
    assert events[1] == SceneChangedEvent(room_id=10, channel_id=0, scene_id=1, active_scene_id=1)
    assert events[2] == LevelChangedEvent(
        room_id=10, channel_id=1, current_level=200, target_level=0, time_to_take=1248, temporary=False
    )
    assert writer.write.call_args[0][0].startswith(b"SUB,JSON,")
    assert b'"TRACKER"' in writer.write.call_args[0][0]


async def test_get_events_reconnects_after_eof(hass: HomeAssistant) -> None:
    """A closed stream is reopened, after a delay, and asks for a resync again."""
    client = _make_live_client(hass)
    client.client_name = "test"
    client.host = MOCK_HOST
    client.port = 9762
    readers = [_FakeReader([b""]), _FakeReader([None])]
    writers = []

    async def open_connection(*args, **kwargs):
        reader = readers.pop(0)
        _, writer = _fake_connection(reader)
        writers.append(writer)
        return reader, writer

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    with patch("custom_components.rako.hub_client.asyncio.open_connection", open_connection), \
         patch("custom_components.rako.hub_client.asyncio.sleep", fake_sleep):
        events = await _collect_events(client, 2)

    assert all(isinstance(event, ResyncRequested) for event in events)
    assert len(writers) == 2
    writers[0].close.assert_called_once()
    assert sleeps == [EVENT_RECONNECT_DELAY]


async def test_get_events_resyncs_when_quiet(hass: HomeAssistant) -> None:
    """A stream with nothing to say for the resync interval asks for a resync."""
    client = _make_live_client(hass)
    client.client_name = "test"
    client.host = MOCK_HOST
    client.port = 9762
    open_connection, _ = _fake_connection(_FakeReader([None]))

    with patch("custom_components.rako.hub_client.asyncio.open_connection", open_connection), \
         patch("custom_components.rako.hub_client.EVENT_RESYNC_INTERVAL", 0.01):
        events = await _collect_events(client, 2)

    assert all(isinstance(event, ResyncRequested) for event in events)


async def test_get_events_retries_failed_connect(hass: HomeAssistant) -> None:
    """A refused connection is retried with a growing delay."""
    client = _make_live_client(hass)
    client.client_name = "test"
    client.host = MOCK_HOST
    client.port = 9762
    attempts = []

    async def open_connection(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionRefusedError("refused")
        reader = _FakeReader([None])
        return reader, _fake_connection(reader)[1]

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    with patch("custom_components.rako.hub_client.asyncio.open_connection", open_connection), \
         patch("custom_components.rako.hub_client.asyncio.sleep", fake_sleep):
        events = await _collect_events(client, 1)

    assert isinstance(events[0], ResyncRequested)
    assert sleeps == [EVENT_RECONNECT_DELAY, EVENT_RECONNECT_DELAY * 2]


def test_enable_keepalive_sets_socket_options() -> None:
    """Keepalive is switched on with the probe timings."""
    sock = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=sock)

    _enable_keepalive(writer)

    calls = sock.setsockopt.call_args_list
    assert calls[0][0] == (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    assert len(calls) >= 1
