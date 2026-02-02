"""Diagnostics support for Australian Emergency Services Incidents."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_STATE,
    CONF_UPDATE_INTERVAL,
    CONF_REMOVE_STALE,
    CONF_ZONES,
    ATTR_SEVERITY,
    HIGH_SEVERITY_LEVELS,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinators = hass.data[DOMAIN].get(entry.entry_id, {})
    incident_coordinator = coordinators.get("incident_coordinator")
    cap_coordinator = coordinators.get("cap_coordinator")

    # Get incident data
    incident_data = incident_coordinator.data if incident_coordinator else {}
    incidents = incident_data.get("incidents", [])

    # Get CAP data
    cap_data = cap_coordinator.data if cap_coordinator else {}
    cap_alerts = cap_data.get("alerts", [])

    # Calculate severity breakdown
    severity_counts = {
        "info": 0,
        "advice": 0,
        "watch_and_act": 0,
        "emergency_warning": 0,
        "all_clear": 0,
    }
    for inc in incidents:
        sev = inc.get(ATTR_SEVERITY, "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    high_severity_count = sum(
        1 for inc in incidents
        if inc.get(ATTR_SEVERITY) in HIGH_SEVERITY_LEVELS
    )

    # Coordinator status
    incident_coordinator_status = {
        "name": incident_coordinator.name if incident_coordinator else "N/A",
        "last_update_success": (
            incident_coordinator.last_update_success
            if incident_coordinator else None
        ),
        "last_update_success_time": (
            incident_coordinator.last_update_success_time.isoformat()
            if incident_coordinator and incident_coordinator.last_update_success_time
            else None
        ),
        "update_interval_seconds": (
            incident_coordinator.update_interval.total_seconds()
            if incident_coordinator and incident_coordinator.update_interval
            else None
        ),
        "consecutive_failures": (
            incident_coordinator._consecutive_failures
            if incident_coordinator else 0
        ),
    }

    cap_coordinator_status = {
        "name": cap_coordinator.name if cap_coordinator else "N/A",
        "last_update_success": (
            cap_coordinator.last_update_success
            if cap_coordinator else None
        ),
        "last_update_success_time": (
            cap_coordinator.last_update_success_time.isoformat()
            if cap_coordinator and cap_coordinator.last_update_success_time
            else None
        ),
        "update_interval_seconds": (
            cap_coordinator.update_interval.total_seconds()
            if cap_coordinator and cap_coordinator.update_interval
            else None
        ),
        "consecutive_failures": (
            cap_coordinator._consecutive_failures
            if cap_coordinator else 0
        ),
        "has_cap_feed": (
            cap_coordinator.cap_url is not None
            if cap_coordinator else False
        ),
    }

    # Configuration
    config = {
        "state": entry.options.get(CONF_STATE, entry.data.get(CONF_STATE)),
        "update_interval": entry.options.get(
            CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL)
        ),
        "remove_stale": entry.options.get(
            CONF_REMOVE_STALE, entry.data.get(CONF_REMOVE_STALE)
        ),
        "monitored_zones": entry.options.get(
            CONF_ZONES, entry.data.get(CONF_ZONES, [])
        ),
    }

    return {
        "generated_at": dt_now().isoformat(),
        "config": config,
        "incident_coordinator": incident_coordinator_status,
        "cap_coordinator": cap_coordinator_status,
        "summary": {
            "total_incidents": len(incidents),
            "high_severity_incidents": high_severity_count,
            "total_cap_alerts": len(cap_alerts),
            "severity_breakdown": severity_counts,
        },
        "incidents": [
            {
                "incident_no": inc.get("incident_no"),
                "type": inc.get("type"),
                "severity": inc.get("severity"),
                "status": inc.get("status"),
                "region": inc.get("region"),
                "location_name": inc.get("location_name"),
                "has_coordinates": (
                    inc.get("latitude") is not None and
                    inc.get("longitude") is not None
                ),
            }
            for inc in incidents
        ],
        "cap_alerts": [
            {
                "id": alert.get("id"),
                "event": alert.get("event"),
                "severity": alert.get("severity"),
                "headline": alert.get("headline"),
                "area_count": len(alert.get("areas", [])),
            }
            for alert in cap_alerts
        ],
    }
