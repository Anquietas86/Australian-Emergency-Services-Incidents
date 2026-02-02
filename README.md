# Australian Emergency Services Incidents

**Version:** v0.2.0

This Home Assistant custom integration pulls live emergency incidents from **Australian Emergency Services** and exposes them as:
- **Geolocation** entities (map-friendly coordinates)
- **Sensor** entities for incident counts and summaries
- **Lifecycle events** for automations

### Supported Regions
- **South Australia (SA)**: CFS/SES via CRIIMSON feed + CAP alerts
- **New South Wales (NSW)**: RFS major incidents
- **Victoria (VIC)**: Emergency Management Victoria incidents
- **Queensland (QLD)**: Queensland Fire and Emergency Services bushfire alerts

### Key Features
- **Multi-state support**: Monitor incidents across SA, NSW, VIC, and QLD
- **Lifecycle events** (`incident_created|updated|removed`) for automation triggers
- **CAP alert events** (SA) for emergency warnings
- **Incident tracking**: `first_seen`, `last_seen`, `last_changed` timestamps with duration tracking
- **Normalized severity levels**: `info | advice | watch_and_act | emergency_warning | all_clear`
- **Multiple sensor types**:
  - Active incidents (total count + breakdown by severity)
  - Incident summary (detailed list with current status)
  - High severity incidents (emergency warnings and watch & act only)
- **Zone monitoring**: Optionally track incidents within selected Home Assistant zones
- **Exponential backoff retry**: Resilient data source polling with automatic backoff on failures
- **Manual refresh service**: `aus_emergency.refresh` to force immediate data updates
- **Configurable update intervals** (default: 10 minutes)

## Installation
1. Clone/download `custom_components/aus_emergency/` to your Home Assistant `config/` directory.
2. Restart Home Assistant.
3. Navigate to **Settings → Devices & Services → Create Integration** → search for *Australian Emergency Services Incidents*.
4. Select your state (SA, NSW, VIC, or QLD) and configure your preferences.

### Configuration Options
- **State**: Select the emergency service region:
  - **SA** (South Australia) — CFS/SES via CRIIMSON + CAP alerts
  - **NSW** (New South Wales) — RFS major incidents
  - **VIC** (Victoria) — Emergency Management Victoria
  - **QLD** (Queensland) — Queensland Fire and Emergency Services
- **Update Interval**: How frequently to poll for new incidents (default: 10 minutes)
- **Remove Stale Incidents**: Automatically remove incidents no longer in the active feed
- **Zone Monitoring**: Optional — select Home Assistant zones to monitor incidents within them

## Entities Created

### Geolocation Entities
- One entity per active incident with:
  - Coordinates for map display
  - Incident type, status, and severity
  - Location and region information
  - Agency, resource details, and duration
  - Zone membership (if within monitored zones)

### Sensor Entities
- **Active Incidents** (`sensor.active_incidents`): 
  - Total count of active incidents
  - Count breakdown by severity level
  - List of all incident summaries in attributes
  
- **Incident Summary** (`sensor.incident_summary`):
  - Detailed list of all incidents with current status
  - Rich attributes: incident number, type, severity, location, duration
  - Useful for dashboards and detailed automation logic

- **High Severity Incidents** (`sensor.*_high_severity_incidents`):
  - Count of `emergency_warning` and `watch_and_act` incidents only
  - For critical notification triggers


## Events for Automations

Create powerful automations by listening to incident lifecycle events. Each state (SA, NSW, VIC, QLD) fires the same core events plus state-specific CAP alerts (SA only).

### Incident Lifecycle Events
- `aus_emergency_incident_created` — New incident detected
- `aus_emergency_incident_updated` — Incident status or details changed
- `aus_emergency_incident_removed` — Incident resolved/cleared

### CAP Alert Events (SA only)
- `aus_emergency_cap_alert_created` — New CAP alert issued
- `aus_emergency_cap_alert_updated` — CAP alert details updated
- `aus_emergency_cap_alert_removed` — CAP alert expires/clears

### Event Payload

Each event includes:
```json
{
  "incident_no": "12345",
  "agency": "CFS",
  "type": "Grass Fire",
  "severity": "emergency_warning",
  "title": "Large Grass Fire - South Road",
  "summary": "Active grassfire with resources deployed",
  "location_name": "Adelaide Hills",
  "region": "Hills & Mid Murray",
  "latitude": -34.7282,
  "longitude": 139.0808,
  "duration_minutes": 45,
  "first_seen": "2024-02-02T10:15:00+10:00",
  "last_seen": "2024-02-02T11:00:00+10:00",
  "last_changed": "2024-02-02T10:45:00+10:00",
  "in_zone": ["backyard", "neighborhood"],
  "map_url": "https://...",
  "google_maps_url": "https://maps.google.com/?q=..."
}
```

### Example Automation
```yaml
automation:
  - alias: "Alert on Emergency Warning"
    trigger:
      platform: event
      event_type: aus_emergency_incident_created
      event_data:
        severity: emergency_warning
    action:
      - service: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.title }}"
          message: "{{ trigger.event.data.summary }}"
```

## Service

### `aus_emergency.refresh`
Forces an immediate refresh of all incident data from both CRIIMSON and CAP feeds without waiting for the next scheduled update.

**Usage in automation:**
```yaml
service: aus_emergency.refresh
```

## Requirements
- **Home Assistant** 2023.12+
- **aiohttp** - for async HTTP requests
- **defusedxml** - for secure XML parsing

## Technical Details

- **Domain**: `aus_emergency`
- **Class**: Cloud Polling
- **Update Interval**: Configurable (default 600 seconds)
- **Code Owners**: @Anquietas86
- **Documentation**: [GitHub Repository](https://github.com/Anquietas86/Australian-Emergency-Services-Incidents)

## Current Status

✅ **Stable** — Production-ready for South Australia
- Full CFS/SES incident monitoring via CRIIMSON feed
- CAP alert integration for comprehensive coverage
- Robust error handling and resource management
- Comprehensive event system for automations

## Future Plans

- Expand support to additional Australian states
- Additional data sources as available
