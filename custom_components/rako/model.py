"""Rako integration shared models."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .hub_client import HubClient


class RakoDomainEntryData(TypedDict):
    """A single Rako config entry's data."""

    hub_id: str
    hub_client: HubClient
