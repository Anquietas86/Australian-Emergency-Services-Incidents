DOMAIN = "aus_emergency"

CONF_STATE = "state"  # Legacy single state
CONF_STATES = "states"  # New multi-state support
CONF_UPDATE_INTERVAL = "update_interval"
CONF_REMOVE_STALE = "remove_stale"
CONF_EXPOSE_TO_ASSISTANTS = "expose_to_assistants"
CONF_ZONES = "zones"
CONF_RADIUS = "radius"  # km; 0 = whole state
CONF_ZONE_BUFFER = "zone_buffer"  # km added to each zone's own radius
CONF_ALERT_RADIUS = "alert_radius"  # km, for the nearby emergency binary sensor
CONF_ALERT_MIN_SEVERITY = "alert_min_severity"

DEFAULT_STATE = "SA"
DEFAULT_STATES = ["SA"]
DEFAULT_UPDATE_INTERVAL = 600  # seconds
MIN_UPDATE_INTERVAL = 60  # seconds; protects public feeds from runaway polling
MAX_UPDATE_INTERVAL = 86400
DEFAULT_REMOVE_STALE = True
DEFAULT_EXPOSE_TO_ASSISTANTS = False
DEFAULT_RADIUS = 0
DEFAULT_ZONE_BUFFER = 0
DEFAULT_ALERT_RADIUS = 20
DEFAULT_ALERT_MIN_SEVERITY = "watch_and_act"
MAX_RADIUS_KM = 5000

# Severities in increasing order of urgency; all_clear never triggers an alert.
SEVERITY_ORDER = ["info", "advice", "watch_and_act", "emergency_warning"]

# Supported states
SUPPORTED_STATES = ["SA", "NSW", "VIC", "QLD", "TAS", "WA", "ACT"]
# States with no machine-readable feed are kept for existing configs but not offered for new selections.
UNAVAILABLE_STATES = ["TAS"]
SELECTABLE_STATES = [s for s in SUPPORTED_STATES if s not in UNAVAILABLE_STATES]

# Timezone each provider publishes naive local timestamps in
STATE_TIME_ZONES = {
    "SA": "Australia/Adelaide",
    "NSW": "Australia/Sydney",
    "VIC": "Australia/Melbourne",
    "QLD": "Australia/Brisbane",
    "TAS": "Australia/Hobart",
    "WA": "Australia/Perth",
    "ACT": "Australia/Sydney",
}

# Data source identifiers
SOURCE_SA_CFS = "sa_cfs"
SOURCE_SA_CFS_GIS = "sa_cfs_gis"
SA_MAP_MAX_RECORD_AGE_DAYS = 7  # Freshness check, not a claim that incidents resolve within a week.
# Public incident layer linked from the official CFS map, used when CRIIMSON is down.
SA_MAP_INCIDENTS_URL = (
    "https://cfsdata.geohub.sa.gov.au/server/rest/services/IMS_Read/"
    "SACFS_and_SAMFS_Incidents_and_Incident_Updates/FeatureServer/1/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326"
    "&resultRecordCount=2000&f=json"
)
SOURCE_NSW_RFS = "nsw_rfs"
SOURCE_VIC_EMV = "vic_emv"
SOURCE_QLD_QFES = "qld_qfes"
SOURCE_TAS_TFS = "tas_tfs"
SOURCE_WA_DFES = "wa_dfes"
SOURCE_ACT_ESA = "act_esa"

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
        # Public warnings (with warning-area polygons) from the VicEmergency map.
        "warnings": "https://emergency.vic.gov.au/public/osom-geojson.json",
        "cap": None,
        "source": SOURCE_VIC_EMV,
    },
    "QLD": {
        "json": "https://publiccontent-gis-psba-qld-gov-au.s3.amazonaws.com/content/Feeds/BushfireCurrentIncidents/bushfireAlert.json",
        "cap": None,
        "source": SOURCE_QLD_QFES,
    },
    "TAS": {
        "json": None,
        "georss": None,  # fire.tas.gov.au RSS/KML feeds retired (410 Gone) — replaced by alert.tas.gov.au which has no machine-readable feed
        "cap": None,
        "source": SOURCE_TAS_TFS,
    },
    "WA": {
        "json": "https://api.emergency.wa.gov.au/v1/incidents",
        "warnings": "https://api.emergency.wa.gov.au/v1/warnings",
        "cap": None,
        "source": SOURCE_WA_DFES,
    },
    "ACT": {
        "georss": "https://www.esa.act.gov.au/feeds/currentincidents.xml",
        "cap": None,
        "source": SOURCE_ACT_ESA,
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
ATTR_DISTANCE_KM = "distance_km"
ATTR_BEARING = "bearing"
ATTR_DIRECTION = "direction"
ATTR_HOME_IN_AREA = "home_in_area"
# Warning-area rings as (lat, lon) lists. Internal only: never written to
# attributes or events, where it would blow the attribute size limit.
ATTR_POLYGONS = "_polygons"

# Consecutive failed updates before a Repairs issue is raised
FEED_ISSUE_FAILURE_THRESHOLD = 3

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

DEVICE_INFO_ACT_ESA = {
    "identifiers": {("aus_emergency", "act_esa")},
    "name": "Australian Capital Territory",
    "manufacturer": "ACT Government",
    "model": "ESA Current Incidents Feed",
}

STATE_DEVICE_INFO = {
    "SA": DEVICE_INFO_SA_CFS,
    "NSW": DEVICE_INFO_NSW_RFS,
    "VIC": DEVICE_INFO_VIC_EMV,
    "QLD": DEVICE_INFO_QLD_QFES,
    "TAS": DEVICE_INFO_TAS_TFS,
    "WA": DEVICE_INFO_WA_DFES,
    "ACT": DEVICE_INFO_ACT_ESA,
}
