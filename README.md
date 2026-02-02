# Australian Emergency Services Incidents

**Version:** v0.1.23

This Home Assistant custom integration pulls live emergency incidents for **South Australia** from multiple sources and exposes them as:
- **Geolocation** entities (map-friendly coordinates)
- An always-present **Active incidents** sensor (count + rich attributes)
- **Lifecycle events** for automations

### Data Sources
- **CFS/SES incidents** via CRIIMSON feed
- **CAP alerts** (Common Alerting Protocol) from SA Emergency Services

### Key Features
- Lifecycle **events**: `aus_emergency_incident_created|updated|removed` for automation triggers
- Incident tracking with `first_seen`, `last_seen`, `last_changed` timestamps
- Normalized **severity levels**: `info | advice | watch_and_act | emergency_warning | all_clear`
- Rich notification-ready attributes: `title`, `summary`, `map_url`, `google_maps_url`
- **Active incidents** sensor with counts breakdown by severity
- **Manual refresh service**: `aus_emergency.refresh` to force immediate data updates
- Configurable update intervals

## Installation
1. Clone/download `custom_components/aus_emergency/` to your Home Assistant config directory.
2. Restart Home Assistant.
3. Navigate to **Settings → Devices & Services → Create Integration** → search for *Australian Emergency Services Incidents*.
4. Select your state (currently **South Australia (SA)**) and configure your preferences.

### Configuration Options
- **State**: Select the emergency service region (currently SA only)
- **Update Interval**: How frequently to poll for new incidents (default: 10 minutes)
- **Remove Stale Incidents**: Automatically remove incidents that are no longer active

## Entities Created

## Entities Created

### Geolocation Entities
- One entity per active incident with:
  - Coordinates for map display
  - Incident type and status
  - Location and region information
  - Agency and resource details

### Sensor Entities
- **Active Incidents** (`sensor.active_incidents`): 
  - Total count of active incidents
  - Count breakdown by severity level
  - List of all incident summaries in attributes

## Events for Automations
Listen/trigger on:
- `aus_emergency_incident_created`
- `aus_emergency_incident_updated`
- `aus_emergency_incident_removed`

## Events for Automations

Create powerful automations by listening to incident lifecycle events:

### Available Events
- `aus_emergency_incident_created` — New incident detected
- `aus_emergency_incident_updated` — Incident status or details changed
- `aus_emergency_incident_removed` — Incident resolved/cleared

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
