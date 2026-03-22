"""Tests for config_flow.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import (
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.rako.config_flow import ConfigFlow
from custom_components.rako.const import DOMAIN
from tests.conftest import MOCK_HUB_ID, MOCK_USER_INPUT


@pytest.fixture
def mock_hub():
    """Patch rakopy Hub used in config_flow.validate_input."""
    with patch("custom_components.rako.config_flow.Hub") as mock_cls:
        hub_instance = MagicMock()
        hub_status = MagicMock()
        hub_status.id = MOCK_HUB_ID
        hub_instance.get_hub_status = AsyncMock(return_value=hub_status)
        mock_cls.return_value = hub_instance
        yield mock_cls


@pytest.fixture(autouse=True)
async def _register_rako_integration(hass: HomeAssistant):
    """Register a mock Rako integration with HA so config flow resolves."""
    mock_integration(
        hass,
        MockModule(
            DOMAIN,
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
        built_in=False,
    )
    mock_platform(hass, f"{DOMAIN}.config_flow")

    with patch.dict(config_entries.HANDLERS, {DOMAIN: ConfigFlow}):
        yield


async def test_step_user_shows_form(hass: HomeAssistant) -> None:
    """Test that the user step shows a form when no input is given."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_step_user_creates_entry(hass: HomeAssistant, mock_hub) -> None:
    """Test successful config flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USER_INPUT,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Rako Hub"
    assert result["data"] == MOCK_USER_INPUT


async def test_step_user_cannot_connect(hass: HomeAssistant) -> None:
    """Test form shows error when connection fails."""
    with patch(
        "custom_components.rako.config_flow.Hub"
    ) as mock_cls:
        hub_instance = MagicMock()
        hub_instance.get_hub_status = AsyncMock(side_effect=Exception("timeout"))
        mock_cls.return_value = hub_instance

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            MOCK_USER_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_step_user_duplicate_unique_id(hass: HomeAssistant, mock_hub) -> None:
    """Test that duplicate hub IDs are properly aborted."""
    # Create first entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USER_INPUT,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Try to add the same hub again — should abort
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        MOCK_USER_INPUT,
    )
    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
