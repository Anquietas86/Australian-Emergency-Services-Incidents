## Changelog
- 0.2.0: New Features & Updates
  - Multi-State Support: NSW RFS, VIC EMV, QLD QFES feeds added alongside SA CFS
  - CAP Alert Events: Now fires aus_emergency_cap_alert_created, _updated, _removed
  - High Severity Sensor: New sensor.*_high_severity_incidents counts only emergency_warning + watch_and_act
  - Duration Tracking: All incidents now include duration_minutes attribute
  - Retry Backoff: Exponential backoff (30s → 60s → 120s → ... max 600s) on feed failures
  - Datetime Parsing: incident_datetime attribute with proper ISO format
  - Zone Monitoring: Select HA zones in config; incidents show in_zone list of matching zones
  Breaking Chnages - Coordinator key changed from cfs_coordinator to incident_coordinator (only affects custom automation reading hass.data)
- 0.1.23: Add support for SA CFS CAP alerts
- 0.1.22: Refactor and improve integration
  - Centralized data processing in the coordinator.
  - Improved incident summary to be more dynamic.
  - Reduced code duplication.
  - Added more robust logging and error handling.
- 0.1.21: Automation-friendly improvements (events, timestamps, severity, counts, refresh service)
- 0.1.20: Active incidents sensor
- 0.1.16: Stable SA CFS/SES geolocation
