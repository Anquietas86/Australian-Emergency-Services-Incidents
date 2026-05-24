# Changelog

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
