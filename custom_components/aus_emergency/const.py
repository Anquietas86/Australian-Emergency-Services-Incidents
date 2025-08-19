
DOMAIN = "aus_emergency"

CONF_STATE = "state"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_REMOVE_STALE = "remove_stale"

DEFAULT_STATE = "SA"
DEFAULT_UPDATE_INTERVAL = 600  # seconds
DEFAULT_REMOVE_STALE = False

SOURCE_SA_CFS = "sa_cfs"

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

EVENT_CREATED = "aus_emergency_incident_created"
EVENT_UPDATED = "aus_emergency_incident_updated"
EVENT_REMOVED = "aus_emergency_incident_removed"

SERVICE_REFRESH = "refresh"
