from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform

from .const import DOMAIN, SERVICE_REFRESH, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .coordinator import CFSDataCoordinator
from .cap_coordinator import CFSCAPDataCoordinator

PLATFORMS: list[str] = [Platform.GEO_LOCATION, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Australian Emergency Services Incidents from a config entry."""
    update_seconds = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )

    # Create and store coordinators
    cfs_coordinator = CFSDataCoordinator(hass, update_seconds)
    cap_coordinator = CFSCAPDataCoordinator(hass, update_seconds)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "cfs_coordinator": cfs_coordinator,
        "cap_coordinator": cap_coordinator,
    }

    # Initial refresh
    await cfs_coordinator.async_config_entry_first_refresh()
    await cap_coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_refresh(call: ServiceCall):
        _LOGGER.debug("Refreshing data from service call")
        await cfs_coordinator.async_request_refresh()
        await cap_coordinator.async_request_refresh()

    # This part is for the global refresh service, not entry-specific
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        _LOGGER.debug("Registering refresh service")
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    # If it's the last entry, remove the service
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)

    return unload_ok
