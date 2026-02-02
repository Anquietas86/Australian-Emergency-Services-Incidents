from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform

from .const import (
    DOMAIN,
    SERVICE_REFRESH,
    CONF_UPDATE_INTERVAL,
    CONF_STATE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_STATE,
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
            if "incident_coordinator" in hass.data[DOMAIN][entry_id]:
                await hass.data[DOMAIN][entry_id]["incident_coordinator"].async_request_refresh()
            if "cap_coordinator" in hass.data[DOMAIN][entry_id]:
                await hass.data[DOMAIN][entry_id]["cap_coordinator"].async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Australian Emergency Services Incidents from a config entry."""
    update_seconds = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )
    state = entry.options.get(
        CONF_STATE,
        entry.data.get(CONF_STATE, DEFAULT_STATE),
    )

    # Create and store coordinators
    incident_coordinator = IncidentDataCoordinator(hass, state, update_seconds)
    cap_coordinator = CFSCAPDataCoordinator(hass, state, update_seconds)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "incident_coordinator": incident_coordinator,
        "cap_coordinator": cap_coordinator,
    }

    # Initial refresh
    await incident_coordinator.async_config_entry_first_refresh()
    await cap_coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload integration when options change
    async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Close aiohttp sessions to prevent resource leaks
        coordinators = hass.data[DOMAIN].get(entry.entry_id, {})
        if "incident_coordinator" in coordinators:
            await coordinators["incident_coordinator"].async_close()
        if "cap_coordinator" in coordinators:
            await coordinators["cap_coordinator"].async_close()
        hass.data[DOMAIN].pop(entry.entry_id)

    # If it's the last entry, remove the service
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)

    return unload_ok
