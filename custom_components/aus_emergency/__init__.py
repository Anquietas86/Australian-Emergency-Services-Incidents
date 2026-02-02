from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .const import (
    DOMAIN,
    SERVICE_REFRESH,
    SERVICE_REMOVE_STATE,
    CONF_UPDATE_INTERVAL,
    CONF_STATE,
    CONF_STATES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_STATE,
    DEFAULT_STATES,
    STATE_DEVICE_INFO,
    SUPPORTED_STATES,
)
from .coordinator import IncidentDataCoordinator
from .cap_coordinator import CFSCAPDataCoordinator

PLATFORMS: list[str] = [Platform.GEO_LOCATION, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the component."""
    # This will make sure that the refresh service is available to all entries
    async def _handle_refresh(call: ServiceCall):
        """Handle the service call."""
        _LOGGER.info("Refreshing data from service call")
        for entry_id in hass.data.get(DOMAIN, {}):
            entry_data = hass.data[DOMAIN][entry_id]
            # Refresh all incident coordinators
            for coordinator in entry_data.get("incident_coordinators", {}).values():
                await coordinator.async_request_refresh()
            # Refresh all CAP coordinators
            for coordinator in entry_data.get("cap_coordinators", {}).values():
                await coordinator.async_request_refresh()

    async def _handle_remove_state(call: ServiceCall):
        """Handle the remove_state service call."""
        state_code = call.data.get("state")
        if not state_code:
            _LOGGER.error("No state specified for remove_state service")
            return

        state_code = state_code.upper()
        if state_code not in SUPPORTED_STATES:
            _LOGGER.error(
                "Invalid state '%s'. Supported states: %s",
                state_code,
                SUPPORTED_STATES,
            )
            return

        _LOGGER.info("Manual removal requested for state: %s", state_code)
        _remove_state_devices_global(hass, [state_code])

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_STATE,
        _handle_remove_state,
        schema=vol.Schema({vol.Required("state"): cv.string}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Australian Emergency Services Incidents from a config entry."""
    update_seconds = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )

    # Support both new multi-state and legacy single-state configs
    states = entry.options.get(CONF_STATES) or entry.data.get(CONF_STATES)
    if not states:
        # Migrate from old single-state config
        old_state = entry.options.get(CONF_STATE) or entry.data.get(CONF_STATE, DEFAULT_STATE)
        states = [old_state] if old_state else DEFAULT_STATES

    # Create coordinators for each selected state
    incident_coordinators = {}
    cap_coordinators = {}

    for state in states:
        incident_coordinators[state] = IncidentDataCoordinator(hass, state, update_seconds)
        cap_coordinators[state] = CFSCAPDataCoordinator(hass, state, update_seconds)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "incident_coordinators": incident_coordinators,
        "cap_coordinators": cap_coordinators,
        "states": states,
        # Keep legacy keys for backwards compatibility with sensors/geo_location
        "incident_coordinator": list(incident_coordinators.values())[0] if incident_coordinators else None,
        "cap_coordinator": list(cap_coordinators.values())[0] if cap_coordinators else None,
    }

    # Initial refresh for all coordinators
    for state in states:
        await incident_coordinators[state].async_config_entry_first_refresh()
        await cap_coordinators[state].async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload integration when options change
    async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Get the old states before reload
        old_states = set(states)

        # Get the new states from updated options
        new_states_list = entry.options.get(CONF_STATES) or entry.data.get(CONF_STATES)
        if not new_states_list:
            old_state = entry.options.get(CONF_STATE) or entry.data.get(CONF_STATE, DEFAULT_STATE)
            new_states_list = [old_state] if old_state else DEFAULT_STATES
        new_states = set(new_states_list)

        # Find states that were removed
        removed_states = list(old_states - new_states)
        if removed_states:
            _LOGGER.info("States removed from config: %s", removed_states)
            _remove_state_devices(hass, entry, removed_states)

        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Close aiohttp sessions to prevent resource leaks
        entry_data = hass.data[DOMAIN].get(entry.entry_id, {})

        # Close all incident coordinators
        incident_coordinators = entry_data.get("incident_coordinators", {})
        for coordinator in incident_coordinators.values():
            await coordinator.async_close()

        # Close all CAP coordinators
        cap_coordinators = entry_data.get("cap_coordinators", {})
        for coordinator in cap_coordinators.values():
            await coordinator.async_close()

        hass.data[DOMAIN].pop(entry.entry_id)

    # If it's the last entry, remove services and clean up domain data
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_STATE)
        hass.data.pop(DOMAIN)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of an entry - clean up devices and entities."""
    # Get the states that were configured for this entry
    states = entry.options.get(CONF_STATES) or entry.data.get(CONF_STATES)
    if not states:
        old_state = entry.options.get(CONF_STATE) or entry.data.get(CONF_STATE, DEFAULT_STATE)
        states = [old_state] if old_state else []

    if states:
        _LOGGER.info("Removing devices for states: %s", states)
        _remove_state_devices_global(hass, states)


def _remove_state_devices(
    hass: HomeAssistant, entry: ConfigEntry, states_to_remove: list[str]
) -> None:
    """Remove devices and entities for states that are no longer configured."""
    _remove_state_devices_global(hass, states_to_remove)


def _remove_state_devices_global(
    hass: HomeAssistant, states_to_remove: list[str]
) -> None:
    """Remove devices and entities for specified states (global version)."""
    if not states_to_remove:
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for state_code in states_to_remove:
        device_info = STATE_DEVICE_INFO.get(state_code)
        if not device_info:
            _LOGGER.warning("Unknown state code: %s", state_code)
            continue

        # Find the device by its identifiers
        identifiers = device_info.get("identifiers")
        if not identifiers:
            continue

        device = device_registry.async_get_device(identifiers=identifiers)
        if not device:
            _LOGGER.info("No device found for state %s (may already be removed)", state_code)
            continue

        # Remove all entities associated with this device
        entities_to_remove = er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
        removed_count = 0
        for entity_entry in entities_to_remove:
            _LOGGER.info(
                "Removing entity %s for state %s",
                entity_entry.entity_id,
                state_code,
            )
            entity_registry.async_remove(entity_entry.entity_id)
            removed_count += 1

        # Remove the device itself
        _LOGGER.info(
            "Removing device '%s' for state %s (removed %d entities)",
            device.name,
            state_code,
            removed_count,
        )
        device_registry.async_remove_device(device.id)
