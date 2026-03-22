"""Shared fixtures for Rako integration tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant

from rakopy.model import (
    Channel,
    ChannelLevel,
    HubStatus,
    Level,
    LevelInfo,
    Room,
    Scene,
)

from custom_components.rako.const import DOMAIN


MOCK_HUB_ID = "AB1234"
MOCK_HUB_MAC = "00:11:22:33:44:55"
MOCK_HOST = "192.168.1.100"
MOCK_NAME = "My Rako"

MOCK_USER_INPUT = {
    CONF_NAME: MOCK_NAME,
    CONF_HOST: MOCK_HOST,
}


@pytest.fixture
def mock_hub_status() -> HubStatus:
    """Return a mock HubStatus."""
    return HubStatus(
        product_type="Bridge",
        protocol_version="2.0",
        id=MOCK_HUB_ID,
        mac_address=MOCK_HUB_MAC,
        version="1.0.0",
    )


@pytest.fixture
def mock_light_channel() -> Channel:
    """Return a mock light channel."""
    return Channel(
        id=1,
        title="Ceiling Light",
        type="LIGHT",
        color_type=None,
        color_title=None,
        multi_channel_component=None,
    )


@pytest.fixture
def mock_light_channel_2() -> Channel:
    """Return a second mock light channel."""
    return Channel(
        id=2,
        title="Wall Light",
        type="LIGHT",
        color_type=None,
        color_title=None,
        multi_channel_component=None,
    )


@pytest.fixture
def mock_blind_channel() -> Channel:
    """Return a mock blind channel."""
    return Channel(
        id=1,
        title="Blind",
        type="BLIND",
        color_type=None,
        color_title=None,
        multi_channel_component=None,
    )


@pytest.fixture
def mock_curtain_channel() -> Channel:
    """Return a mock curtain channel."""
    return Channel(
        id=2,
        title="Living Room Curtain",
        type="BLIND",
        color_type=None,
        color_title=None,
        multi_channel_component=None,
    )


@pytest.fixture
def mock_scenes() -> list[Scene]:
    """Return mock scenes."""
    return [
        Scene(id=0, title="Off"),
        Scene(id=1, title="Scene 1"),
        Scene(id=2, title="Scene 2"),
    ]


@pytest.fixture
def mock_light_room(mock_light_channel, mock_light_channel_2, mock_scenes) -> Room:
    """Return a mock light room."""
    return Room(
        id=1,
        title="Living Room",
        type="LIGHT",
        mode="NORMAL",
        channels=[mock_light_channel, mock_light_channel_2],
        scenes=mock_scenes,
    )


@pytest.fixture
def mock_blind_room(mock_blind_channel, mock_curtain_channel, mock_scenes) -> Room:
    """Return a mock blind room."""
    return Room(
        id=2,
        title="Bedroom",
        type="BLIND",
        mode="NORMAL",
        channels=[mock_blind_channel, mock_curtain_channel],
        scenes=mock_scenes,
    )


def make_channel_level(
    channel_id: int, current_level: int, target_level: int | None = None
) -> ChannelLevel:
    """Create a ChannelLevel with given values."""
    return ChannelLevel(
        channel_id=channel_id,
        current_level=current_level,
        target_level=target_level,
        level_info=LevelInfo(kelvin=0, red=0, green=0, blue=0),
    )


@pytest.fixture
def mock_light_levels() -> list[Level]:
    """Return mock levels for a light room."""
    return [
        Level(
            room_id=1,
            current_scene_id=1,
            channel_levels=[
                make_channel_level(0, 128),
                make_channel_level(1, 200),
                make_channel_level(2, 50),
            ],
        ),
    ]


@pytest.fixture
def mock_blind_levels() -> list[Level]:
    """Return mock levels for a blind room."""
    return [
        Level(
            room_id=2,
            current_scene_id=0,
            channel_levels=[
                make_channel_level(1, 255),
                make_channel_level(2, 0),
            ],
        ),
    ]


@pytest.fixture
def mock_all_levels(mock_light_levels, mock_blind_levels) -> list[Level]:
    """Return combined levels for all rooms."""
    return mock_light_levels + mock_blind_levels


@pytest.fixture
def mock_all_rooms(mock_light_room, mock_blind_room) -> list[Room]:
    """Return all rooms."""
    return [mock_light_room, mock_blind_room]


@pytest.fixture
def mock_hub_client(
    hass: HomeAssistant,
    mock_hub_status,
    mock_all_levels,
    mock_all_rooms,
) -> MagicMock:
    """Return a mocked HubClient."""
    client = MagicMock()
    client.hub_id = MOCK_HUB_ID
    client.entry_id = "test_entry_id"
    client.hass = hass

    client.get_hub_status = AsyncMock(return_value=mock_hub_status)
    client.get_levels = AsyncMock(return_value=mock_all_levels)
    client.get_rooms = AsyncMock(return_value=mock_all_rooms)
    client.set_level = AsyncMock()
    client.set_scene = AsyncMock()
    client.add_light = AsyncMock()
    client.remove_light = AsyncMock()
    client.add_cover = AsyncMock()
    client.remove_cover = AsyncMock()
    client.add_scene = AsyncMock()
    client.remove_scene = AsyncMock()
    client.get_events = MagicMock()

    return client


@pytest.fixture
def mock_config_entry(hass: HomeAssistant, mock_hub_client) -> MagicMock:
    """Return a mock config entry with runtime data set."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = MOCK_USER_INPUT
    entry.runtime_data = {
        "hub_id": MOCK_HUB_ID,
        "hub_client": mock_hub_client,
    }
    return entry
