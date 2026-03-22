"""Tests for select.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.rako.select import RakoSceneEntity, async_setup_entry
from rakopy.model import Room, Scene

from tests.conftest import MOCK_HUB_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scene_entity(
    hub_client,
    room: Room | None = None,
    current_scene_id: int = 1,
) -> RakoSceneEntity:
    """Convenience factory for RakoSceneEntity."""
    if room is None:
        room = Room(
            id=1,
            title="Living Room",
            type="LIGHT",
            mode="NORMAL",
            channels=[],
            scenes=[
                Scene(id=0, title="Off"),
                Scene(id=1, title="Scene 1"),
                Scene(id=2, title="Scene 2"),
            ],
        )
    return RakoSceneEntity(hub_client=hub_client, room=room, current_scene_id=current_scene_id)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_setup_entry_discovers_scenes(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """async_setup_entry should create a scene entity for every room."""
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    scenes = [e for e in added if isinstance(e, RakoSceneEntity)]
    # 2 rooms (light + blind) → 2 scene entities
    assert len(scenes) == 2


async def test_setup_entry_warns_missing_levels(
    hass: HomeAssistant,
    mock_config_entry,
    mock_hub_client,
) -> None:
    """Rooms with no levels should not crash."""
    mock_hub_client.get_levels = AsyncMock(return_value=[])
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    assert len(added) == 0


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def test_current_option(mock_hub_client) -> None:
    """current_option should return the title of the current scene."""
    entity = _make_scene_entity(mock_hub_client, current_scene_id=1)
    assert entity.current_option == "Scene 1"


def test_current_option_fallback_to_zero(mock_hub_client) -> None:
    """If current scene id is not in lookup, fall back to scene 0 title."""
    entity = _make_scene_entity(mock_hub_client, current_scene_id=99)
    assert entity.current_option == "Off"


def test_options(mock_hub_client) -> None:
    """options should return all scene titles in order."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity.options == ["Off", "Scene 1", "Scene 2"]


def test_name(mock_hub_client) -> None:
    """Name should be the room title."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity.name == "Living Room"


def test_should_poll(mock_hub_client) -> None:
    """should_poll should be False."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity.should_poll is False


def test_unique_id(mock_hub_client) -> None:
    """Unique ID format should be hub_id_room_id."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity.unique_id == f"{MOCK_HUB_ID}_1"


# ---------------------------------------------------------------------------
# Lookup dictionaries
# ---------------------------------------------------------------------------

def test_lookup_dict(mock_hub_client) -> None:
    """Lookup should map scene id → title."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity._lookup == {0: "Off", 1: "Scene 1", 2: "Scene 2"}


def test_reverse_lookup_dict(mock_hub_client) -> None:
    """Reverse lookup should map title → scene id."""
    entity = _make_scene_entity(mock_hub_client)
    assert entity._reverse_lookup == {"Off": 0, "Scene 1": 1, "Scene 2": 2}


# ---------------------------------------------------------------------------
# async_select_option
# ---------------------------------------------------------------------------

async def test_select_option(mock_hub_client) -> None:
    """async_select_option should call set_scene with the correct scene id."""
    entity = _make_scene_entity(mock_hub_client)

    await entity.async_select_option("Scene 2")

    mock_hub_client.set_scene.assert_awaited_once_with(1, 0, 2)


async def test_select_option_off(mock_hub_client) -> None:
    """Selecting 'Off' should send scene id 0."""
    entity = _make_scene_entity(mock_hub_client)

    await entity.async_select_option("Off")

    mock_hub_client.set_scene.assert_awaited_once_with(1, 0, 0)


# ---------------------------------------------------------------------------
# current_option setter (used by event listener)
# ---------------------------------------------------------------------------

def test_current_option_setter(mock_hub_client) -> None:
    """Setting current_option via the setter should update the scene id."""
    entity = _make_scene_entity(mock_hub_client, current_scene_id=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(entity, "async_write_ha_state", lambda: None)
        entity.current_option = 2

    assert entity._current_scene_id == 2
    assert entity._attr_current_option == "Scene 2"


def test_current_option_setter_unknown_scene(mock_hub_client) -> None:
    """Setting current_option to an unknown scene id should update scene id but not attr."""
    entity = _make_scene_entity(mock_hub_client, current_scene_id=0)
    entity._attr_current_option = "Off"

    entity.current_option = 99

    assert entity._current_scene_id == 99
    # _attr_current_option should remain unchanged since scene 99 doesn't exist
    assert entity._attr_current_option == "Off"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def test_async_added_to_hass(mock_hub_client) -> None:
    """async_added_to_hass should register scene with hub client."""
    entity = _make_scene_entity(mock_hub_client)
    await entity.async_added_to_hass()
    mock_hub_client.add_scene.assert_awaited_once_with(entity)


async def test_async_will_remove_from_hass(mock_hub_client) -> None:
    """async_will_remove_from_hass should deregister scene from hub client."""
    entity = _make_scene_entity(mock_hub_client)
    await entity.async_will_remove_from_hass()
    mock_hub_client.remove_scene.assert_awaited_once_with(entity)
