"""Tests for cover.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.rako.cover import RakoCoverEntity, async_setup_entry
from rakopy.errors import SendCommandError
from rakopy.model import Channel, Room

from tests.conftest import MOCK_HUB_ID, make_channel_level


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cover(
    hub_client,
    room: Room | None = None,
    channel: Channel | None = None,
    current_level: int = 128,
    target_level: int | None = None,
) -> RakoCoverEntity:
    """Convenience factory for RakoCoverEntity."""
    if room is None:
        room = Room(id=2, title="Bedroom", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    if channel is None:
        channel = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cl = make_channel_level(channel.id, current_level, target_level)
    return RakoCoverEntity(hub_client=hub_client, room=room, channel=channel, channel_level=cl)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_setup_entry_discovers_covers(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """async_setup_entry should discover blind rooms and add cover entities."""
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    covers = [e for e in added if isinstance(e, RakoCoverEntity)]
    # mock_blind_room has 2 channels
    assert len(covers) == 2


async def test_setup_entry_discovers_curtain_rooms(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """Rooms reported as type 'CURTAIN' should also produce cover entities.

    Regression test for issue #23: some hubs report curtain rooms with
    room.type == "CURTAIN" rather than "BLIND", which previously caused the
    cover entity to disappear.
    """
    from rakopy.model import Channel, Level, Room

    from tests.conftest import make_channel_level

    curtain_channel = Channel(
        id=1,
        title="Curtain",
        type="BLIND",
        color_type=None,
        color_title=None,
        multi_channel_component=None,
    )
    curtain_room = Room(
        id=13,
        title="Curtain",
        type="CURTAIN",
        mode="NORMAL",
        channels=[curtain_channel],
        scenes=[],
    )
    curtain_level = Level(
        room_id=13,
        current_scene_id=0,
        channel_levels=[make_channel_level(1, 0)],
    )
    mock_hub_client.get_rooms = AsyncMock(return_value=[curtain_room])
    mock_hub_client.get_levels = AsyncMock(return_value=[curtain_level])

    added: list = []
    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    covers = [e for e in added if isinstance(e, RakoCoverEntity)]
    assert len(covers) == 1
    assert covers[0]._room.type == "CURTAIN"


async def test_setup_entry_skips_light_rooms(
    hass: HomeAssistant,
    mock_hub_client,
    mock_config_entry,
) -> None:
    """Light rooms should not produce cover entities."""
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    for entity in added:
        assert isinstance(entity, RakoCoverEntity)
        assert entity._room.type == "BLIND"


async def test_setup_entry_warns_missing_levels(
    hass: HomeAssistant,
    mock_config_entry,
    mock_hub_client,
) -> None:
    """Rooms with no matching levels should not crash."""
    mock_hub_client.get_levels = AsyncMock(return_value=[])
    added: list = []

    await async_setup_entry(hass, mock_config_entry, lambda entities, _: added.extend(entities))

    assert len(added) == 0


# ---------------------------------------------------------------------------
# Position conversions
# ---------------------------------------------------------------------------

def test_rako_to_ha_position_zero() -> None:
    """Rako 0 → HA 0."""
    assert RakoCoverEntity._rako_to_ha_position(0) == 0


def test_rako_to_ha_position_max() -> None:
    """Rako 255 → HA 100."""
    assert RakoCoverEntity._rako_to_ha_position(255) == 100


def test_rako_to_ha_position_mid() -> None:
    """Rako 127 → HA ~49."""
    result = RakoCoverEntity._rako_to_ha_position(127)
    assert 49 <= result <= 50


def test_ha_to_rako_position_zero() -> None:
    """HA 0 → Rako 0."""
    assert RakoCoverEntity._ha_to_rako_position(0) == 0


def test_ha_to_rako_position_max() -> None:
    """HA 100 → Rako 255."""
    assert RakoCoverEntity._ha_to_rako_position(100) == 255


def test_ha_to_rako_position_mid() -> None:
    """HA 50 → Rako 127."""
    result = RakoCoverEntity._ha_to_rako_position(50)
    assert 127 <= result <= 128


# ---------------------------------------------------------------------------
# Device class detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "channel_title,expected_class",
    [
        ("Blind", CoverDeviceClass.BLIND),
        ("Living Room Curtain", CoverDeviceClass.CURTAIN),
        ("Front Drape", CoverDeviceClass.CURTAIN),
        ("Roller Shutter", CoverDeviceClass.SHUTTER),
        ("Patio Awning", CoverDeviceClass.AWNING),
        ("Window Shade", CoverDeviceClass.SHADE),
        ("Something Else", CoverDeviceClass.BLIND),
    ],
)
def test_determine_device_class(mock_hub_client, channel_title, expected_class) -> None:
    """Device class should be inferred from channel name keywords."""
    ch = Channel(id=1, title=channel_title, type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, channel=ch)
    assert cover._attr_device_class == expected_class


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def test_current_cover_position(mock_hub_client) -> None:
    """Position should reflect the initial channel level."""
    cover = _make_cover(mock_hub_client, current_level=255)
    assert cover.current_cover_position == 100


def test_current_cover_position_target(mock_hub_client) -> None:
    """When target_level is set, position uses target."""
    cover = _make_cover(mock_hub_client, current_level=0, target_level=255)
    assert cover.current_cover_position == 100


def test_is_closed(mock_hub_client) -> None:
    """Cover should report closed when position == 0."""
    cover = _make_cover(mock_hub_client, current_level=0)
    assert cover.is_closed is True
    assert cover.is_open is False


def test_is_open(mock_hub_client) -> None:
    """Cover should report open when position == 100."""
    cover = _make_cover(mock_hub_client, current_level=255)
    assert cover.is_open is True
    assert cover.is_closed is False


def test_name(mock_hub_client) -> None:
    """Name should be '{room.title} {channel.title}'."""
    room = Room(id=2, title="Bedroom", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch)
    assert cover.name == "Bedroom Blind"


def test_should_poll(mock_hub_client) -> None:
    """should_poll should be False."""
    cover = _make_cover(mock_hub_client)
    assert cover.should_poll is False


def test_unique_id(mock_hub_client) -> None:
    """Unique ID format should be hub_id_room_id_channel_id."""
    room = Room(id=2, title="R", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=3, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch)
    assert cover.unique_id == f"{MOCK_HUB_ID}_2_3"


def test_supported_features(mock_hub_client) -> None:
    """Cover should support open, close, stop, and set_position."""
    cover = _make_cover(mock_hub_client)
    expected = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    assert cover._attr_supported_features == expected


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def test_open_cover(mock_hub_client) -> None:
    """Opening cover should call set_scene with scene 1."""
    room = Room(id=2, title="R", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch, current_level=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cover, "async_write_ha_state", lambda: None)
        await cover.async_open_cover()

    mock_hub_client.set_scene.assert_awaited_once_with(2, 1, 1)
    assert cover.current_cover_position == 100


async def test_close_cover(mock_hub_client) -> None:
    """Closing cover should call set_scene with scene 0."""
    room = Room(id=2, title="R", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch, current_level=255)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cover, "async_write_ha_state", lambda: None)
        await cover.async_close_cover()

    mock_hub_client.set_scene.assert_awaited_once_with(2, 1, 0)
    assert cover.current_cover_position == 0


async def test_stop_cover(mock_hub_client) -> None:
    """Stopping cover should call set_scene with scene 3."""
    room = Room(id=2, title="R", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch)

    await cover.async_stop_cover()

    mock_hub_client.set_scene.assert_awaited_once_with(2, 1, 3)


async def test_set_cover_position(mock_hub_client) -> None:
    """set_cover_position should convert HA→Rako and call set_level."""
    room = Room(id=2, title="R", type="BLIND", mode="NORMAL", channels=[], scenes=[])
    ch = Channel(id=1, title="Blind", type="BLIND", color_type=None, color_title=None, multi_channel_component=None)
    cover = _make_cover(mock_hub_client, room=room, channel=ch, current_level=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cover, "async_write_ha_state", lambda: None)
        await cover.async_set_cover_position(**{ATTR_POSITION: 50})

    mock_hub_client.set_level.assert_awaited_once_with(2, 1, 127)
    assert cover.current_cover_position == 50


async def test_set_cover_position_none(mock_hub_client) -> None:
    """set_cover_position with no position kwarg should be a no-op."""
    cover = _make_cover(mock_hub_client)

    await cover.async_set_cover_position()

    mock_hub_client.set_level.assert_not_awaited()


async def test_open_cover_send_command_error(mock_hub_client) -> None:
    """SendCommandError during open_cover should be caught."""
    mock_hub_client.set_scene = AsyncMock(side_effect=SendCommandError("fail"))
    cover = _make_cover(mock_hub_client)

    # Should not raise
    await cover.async_open_cover()


async def test_close_cover_send_command_error(mock_hub_client) -> None:
    """SendCommandError during close_cover should be caught."""
    mock_hub_client.set_scene = AsyncMock(side_effect=SendCommandError("fail"))
    cover = _make_cover(mock_hub_client)

    # Should not raise
    await cover.async_close_cover()


async def test_stop_cover_send_command_error(mock_hub_client) -> None:
    """SendCommandError during stop_cover should be caught."""
    mock_hub_client.set_scene = AsyncMock(side_effect=SendCommandError("fail"))
    cover = _make_cover(mock_hub_client)

    # Should not raise
    await cover.async_stop_cover()


async def test_set_position_send_command_error(mock_hub_client) -> None:
    """SendCommandError during set_cover_position should be caught."""
    mock_hub_client.set_level = AsyncMock(side_effect=SendCommandError("fail"))
    cover = _make_cover(mock_hub_client)

    # Should not raise
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def test_async_added_to_hass(mock_hub_client) -> None:
    """async_added_to_hass should register cover with hub client."""
    cover = _make_cover(mock_hub_client)
    await cover.async_added_to_hass()
    mock_hub_client.add_cover.assert_awaited_once_with(cover)


async def test_async_will_remove_from_hass(mock_hub_client) -> None:
    """async_will_remove_from_hass should deregister cover from hub client."""
    cover = _make_cover(mock_hub_client)
    await cover.async_will_remove_from_hass()
    mock_hub_client.remove_cover.assert_awaited_once_with(cover)
