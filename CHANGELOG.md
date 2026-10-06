# Changelog

## v0.4.0

- Add distance from home to every incident: `distance_km`, `bearing` and `direction` (8-point compass) in attributes and lifecycle events, and `home_in_area` when home is inside a warning area. Incident lists are sorted nearest first.
- Add an **Emergency nearby** binary sensor (radius and minimum severity are configurable). It is unavailable, not off, when a feed is down and nothing matched.
- Add an optional **map entity radius**: only incidents within it get geo-location entities and events. Sensors still count the whole state.
- Add a **zone buffer** so zones match incidents within their radius plus a distance; a zone inside a warning area always matches.
- Keep warning-area polygons from NSW, QLD, VIC and WA (and SA CAP alerts) and use them for distance and zone checks. Polygons stay internal and never reach attributes, events or the recorder.
- Add **ACT** (ESA current incidents GeoRSS) and **VIC public warnings** (VicEmergency map feed). A failed VIC warnings fetch fails the VIC update, like WA, rather than showing incidents alone.
- Raise **Repairs** issues when a feed fails 3 updates in a row, when SA is on the CFS map fallback, and when a selected state has no feed (TAS). They clear on recovery or when the state is removed.
- Entity names now come from translations. Sensor friendly names drop the duplicated state code (for example "South Australia Active incidents"); entity IDs are unchanged.
- Add CI: pytest, hassfest and HACS validation.

## v0.3.5

Fixes from a code audit, verified against Home Assistant 2026.2.3.

- Keep the `refresh` and `remove_state` services registered after a reload or options save (they previously disappeared until restart).
- Validate the update interval (60 to 86400 seconds) and clamp stored out-of-range values, so 0 can no longer make the integration poll continuously.
- Allow only one config entry; a second entry collided with the first's devices and entity unique IDs.
- Stop firing `aus_emergency_incident_created` / `aus_emergency_cap_alert_created` for every active incident on each restart or reload. Only incidents not seen before are announced.
- Fix the "keep stale incidents" mode (`remove_stale` off): ended incidents now actually show as unavailable, fire one removed event, and come back on the same entity (with a created event) if they reappear.
- Expose new entities to assistants after they are registered (exposure was silently skipped before). The option now defaults to off for new installs; existing choices are kept.
- CAP alerts: tolerate polygons with newlines or malformed points, alerts without an area, and remove registrations for alerts that expired while HA was down.
- Timestamps: `incident_datetime` is now always timezone-aware. NSW `pubDate` (UTC, 12-hour clock) is now parsed, UTC `Z` values are no longer treated as local, and naive SA/VIC/QLD/WA times use the state's own timezone.
- Coerce coordinates to floats and fix a single-coordinate GeoJSON point producing a tuple latitude.
- NSW incident entity IDs are now valid (slugified). HA warned that the old URL-based IDs stop working in 2027.2. Unique IDs are unchanged.
- Reduce database growth: drop the per-update `summary_generated` attribute and exclude the incident list and volatile attributes (`last_seen`, `duration_minutes`, CAP text) from the recorder.
- Use Home Assistant's shared HTTP session, stop polling geo-location entities, avoid a duplicate fetch at startup, release coordinator listeners on unload, hide the feedless TAS option for new selections, and list TAS/WA in the `remove_state` service.

## v0.3.4

- Default `remove_stale` to on for new installations. Existing explicit off choices are still respected.
- Reconcile the HA entity registry on every successful incident update, removing registrations from previous runs that are no longer in the feed. Cleanup is scoped to the same config entry and state; CAP alerts, sensors, other states, and live incidents are preserved.
- Keep geo-location entities unavailable during a feed failure and restore availability after recovery; never prune the registry from a failed fetch.
- Add regression tests for registry scope, startup orphans, explicit opt-out, and feed failure/recovery.

## v0.3.3

- Reject historical or undated records from the SA CFS map fallback. If its only records are stale, report the incident sensor as unavailable (unknown), never as a live incident or a reassuring zero.
- Record the number of omitted stale map records for diagnostic use; add regressions for stale-only, undated, and mixed-age responses.

## v0.3.2

- Isolate feed failures: failed SA/CAP/TAS sources no longer block healthy states or the integration from loading; unavailable feeds remain unavailable and retry automatically.
- Reject HTTP errors and non-feed responses instead of reporting an unsafe zero count; close coordinator sessions if setup fails.
- When the SA CRIIMSON JSON feed is unavailable, use the public incident layer used by the official CFS map as a *best-effort* fallback. Sensors identify it as `sa_cfs_gis`; this layer may contain older records and does not replace CAP warnings.
- Update VIC incident identifiers, locations, statuses, agency and coordinates for the current feed schema; restore NSW alert severity and status from the current feed.
- Add offline regression tests for feed failures, partial setup and provider payloads.

## v0.3.1

- **TAS feed retired**: Set georss URL to None — fire.tas.gov.au RSS/KML feeds permanently retired (410 Gone). All five other state feeds verified 200 OK with live data.
- **Logo optimization**: Resized root logo.png from 1024×1024 (1.1MB) to 256×256 (39KB) for HACS compatibility. Removed duplicate hacs_logo.png.

## v0.3.0

- **TAS & WA support**: Tasmania Fire Service (GeoRSS) and WA DFES (EmergencyWA API + warnings) added to all states
- **Fixed diagnostics**: Now iterates all coordinators across all states instead of just the legacy key
- **CAP coordinator optimization**: Only creates CAP data coordinators for states that actually have a CAP feed URL (SA only)
- **Refactored Haversine**: Extracted distance calculation into shared `utils.haversine_distance()` utility
- **Added `aus_emergency.remove_state` service**: Cleanly remove devices/entities for a deselected state
- **Updated `.gitignore`**: Added common Python/IDE patterns
- **README overhaul**: Documented all six states, both services, and updated entity naming examples
- **Removed unused import**: `parse_datetime` removed from geo_location.py
- **Expose to Assistants toggle**: Config option to control voice assistant exposure per integration

## v0.2.5

- Added TAS & WA feeds
- Bugfixes

## v0.2.1

- Fixed QLD feed (QS3 binary/octet-stream content type)
- Added ability to enable/disable exposing entities to assistants
- Added ability to delete integration (async_remove_entry with device cleanup)

## v0.2.0

- Added NSW RFS (GeoJSON), VIC EMV (JSON), and QLD QFES (GeoJSON) feeds
- Multi-state support with state-prefixed entity IDs
- CAP alert lifecycle events (created/updated/removed) for SA
- High severity incidents sensor (emergency_warning + watch_and_act)
- Duration tracking (first_seen with duration_minutes attribute)
- Exponential backoff retry on feed failures (30s → 60s → 120s → … max 600s)
- Incident datetime parsing with ISO format normalization
- Zone monitoring with configurable zone selection and Haversine distance
- **Breaking**: Coordinator key changed from `cfs_coordinator` to `incident_coordinator`

## v0.1.0

- Initial release — SA CFS CRIIMSON feed support
