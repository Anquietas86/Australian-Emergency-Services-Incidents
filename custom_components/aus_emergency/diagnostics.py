"""Diagnostics support for Australian Emergency Services Incidents."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL,
    CONF_REMOVE_STALE,
    CONF_ZONES,
    CONF_STATES,
    ATTR_SEVERITY,
    HIGH_SEVERITY_LEVELS,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
    incident_coordinators = entry_data.get("incident_coordinators", {})
    cap_coordinators = entry_data.get("cap_coordinators", {})

    # Aggregate data across all states
    all_incidents = []
    all_cap_alerts = []
    coordinator_statuses = []
    cap_statuses = []

    for state, coordinator in incident_coordinators.items():
        data = coordinator.data or {}
        incidents = data.get("incidents", [])
        all_incidents.extend(incidents)
        coordinator_statuses.append({
            "state": state,
            "name": coordinator.name,
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "consecutive_failures": coordinator._consecutive_failures,
            "incident_count": len(incidents),
        })

    for state, coordinator in cap_coordinators.items():
        data = coordinator.data or {}
        alerts = data.get("alerts", [])
        all_cap_alerts.extend(alerts)
        cap_statuses.append({
            "state": state,
            "name": coordinator.name,
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "consecutive_failures": coordinator._consecutive_failures,
            "has_cap_feed": coordinator.cap_url is not None,
            "alert_count": len(alerts),
        })

    # Calculate severity breakdown
    severity_counts = {
        "info": 0,
        "advice": 0,
        "watch_and_act": 0,
        "emergency_warning": 0,
        "all_clear": 0,
    }
    for inc in all_incidents:
        sev = inc.get(ATTR_SEVERITY, "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    high_severity_count = sum(
        1 for inc in all_incidents
        if inc.get(ATTR_SEVERITY) in HIGH_SEVERITY_LEVELS
    )

    # Configuration
    config = {
        "states": entry.options.get(CONF_STATES, entry.data.get(CONF_STATES)),
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
        "incident_coordinators": coordinator_statuses,
        "cap_coordinators": cap_statuses,
        "summary": {
            "total_incidents": len(all_incidents),
            "high_severity_incidents": high_severity_count,
            "total_cap_alerts": len(all_cap_alerts),
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
            for inc in all_incidents
        ],
        "cap_alerts": [
            {
                "id": alert.get("id"),
                "event": alert.get("event"),
                "severity": alert.get("severity"),
                "headline": alert.get("headline"),
                "area_count": len(alert.get("areas", [])),
            }
            for alert in all_cap_alerts
        ],
    }
