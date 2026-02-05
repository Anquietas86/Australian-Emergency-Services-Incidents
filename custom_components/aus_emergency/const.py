DOMAIN = "aus_emergency"

CONF_STATE = "state"  # Legacy single state
CONF_STATES = "states"  # New multi-state support
CONF_UPDATE_INTERVAL = "update_interval"
CONF_REMOVE_STALE = "remove_stale"
CONF_EXPOSE_TO_ASSISTANTS = "expose_to_assistants"
CONF_ZONES = "zones"

DEFAULT_STATE = "SA"
DEFAULT_STATES = ["SA"]
DEFAULT_UPDATE_INTERVAL = 600  # seconds
DEFAULT_REMOVE_STALE = False
DEFAULT_EXPOSE_TO_ASSISTANTS = True

# Supported states
SUPPORTED_STATES = ["SA", "NSW", "VIC", "QLD", "TAS", "WA"]

# Data source identifiers
SOURCE_SA_CFS = "sa_cfs"
SOURCE_NSW_RFS = "nsw_rfs"
SOURCE_VIC_EMV = "vic_emv"
SOURCE_QLD_QFES = "qld_qfes"
SOURCE_TAS_TFS = "tas_tfs"
SOURCE_WA_DFES = "wa_dfes"

# Feed URLs by state
FEED_URLS = {
    "SA": {
        "json": "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json",
        "cap": "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_cap_incidents.xml",
        "source": SOURCE_SA_CFS,
    },
    "NSW": {
        "json": "https://www.rfs.nsw.gov.au/feeds/majorIncidents.json",
        "cap": None,  # NSW uses GeoJSON, no CAP feed
        "source": SOURCE_NSW_RFS,
    },
    "VIC": {
        "json": "https://data.emergency.vic.gov.au/Show?pageId=getIncidentJSON",
        "cap": None,
        "source": SOURCE_VIC_EMV,
    },
    "QLD": {
        "json": "https://publiccontent-gis-psba-qld-gov-au.s3.amazonaws.com/content/Feeds/BushfireCurrentIncidents/bushfireAlert.json",
        "cap": None,
        "source": SOURCE_QLD_QFES,
    },
    "TAS": {
        "json": None,  # TAS uses GeoRSS, not JSON
        "georss": "http://www.fire.tas.gov.au/Show?pageId=colBushfireSummariesRss",
        "cap": None,
        "source": SOURCE_TAS_TFS,
    },
    "WA": {
        "json": "https://api.emergency.wa.gov.au/v1/incidents",
        "warnings": "https://api.emergency.wa.gov.au/v1/warnings",
        "cap": None,
        "source": SOURCE_WA_DFES,
    },
}

ATTR_INCIDENT_NO = "incident_no"
ATTR_TYPE = "type"
ATTR_STATUS = "status"
ATTR_LEVEL = "level"
ATTR_REGION = "region"
ATTR_LOCATION_NAME = "location_name"
ATTR_MESSAGE = "message"
ATTR_MESSAGE_LINK = "message_link"
ATTR_RESOURCES = "resources"
ATTR_AIRCRAFT = "aircraft"
ATTR_DATE = "date"
ATTR_TIME = "time"
ATTR_AGENCY = "agency"

ATTR_SEVERITY = "severity"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_INCIDENT_DATETIME = "incident_datetime"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_IN_ZONE = "in_zone"

# High severity levels for filtering
HIGH_SEVERITY_LEVELS = ["emergency_warning", "watch_and_act"]

# Maximum incidents to store in sensor attributes (to avoid exceeding 16KB limit)
MAX_INCIDENTS_IN_ATTRIBUTES = 25

EVENT_CREATED = "aus_emergency_incident_created"
EVENT_UPDATED = "aus_emergency_incident_updated"
EVENT_REMOVED = "aus_emergency_incident_removed"

# CAP-specific events
EVENT_CAP_CREATED = "aus_emergency_cap_alert_created"
EVENT_CAP_UPDATED = "aus_emergency_cap_alert_updated"
EVENT_CAP_REMOVED = "aus_emergency_cap_alert_removed"

SERVICE_REFRESH = "refresh"
SERVICE_REMOVE_STATE = "remove_state"

# Retry/backoff settings
DEFAULT_RETRY_DELAY = 30  # seconds
MAX_RETRY_DELAY = 600  # 10 minutes max backoff
BACKOFF_MULTIPLIER = 2

DEVICE_INFO_SA_CFS = {
    "identifiers": {("aus_emergency", "sa_cfs")},
    "name": "South Australia",
    "manufacturer": "SA Government",
    "model": "CRIIMSON Feed",
}

DEVICE_INFO_NSW_RFS = {
    "identifiers": {("aus_emergency", "nsw_rfs")},
    "name": "New South Wales",
    "manufacturer": "NSW Government",
    "model": "RFS Feed",
}

DEVICE_INFO_VIC_EMV = {
    "identifiers": {("aus_emergency", "vic_emv")},
    "name": "Victoria",
    "manufacturer": "VIC Government",
    "model": "EMV Feed",
}

DEVICE_INFO_QLD_QFES = {
    "identifiers": {("aus_emergency", "qld_qfes")},
    "name": "Queensland",
    "manufacturer": "QLD Government",
    "model": "QFD Feed",
}

DEVICE_INFO_TAS_TFS = {
    "identifiers": {("aus_emergency", "tas_tfs")},
    "name": "Tasmania",
    "manufacturer": "TAS Government",
    "model": "TFS GeoRSS Feed",
}

DEVICE_INFO_WA_DFES = {
    "identifiers": {("aus_emergency", "wa_dfes")},
    "name": "Western Australia",
    "manufacturer": "WA Government",
    "model": "EmergencyWA API",
}

STATE_DEVICE_INFO = {
    "SA": DEVICE_INFO_SA_CFS,
    "NSW": DEVICE_INFO_NSW_RFS,
    "VIC": DEVICE_INFO_VIC_EMV,
    "QLD": DEVICE_INFO_QLD_QFES,
    "TAS": DEVICE_INFO_TAS_TFS,
    "WA": DEVICE_INFO_WA_DFES,
}
