# Australian Emergency Services Incidents

**Version:** v0.4.0

This Home Assistant custom integration pulls live emergency incidents from **Australian Emergency Services** and exposes them as:
- **Geolocation** entities (map-friendly coordinates)
- **Sensor** entities for incident counts and summaries
- **Lifecycle events** for automations

### Supported Regions
- **South Australia (SA)**: CFS/SES via CRIIMSON feed + CAP alerts. If CRIIMSON fails, incidents fall back to the public CFS map's CFS/MFS incident layer (CAP warnings are **not** provided by this fallback).
- **New South Wales (NSW)**: RFS major incidents (GeoJSON)
- **Victoria (VIC)**: Emergency Management Victoria incidents (JSON)
- **Queensland (QLD)**: Queensland Fire and Emergency Services bushfire alerts (GeoJSON)
- **Tasmania (TAS)**: TFS retired its machine-readable RSS/KML feeds in 2026. TAS is no longer offered for new selections; existing configurations keep it, but its sensors report **unavailable**, not a misleading zero, until a verified replacement feed is found.
- **Western Australia (WA)**: DFES EmergencyWA API (JSON + warnings feed)
- **Australian Capital Territory (ACT)**: ESA current incidents (GeoRSS). This feed has no warning levels, so ACT incidents are severity `info`, and it includes ambulance responses.
- **Victoria (VIC)** also pulls public warnings (with warning areas) from the VicEmergency map feed alongside incidents.
- **Northern Territory (NT)** is not supported: the only machine-readable NT file found says not to use or scrape it.

**SA feed status and safety:** The official [SA open-data catalogue](https://data.sa.gov.au/data/dataset/south-australian-country-fire-service-current-incidents-rss-feed) and [CFS incident page](https://www.cfs.sa.gov.au/warnings-restrictions/warnings/incidents-warnings/) still publish links on `data.eso.sa.gov.au`, which returned an HTML “File Unavailable” response on 2 October 2026. The [official CFS map](https://apps.geohub.sa.gov.au/CFSMap/) uses a separate public ArcGIS incident layer; this is the `sa_cfs_gis` fallback. Its records can include historical incidents, and it does **not** provide the equivalent of the CAP warning feed. The fallback excludes records with no update time or older than seven days. If all returned records fail that check, the count is **unavailable**, not zero; if current and stale records coexist, `excluded_stale_count` is recorded in the coordinator data. A long-running incident with no recent update may be excluded, so the map fallback is **not** a safety-critical all-clear. Check the sensor's `source` attribute (`sa_cfs_gis`) and timestamps. Feed failures are reported as unavailable rather than zero incidents.

### Key Features
- **Multi-state support**: Monitor incidents across all six Australian states
- **Lifecycle events** (`incident_created|updated|removed`) for automation triggers
- **CAP alert events** (SA) for emergency warnings
- **Incident tracking**: `first_seen`, `last_seen`, `last_changed` timestamps with duration tracking
- **Normalized severity levels**: `info | advice | watch_and_act | emergency_warning | all_clear`
- **Multiple sensor types**:
  - Active incidents (total count + breakdown by severity)
  - Incident summary (detailed list with current status)
  - High severity incidents (emergency warnings and watch & act only)
- **Distance from home** on every incident and event (`distance_km`, `bearing`, `direction`), with lists sorted nearest first
- **Emergency nearby** binary sensor for "is something serious near home?"
- **Warning areas**: NSW, VIC, QLD and WA warning polygons are used for distance, so being inside a warning area counts as 0 km (`home_in_area: true`)
- **Map radius**: optionally only create map entities for incidents near home
- **Zone monitoring**: Optionally track incidents within selected Home Assistant zones, with an extra buffer distance
- **Repairs**: a Repairs card when a feed stays down, when SA falls back to the CFS map, or when a selected state has no feed
- **Exponential backoff retry**: Resilient data source polling with automatic backoff on failures
- **Manual refresh service**: `aus_emergency.refresh` to force immediate data updates
- **State removal service**: `aus_emergency.remove_state` to clean up devices when a state is deselected
- **Configurable update intervals** (default: 10 minutes)

## Installation
1. Clone/download `custom_components/aus_emergency/` to your Home Assistant `config/` directory.
2. Restart Home Assistant.
3. Navigate to **Settings → Devices & Services → Create Integration** → search for *Australian Emergency Services Incidents*.
4. Select your states (SA, NSW, VIC, QLD, WA, ACT) and configure your preferences. Only one instance of the integration can be added; use its options to change states.

### Configuration Options
- **States**: Select one or more emergency service regions:
  - **SA** (South Australia) — CFS/SES via CRIIMSON + CAP alerts
  - **NSW** (New South Wales) — RFS major incidents
  - **VIC** (Victoria) — Emergency Management Victoria
  - **QLD** (Queensland) — Queensland Fire and Emergency Services
  - **TAS** (Tasmania) — no current feed; only kept for existing configurations
  - **WA** (Western Australia) — DFES EmergencyWA API
  - **ACT** (Australian Capital Territory) — ESA current incidents
- **Update Interval**: How frequently to poll for new incidents, 60 to 86400 seconds (default: 10 minutes)
- **Remove Stale Incidents**: Enabled by default. After a successful feed update, remove ended incidents and orphaned registrations from earlier runs. Feed errors never trigger registry cleanup; you can explicitly turn this off to keep ended incidents as unavailable entities, which become available again if the incident returns.
- **Expose to Assistants**: Off by default. When on, newly created incident entities are exposed to Assist and Google Assistant. Existing entities are never re-exposed, so manual changes stick.
- **Zone Monitoring**: Optional — select Home Assistant zones to monitor incidents within them
- **Zone buffer (km)**: Default 0. Added to each zone's own radius, so a 100 m home zone with a 10 km buffer matches incidents within about 10 km. A zone inside a warning area always matches.
- **Map entity radius (km)**: Default 0 (every incident in the selected states). When set, only incidents within this distance of your Home Assistant home location get map entities and lifecycle events; an incident that moves outside it is treated as removed. Incidents without a location are skipped. Sensors still count the whole state.
- **Nearby emergency radius (km)** and **minimum severity**: Default 20 km and Watch and act. Control the Emergency nearby sensor below.

## Entities Created

### Geolocation Entities
- One entity per active incident with:
  - Coordinates for map display
  - Incident type, status, and severity
  - Location and region information
  - Agency, resource details, and duration
  - Zone membership (if within monitored zones)

### Sensor Entities
- **Active Incidents** (`sensor.*_active_incidents`): 
  - Total count of active incidents
  - Count breakdown by severity level
  - List of all incident summaries in attributes
  
- **Incident Summary** (`sensor.*_incident_summary`):
  - Detailed list of all incidents with current status
  - Rich attributes: incident number, type, severity, location, duration
  - Useful for dashboards and detailed automation logic

- **High Severity Incidents** (`sensor.*_high_severity_incidents`):
  - Count of `emergency_warning` and `watch_and_act` incidents only
  - For critical notification triggers

Incident lists in sensor attributes are sorted nearest to home first, so the 25-incident cap keeps the closest ones.

### Emergency nearby (`binary_sensor.emergency_nearby`)
- **On** when any incident at or above the minimum severity is within the nearby radius of home, or home is inside its warning area
- Attributes: `count`, `nearest_title`, `nearest_severity`, `nearest_distance_km`, `nearest_direction`, `home_in_warning_area`, `unavailable_states` and the matching `incidents` (up to 10)
- **Unavailable** instead of off when any feed is down and nothing matched, so a feed outage never looks like an all clear. A confirmed match keeps it on even if another state's feed is down.
- ACT incidents have no warning level and only count when the minimum severity is Info.


## Events for Automations

Create powerful automations by listening to incident lifecycle events. Each state fires the same core events plus state-specific CAP alerts (SA only).

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
  "distance_km": 12.4,
  "bearing": 318,
  "direction": "NW",
  "home_in_area": false,
  "map_url": "https://...",
  "google_maps_url": "https://maps.google.com/?q=..."
}
```

### Example Automations
```yaml
automation:
  - alias: "Emergency near home"
    trigger:
      platform: state
      entity_id: binary_sensor.emergency_nearby
      to: "on"
    action:
      - service: notify.mobile_app_phone
        data:
          title: "Emergency near home"
          message: >
            {{ state_attr('binary_sensor.emergency_nearby', 'nearest_title') }},
            {{ state_attr('binary_sensor.emergency_nearby', 'nearest_distance_km') }} km
            {{ state_attr('binary_sensor.emergency_nearby', 'nearest_direction') }}

  - alias: "New incident within 20 km"
    trigger:
      platform: event
      event_type: aus_emergency_incident_created
    condition:
      - "{{ trigger.event.data.distance_km is not none and trigger.event.data.distance_km <= 20 }}"
    action:
      - service: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.title }} ({{ trigger.event.data.distance_km }} km {{ trigger.event.data.direction }})"

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

## Example Dashboard

```yaml
type: vertical-stack
cards:
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.emergency_nearby
        state: "on"
    card:
      type: markdown
      content: >
        ## ⚠️ {{ state_attr('binary_sensor.emergency_nearby', 'nearest_title') }}
        {{ state_attr('binary_sensor.emergency_nearby', 'nearest_severity') | replace('_', ' ') | title }},
        {{ state_attr('binary_sensor.emergency_nearby', 'nearest_distance_km') }} km
        {{ state_attr('binary_sensor.emergency_nearby', 'nearest_direction') }} of home
  - type: map
    hours_to_show: 0
    default_zoom: 9
    geo_location_sources:
      - all
    entities:
      - zone.home
  - type: markdown
    title: Nearest incidents
    content: >
      {% for i in state_attr('sensor.sa_active_incidents', 'incidents')[:5] %}
      - **{{ i.type }}** at {{ i.location_name }}: {{ i.distance_km }} km {{ i.direction }} ({{ i.severity | replace('_', ' ') }})
      {% endfor %}
```

Change `sensor.sa_active_incidents` to your state's sensor. `geo_location_sources: all` shows every geo-location entity; list sources such as `sa_cfs` or `nsw_rfs` to narrow it.

## Services

### `aus_emergency.refresh`
Forces an immediate refresh of all incident data from all configured feeds without waiting for the next scheduled update.

**Usage in automation:**
```yaml
service: aus_emergency.refresh
```

### `aus_emergency.remove_state`
Manually removes all devices and entities for a specified state. Useful for cleaning up when a state is no longer needed.

```yaml
service: aus_emergency.remove_state
data:
  state: "VIC"
```

## Requirements
- **Home Assistant** 2024.6+
- **defusedxml** - for secure XML parsing

## Technical Details

- **Domain**: `aus_emergency`
- **Class**: Cloud Polling
- **Update Interval**: Configurable (default 600 seconds)
- **Code Owners**: @Anquietas86
- **Documentation**: [GitHub Repository](https://github.com/Anquietas86/Australian-Emergency-Services-Incidents)

## Current Status

Feeds are operated by their respective agencies and can be unavailable independently. SA incident monitoring has a best-effort map fallback; SA CAP alerts have no verified fallback. TAS has no currently working machine-readable feed. Treat unavailable entities as **unknown**, not zero incidents.

## Development tests

Install `pytest`, `aiohttp`, `defusedxml`, and `voluptuous` into a Python environment, then run `python -m pytest -q`. CI runs these tests plus hassfest and HACS validation on every push and pull request. Tests use minimal Home Assistant interface stubs and do not replace live HA integration testing.
