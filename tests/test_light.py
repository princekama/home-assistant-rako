"""Tests for light.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant

from custom_components.rako.light import RakoLightEntity, async_setup_entry
from rakopy.errors import SendCommandError
from rakopy.model import Channel, Room

from tests.conftest import MOCK_HUB_ID, make_channel_level


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_light(
    hub_client,
    room: Room | None = None,
    channel: Channel | None = None,
    brightness: int = 128,
    target: int | None = None,
) -> RakoLightEntity:
    """Convenience factory for RakoLightEntity."""
    if room is None:
        room = Room(id=1, title="Living Room", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    cl = make_channel_level(channel.id if channel else 0, brightness, target)
    return RakoLightEntity(hub_client=hub_client, room=room, channel=channel, channel_level=cl)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_setup_entry_discovers_lights(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """async_setup_entry should discover light rooms and add entities."""
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    # 1 room-level light + 2 channel-level lights = 3
    light_entities = [e for e in added if isinstance(e, RakoLightEntity)]
    assert len(light_entities) == 3


async def test_setup_entry_skips_blind_rooms(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """Blind rooms should not produce light entities."""
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    for entity in added:
        assert isinstance(entity, RakoLightEntity)
        assert entity._room.type == "LIGHT"


async def test_setup_entry_warns_missing_levels(
    hass: HomeAssistant,
    mock_config_entry,
    mock_hub_client,
) -> None:
    """Rooms with no matching levels should log a warning, not crash."""
    mock_hub_client.get_levels = AsyncMock(return_value=[])
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    assert len(added) == 0


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def test_brightness(mock_hub_client) -> None:
    """Test brightness getter returns the correct value."""
    light = _make_light(mock_hub_client, brightness=200)
    assert light.brightness == 200


def test_brightness_uses_target_level(mock_hub_client) -> None:
    """When target_level is set, it should be used for initial brightness."""
    light = _make_light(mock_hub_client, brightness=50, target=180)
    assert light.brightness == 180


def test_is_on_true(mock_hub_client) -> None:
    """Light should report on when brightness > 0."""
    light = _make_light(mock_hub_client, brightness=1)
    assert light.is_on is True


def test_is_on_false(mock_hub_client) -> None:
    """Light should report off when brightness == 0."""
    light = _make_light(mock_hub_client, brightness=0)
    assert light.is_on is False


def test_name_room_level(mock_hub_client) -> None:
    """Room-level light name should be the room title."""
    room = Room(id=1, title="Kitchen", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=None)
    assert light.name == "Kitchen"


def test_name_channel_level(mock_hub_client) -> None:
    """Channel-level light name should be the channel title."""
    ch = Channel(id=1, title="Dimmer", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    light = _make_light(mock_hub_client, channel=ch)
    assert light.name == "Dimmer"


def test_should_poll(mock_hub_client) -> None:
    """should_poll should be False (push-based)."""
    light = _make_light(mock_hub_client)
    assert light.should_poll is False


def test_unique_id_room_level(mock_hub_client) -> None:
    """Room-level light unique_id format."""
    room = Room(id=5, title="R", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=None)
    assert light.unique_id == f"{MOCK_HUB_ID}_5_0"


def test_unique_id_channel_level(mock_hub_client) -> None:
    """Channel-level light unique_id format."""
    room = Room(id=5, title="R", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=3, title="C", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    light = _make_light(mock_hub_client, room=room, channel=ch)
    assert light.unique_id == f"{MOCK_HUB_ID}_5_3"


def test_color_mode_brightness(mock_hub_client) -> None:
    """Default color mode should be BRIGHTNESS."""
    light = _make_light(mock_hub_client)
    assert light.color_mode == ColorMode.BRIGHTNESS
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}


# ---------------------------------------------------------------------------
# Turn on / off
# ---------------------------------------------------------------------------

async def test_turn_on_default_brightness(mock_hub_client) -> None:
    """Turn on with no brightness should default to 255."""
    ch = Channel(id=1, title="C", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    room = Room(id=1, title="R", type="LIGHT", mode="NORMAL", channels=[ch], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=ch, brightness=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(light, "async_write_ha_state", lambda: None)
        await light.async_turn_on()

    mock_hub_client.set_level.assert_awaited_once_with(1, 1, 255)
    assert light._brightness == 255


async def test_turn_on_with_brightness(mock_hub_client) -> None:
    """Turn on with explicit brightness kwarg."""
    ch = Channel(id=1, title="C", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    room = Room(id=1, title="R", type="LIGHT", mode="NORMAL", channels=[ch], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=ch, brightness=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(light, "async_write_ha_state", lambda: None)
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 100})

    mock_hub_client.set_level.assert_awaited_once_with(1, 1, 100)
    assert light._brightness == 100


async def test_turn_on_room_level(mock_hub_client) -> None:
    """Turn on room-level light uses channel_id 0."""
    room = Room(id=3, title="R", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=None, brightness=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(light, "async_write_ha_state", lambda: None)
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 200})

    mock_hub_client.set_level.assert_awaited_once_with(3, 0, 200)


async def test_turn_off_room_level(mock_hub_client) -> None:
    """Room-level turn off should call set_scene(room_id, 0, 0)."""
    room = Room(id=3, title="R", type="LIGHT", mode="NORMAL", channels=[], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=None, brightness=200)

    await light.async_turn_off()

    mock_hub_client.set_scene.assert_awaited_once_with(3, 0, 0)


async def test_turn_off_channel_level(mock_hub_client) -> None:
    """Channel-level turn off should call turn_on with brightness=0."""
    ch = Channel(id=2, title="C", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    room = Room(id=1, title="R", type="LIGHT", mode="NORMAL", channels=[ch], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=ch, brightness=200)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(light, "async_write_ha_state", lambda: None)
        await light.async_turn_off()

    mock_hub_client.set_level.assert_awaited_once_with(1, 2, 0)
    assert light._brightness == 0


async def test_turn_on_send_command_error(mock_hub_client) -> None:
    """SendCommandError during turn_on should be caught."""
    mock_hub_client.set_level = AsyncMock(side_effect=SendCommandError("fail"))
    ch = Channel(id=1, title="C", type="LIGHT", color_type=None, color_title=None, multi_channel_component=None)
    room = Room(id=1, title="R", type="LIGHT", mode="NORMAL", channels=[ch], scenes=[])
    light = _make_light(mock_hub_client, room=room, channel=ch, brightness=0)

    # Should not raise
    await light.async_turn_on()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def test_async_added_to_hass(mock_hub_client) -> None:
    """async_added_to_hass should register light with hub client."""
    light = _make_light(mock_hub_client)
    await light.async_added_to_hass()
    mock_hub_client.add_light.assert_awaited_once_with(light)


async def test_async_will_remove_from_hass(mock_hub_client) -> None:
    """async_will_remove_from_hass should deregister light from hub client."""
    light = _make_light(mock_hub_client)
    await light.async_will_remove_from_hass()
    mock_hub_client.remove_light.assert_awaited_once_with(light)
