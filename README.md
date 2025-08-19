# Australian Emergency Services Incidents

**Version:** v0.1.21

This Home Assistant custom integration pulls live emergency incidents for **South Australia (CFS/SES)** and exposes them as:
- **Geolocation** entities (map-friendly)
- An always-present **Active incidents** sensor (count + rich attributes)

### New in v0.1.21
- Lifecycle **events**: `aus_emergency_incident_created|updated|removed`
- `first_seen`, `last_seen`, `last_changed` timestamps
- Normalized **severity**: `info | advice | watch_and_act | emergency_warning | all_clear`
- Notification-ready attributes: `title`, `summary`, `map_url`, `google_maps_url`
- **Active incidents** sensor now includes **counts by severity**
- Service: `aus_emergency.refresh` to force an immediate update

## Installation
1. Copy `custom_components/aus_emergency/` to your HA config.
2. Restart Home Assistant.
3. Add integration via **Settings → Devices & services → Add Integration** → *Australian Emergency Services Incidents*.
4. Pick your state (currently **SA**) and options.

## Events for automations
Listen/trigger on:
- `aus_emergency_incident_created`
- `aus_emergency_incident_updated`
- `aus_emergency_incident_removed`

Event payload includes incident details incl. `title`, `summary`, `severity`, and coordinates.

## Service
- `aus_emergency.refresh` — forces a refresh of all feeds and entities.

## Notes
- Internal domain remains `aus_emergency`. Project name anticipates adding other states later.
