"""Tests for hub_client.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant

from rakopy.model import LevelChangedEvent, SceneChangedEvent

from custom_components.rako.hub_client import HubClient, subscribe_to_events
from tests.conftest import MOCK_HOST, MOCK_HUB_ID, MOCK_NAME


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
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

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
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

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
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

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


async def test_subscribe_scene_changed_updates_scene(hass: HomeAssistant) -> None:
    """SceneChangedEvent should update the matching scene select entity."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID

    scene = MagicMock()
    uid = f"{MOCK_HUB_ID}_1"
    client._scene_map = {uid: scene}
    client._light_map = {}
    client._cover_map = {}

    event = SceneChangedEvent(
        room_id=1, channel_id=0, scene_id=2, active_scene_id=2
    )
    await _run_subscribe_with_events(client, [event])

    assert scene.current_option == 2


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
