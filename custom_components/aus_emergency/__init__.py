
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform

from .const import DOMAIN, SERVICE_REFRESH

PLATFORMS: list[str] = [Platform.GEO_LOCATION, Platform.SENSOR]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("refresh_cbs", [])
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_refresh(call: ServiceCall):
        for cb in hass.data.get(DOMAIN, {}).get("refresh_cbs", []):
            try:
                await cb()
            except Exception:
                pass

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok
