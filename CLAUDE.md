# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
# Setup (one-time)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements_dev.txt
pip install -e .

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_light.py -v

# Run a single test
python -m pytest tests/test_light.py::test_turn_on_default_brightness -v
```

Pytest is configured with `asyncio_mode = auto` in `setup.cfg`, so async test functions work without the `@pytest.mark.asyncio` decorator.

## Architecture

This is a Home Assistant custom component (`custom_components/rako/`) for controlling Rako lighting hubs over the local network. `iot_class` is `local_push` — entities are updated via a persistent event stream from the hub, not polling.

### Core data flow

1. **Config flow** (`config_flow.py`) — User provides hub host/name → `rakopy.Hub.get_hub_status()` validates the connection → config entry is created.
2. **Entry setup** (`__init__.py`) — Creates a `HubClient`, fetches hub status, registers the device, stores `RakoDomainEntryData` (`hub_id` + `hub_client`) in `entry.runtime_data`, then forwards setup to three platforms: `light`, `select`, `cover`.
3. **Platform setup** (`light.py`, `cover.py`, `select.py`) — Each platform reads `entry.runtime_data` to get the `HubClient`, calls `get_rooms()` + `get_levels()`, and creates entities for matching room types (`LIGHT`, `BLIND`, or all rooms for scenes).
4. **Event listener** (`hub_client.py: subscribe_to_events`) — A single background task listens to `hub_client.get_events()`. `LevelChangedEvent` updates lights/covers; `SceneChangedEvent` updates scene selects. The task starts when the first entity registers and stops when the last deregisters (reference-counted via `_try_start_event_listener_task` / `_try_cancel_event_listener_task`).

### Key types

- `HubClient` (extends `rakopy.hub.Hub`) — Central hub communication client. Maintains maps of registered entities (`_light_map`, `_cover_map`, `_scene_map`) keyed by `unique_id`.
- `RakoDomainEntryData` (TypedDict) — Stored on `entry.runtime_data` with keys `hub_id` and `hub_client`.
- Room types: `"LIGHT"` rooms produce light entities, `"BLIND"` rooms produce cover entities. All rooms produce scene select entities.

### External dependency

All hub communication uses the [`rakopy`](https://pypi.org/project/rakopy/) library (pinned to v0.0.5 in `manifest.json` and `requirements_dev.txt`). Key classes: `Hub`, `Room`, `Channel`, `ChannelLevel`, `Level`, `HubStatus`, `LevelChangedEvent`, `SceneChangedEvent`, `SendCommandError`.

## Conventions

### Entity unique IDs
- Lights/Covers: `{hub_id}_{room_id}_{channel_id}` (room-level lights use `channel_id` `0`)
- Scenes: `{hub_id}_{room_id}`

### Light channel model
Each `LIGHT` room produces one room-level light (channel `0`, which calls `set_scene(room_id, 0, brightness)` to turn off) plus one light per channel. Color is not yet supported — channels with a `color_type` (e.g. RGB) are treated as brightness-only (`ColorMode.BRIGHTNESS`).

### Cover position conversion
Rako uses 0–255 levels; Home Assistant uses 0–100 positions. `_rako_to_ha_position` and `_ha_to_rako_position` static methods on the cover entity handle conversion.

### Entities use push updates
All entities set `should_poll = False`. State changes come from the event listener, not polling. The brightness/position setters on entities call `async_write_ha_state()` to push updates to HA.

### Testing patterns
- All hub network calls (`get_hub_status`, `get_levels`, `get_rooms`, `set_level`, `set_scene`, `get_events`) must be mocked — no real network access in tests.
- Use `pytest-homeassistant-custom-component` for HA test fixtures (provides `hass` fixture).
- Shared fixtures and factory helpers live in `tests/conftest.py`.
- Entity methods that call `async_write_ha_state()` need the entity added to hass first, or the method monkeypatched in unit tests.
- For config flow tests, register the integration with `mock_integration` + `mock_platform` from the HA test utils and patch `config_entries.HANDLERS`.
