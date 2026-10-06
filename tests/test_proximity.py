"""Distance, warning-area, nearby-sensor, ACT/VIC-warning and Repairs behaviour."""
import asyncio
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from conftest import ISSUES, UpdateFailed, binary_sensor, const, coordinator, geo_location, integration, utils
from test_feeds import Response
from test_geo_cleanup import Entry, Hass, Registry

ADELAIDE = (-34.9285, 138.6007)
# Square warning area around the Adelaide CBD, as (lat, lon)
CBD_RING = [(-34.90, 138.57), (-34.90, 138.63), (-34.96, 138.63), (-34.96, 138.57)]


# --- geometry helpers -------------------------------------------------------

def test_compass_direction_and_bearing():
    assert utils.compass_direction(0) == "N"
    assert utils.compass_direction(350) == "N"
    assert utils.compass_direction(135) == "SE"
    # Due east of Adelaide
    assert round(utils.initial_bearing(*ADELAIDE, ADELAIDE[0], 139.6)) in (89, 90, 91)


def test_point_in_polygon():
    assert utils.point_in_polygon(*ADELAIDE, CBD_RING)
    assert not utils.point_in_polygon(-35.5, 138.6, CBD_RING)
    assert not utils.point_in_polygon(*ADELAIDE, CBD_RING[:2])


def test_distance_is_zero_inside_area_and_uses_nearest_vertex_outside():
    assert utils.distance_to_incident(*ADELAIDE, -36.0, 140.0, [CBD_RING]) == (0.0, True)
    # Home 10 km north of the area: closer to its edge than to its far-away pin.
    distance, inside = utils.distance_to_incident(-34.81, 138.60, -36.0, 140.0, [CBD_RING])
    assert not inside and 9 < distance < 11
    assert utils.distance_to_incident(*ADELAIDE, None, None) == (None, False)


def test_extract_polygons_handles_z_multipolygon_and_collections():
    ring = [[138.57, -34.90, 0], [138.63, -34.90, 0], [138.63, -34.96, 0], [138.57, -34.96, 0]]
    assert coordinator._extract_polygons({"type": "Polygon", "coordinates": [ring]})[0][0] == (-34.90, 138.57)
    assert len(coordinator._extract_polygons({"type": "MultiPolygon", "coordinates": [[ring], [ring]]})) == 2
    collection = {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [138.6, -34.9]}, {"type": "Polygon", "coordinates": [ring]}]}
    assert len(coordinator._extract_polygons(collection)) == 1
    assert coordinator._extract_polygons({"type": "Polygon", "coordinates": [[[1, 2]]]}) == []
    assert coordinator._extract_polygons(None) == []


# --- coordinator post-processing --------------------------------------------

def coord_with_home(state="SA"):
    obj = coordinator.IncidentDataCoordinator(None, state, 600)
    obj.hass = SimpleNamespace(config=SimpleNamespace(latitude=ADELAIDE[0], longitude=ADELAIDE[1]))
    return obj


def test_incidents_get_distance_direction_and_are_sorted_nearest_first():
    incidents = [
        {const.ATTR_LATITUDE: -34.0, const.ATTR_LONGITUDE: 138.6},   # ~100 km N
        {const.ATTR_LATITUDE: None, const.ATTR_LONGITUDE: None},
        {const.ATTR_LATITUDE: -36.0, const.ATTR_LONGITUDE: 140.0, const.ATTR_POLYGONS: [CBD_RING]},
    ]
    coord_with_home()._add_distances(incidents)
    inside, north, unknown = incidents
    assert inside[const.ATTR_DISTANCE_KM] == 0.0 and inside[const.ATTR_HOME_IN_AREA]
    assert inside[const.ATTR_DIRECTION] is None
    assert 90 < north[const.ATTR_DISTANCE_KM] < 110 and north[const.ATTR_DIRECTION] == "N"
    assert unknown[const.ATTR_DISTANCE_KM] is None


def test_qld_polygon_is_kept_and_pinned_at_centroid():
    ring = [[153.0, -27.0, 0], [153.2, -27.0, 0], [153.2, -27.2, 0], [153.0, -27.2, 0]]
    body = json.dumps({"features": [{"properties": {"UniqueID": "WARN-1", "WarningLevel": "Advice"},
                                     "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    obj = coordinator.IncidentDataCoordinator(None, "QLD", 600)
    inc = asyncio.run(obj._parse_qld_data(Response(200, body, "binary/octet-stream")))["incidents"][0]
    assert len(inc[const.ATTR_POLYGONS]) == 1
    assert (round(inc[const.ATTR_LATITUDE], 2), round(inc[const.ATTR_LONGITUDE], 2)) == (-27.1, 153.1)


# --- VIC warnings ------------------------------------------------------------

VIC_WARNING = {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": [
    {"type": "Point", "coordinates": [143.835, -38.23]},
    {"type": "Polygon", "coordinates": [[[143.88, -37.81], [143.89, -37.83], [143.90, -37.83], [143.88, -37.81]]]}]},
    "properties": {"feedType": "warning", "id": "43467", "sourceId": "43467", "category1": "Watch and Act",
                   "location": "Barwon River to Inverleigh", "updated": "2026-10-06T12:41:23+11:00",
                   "action": "Threat Is Reduced", "sourceOrg": "EMV",
                   "cap": {"event": "Riverine Flood", "senderName": "State Emergency Service"}}}
VIC_INCIDENT = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [144.867, -37.832]},
                "properties": {"feedType": "incident", "sourceId": 618639, "category1": "Tree Down"}}


class UrlSession:
    closed = False

    def __init__(self, responses):
        self.responses = responses

    def get(self, url, timeout):
        return self.responses[url]


def vic_session(warnings_response):
    urls = const.FEED_URLS["VIC"]
    return UrlSession({urls["json"]: Response(200, json.dumps({"results": []})),
                       urls["warnings"]: warnings_response})


def test_vic_warnings_are_added_with_their_area():
    obj = coordinator.IncidentDataCoordinator(None, "VIC", 600)
    obj._session = vic_session(Response(200, json.dumps({"features": [VIC_WARNING, VIC_INCIDENT]})))
    (warning,) = asyncio.run(obj._fetch_data())["incidents"]
    assert warning[const.ATTR_INCIDENT_NO] == "warning-43467"
    assert warning[const.ATTR_SEVERITY] == "watch_and_act"
    assert warning[const.ATTR_TYPE] == "Riverine Flood"
    assert (warning[const.ATTR_LATITUDE], warning[const.ATTR_LONGITUDE]) == (-38.23, 143.835)
    assert len(warning[const.ATTR_POLYGONS]) == 1


@pytest.mark.parametrize("response", [
    Response(503, "down", "text/plain"),
    Response(200, "<html>maintenance</html>", "text/html"),
    Response(200, json.dumps({"error": "nope"})),
])
def test_failed_vic_warnings_fail_the_update(response):
    obj = coordinator.IncidentDataCoordinator(None, "VIC", 600)
    obj._session = vic_session(response)
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


# --- ACT ----------------------------------------------------------------------

ACT_FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel><title>ESA</title>
<item><title>RUBBISH FIRE - CITY</title><link>https://esa.act.gov.au/feeds/currentincidents.xml</link>
<type>RUBBISH FIRE</type><agency>Fire</agency>
<description>Incident: RUBBISH FIRE - CITY Location: TRAM STOP 14, ALINGA STREET, CITY, 2601 Status: On Scene Suburb: CITY Type: RUBBISH FIRE Agency: Fire Incident Number: 016125-06102026 Updated: 06 Oct 2026 17:13:47.65 Time of Call: 06 Oct 2026 17:07:09</description>
<guid>016125-06102026</guid><cadid>016125-06102026</cadid><pubDate>2026-10-06 17:18 AEDT</pubDate>
<georss:point>-35.2780036926 149.1292266846</georss:point><resourceStatus>On Scene</resourceStatus>
<controlStatus>Not Applicable</controlStatus></item></channel></rss>"""


def test_act_feed_is_parsed():
    obj = coordinator.IncidentDataCoordinator(None, "ACT", 600)
    obj._tz = ZoneInfo("Australia/Sydney")
    (inc,) = obj._parse_act_georss(ACT_FEED)["incidents"]
    assert inc[const.ATTR_INCIDENT_NO] == "016125-06102026"
    assert (inc[const.ATTR_LATITUDE], inc[const.ATTR_LONGITUDE]) == (-35.2780036926, 149.1292266846)
    assert inc[const.ATTR_TYPE] == "Rubbish Fire"
    assert inc[const.ATTR_STATUS] == "On Scene"
    assert inc[const.ATTR_LOCATION_NAME] == "Tram Stop 14, Alinga Street, City, 2601"
    assert inc[const.ATTR_AGENCY] == "ACT Fire"
    assert inc[const.ATTR_INCIDENT_DATETIME].startswith("2026-10-06T")
    sydney = coordinator.datetime.fromisoformat(inc[const.ATTR_INCIDENT_DATETIME]).astimezone(ZoneInfo("Australia/Sydney"))
    assert (sydney.hour, sydney.minute) == (17, 13)


@pytest.mark.parametrize("body", ["<html>File Unavailable</html", "<html><body>down</body></html>"])
def test_broken_act_feed_is_unavailable_not_zero(body):
    obj = coordinator.IncidentDataCoordinator(None, "ACT", 600)
    with pytest.raises(UpdateFailed):
        obj._parse_act_georss(body)


def test_empty_act_feed_is_zero_incidents():
    obj = coordinator.IncidentDataCoordinator(None, "ACT", 600)
    assert obj._parse_act_georss('<rss version="2.0"><channel></channel></rss>') == {"incidents": []}


# --- Repairs -----------------------------------------------------------------

def test_repairs_issue_after_repeated_failures_and_cleared_on_recovery():
    ISSUES.clear()
    obj = coordinator.IncidentDataCoordinator(None, "NSW", 600)
    obj._session = UrlSession({const.FEED_URLS["NSW"]["json"]: Response(503, "down", "text/plain")})
    for _ in range(const.FEED_ISSUE_FAILURE_THRESHOLD):
        assert "feed_unavailable_nsw" not in ISSUES
        with pytest.raises(UpdateFailed):
            asyncio.run(obj._async_update_data())
    assert ISSUES["feed_unavailable_nsw"]["translation_placeholders"]["state"] == "NSW"

    obj._session = UrlSession({const.FEED_URLS["NSW"]["json"]: Response(200, json.dumps({"features": []}))})
    asyncio.run(obj._async_update_data())
    assert "feed_unavailable_nsw" not in ISSUES


def test_tas_gets_a_no_feed_issue_not_a_failure_issue():
    ISSUES.clear()
    integration._sync_state_issues(None, ["TAS", "SA"])
    assert "no_feed_tas" in ISSUES
    integration._sync_state_issues(None, ["SA"])
    assert "no_feed_tas" not in ISSUES


# --- nearby emergency binary sensor -----------------------------------------

def incident(severity, distance, in_area=False, **extra):
    return {const.ATTR_SEVERITY: severity, const.ATTR_DISTANCE_KM: distance,
            const.ATTR_HOME_IN_AREA: in_area, const.ATTR_TYPE: "Bushfire",
            const.ATTR_LOCATION_NAME: "Somewhere", const.ATTR_POLYGONS: [CBD_RING], **extra}


def test_matching_respects_radius_severity_and_home_in_area():
    matches = binary_sensor.matching_incidents({"SA": [
        incident("watch_and_act", 5),
        incident("advice", 1),                     # below threshold
        incident("emergency_warning", 50),         # too far
        incident("emergency_warning", 80, True),   # far pin, but home is inside the area
        incident("all_clear", 0),
        incident("watch_and_act", None),
    ]}, 20, "watch_and_act")
    assert [(m[const.ATTR_SEVERITY], m[const.ATTR_DISTANCE_KM]) for m in matches] == [
        ("emergency_warning", 80), ("watch_and_act", 5)]
    assert matches[0]["state"] == "SA"


class FakeCoord:
    def __init__(self, incidents, success=True):
        self.data = {"incidents": incidents} if success else None
        self.last_update_success = success


def sensor(coords, **options):
    entry = SimpleNamespace(entry_id="e", data={}, options=options)
    return binary_sensor.NearbyEmergencyBinarySensor(entry, coords)


def test_sensor_on_with_nearest_details_and_no_polygons_in_attributes():
    ent = sensor({"SA": FakeCoord([incident("emergency_warning", 3, direction="NW")])})
    assert ent.available and ent.is_on
    attrs = ent.extra_state_attributes
    assert attrs["nearest_distance_km"] == 3 and attrs["nearest_direction"] == "NW"
    assert attrs["nearest_title"] == "Bushfire at Somewhere"
    assert const.ATTR_POLYGONS not in attrs["incidents"][0]


def test_sensor_off_needs_every_feed_but_on_does_not():
    quiet_failed = sensor({"SA": FakeCoord([]), "NSW": FakeCoord([], success=False)})
    assert not quiet_failed.available
    assert quiet_failed.extra_state_attributes["unavailable_states"] == ["NSW"]

    on_failed = sensor({"SA": FakeCoord([incident("emergency_warning", 1)]), "NSW": FakeCoord([], success=False)})
    assert on_failed.available and on_failed.is_on

    # TAS has no feed; it must not keep the sensor unavailable forever.
    tas = sensor({"SA": FakeCoord([]), "TAS": FakeCoord([], success=False)})
    assert tas.available and not tas.is_on


def test_sensor_uses_configured_radius_and_severity():
    ent = sensor({"SA": FakeCoord([incident("advice", 30)])}, alert_radius=50, alert_min_severity="advice")
    assert ent.is_on


# --- geo entities: radius filter, zone buffer, events -----------------------

class Coord:
    source = "test"

    def __init__(self, incidents):
        self.last_update_success = True
        self.data = {"incidents": incidents}
        self.callback = None

    def async_add_listener(self, callback):
        self.callback = callback
        return lambda: None


def setup_geo(monkeypatch, incidents, **kwargs):
    registry = Registry([])
    monkeypatch.setattr(geo_location.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(geo_location.er, "async_entries_for_config_entry", lambda r, cid: [], raising=False)
    hass, added, coord = Hass(), [], Coord(incidents)
    geo_location._setup_incident_entities(
        hass, Entry(), lambda entities, **kw: added.extend(entities), coord, {}, [], True, False, "SA", **kwargs)
    return hass, added, coord


def geo_item(no, distance, **extra):
    return {const.ATTR_INCIDENT_NO: no, const.ATTR_LATITUDE: -35.0, const.ATTR_LONGITUDE: 138.0,
            const.ATTR_DISTANCE_KM: distance, const.ATTR_DIRECTION: "S", **extra}


def test_radius_filter_skips_far_and_unlocated_incidents(monkeypatch):
    hass, added, _ = setup_geo(monkeypatch, [geo_item("near", 5), geo_item("far", 500), geo_item("nowhere", None)],
                               radius=50)
    assert [e.entity_id for e in added] == ["geo_location.aus_emergency_sa_near"]
    assert [p["incident_no"] for _, p in hass.events] == ["near"]


def test_zero_radius_keeps_every_incident(monkeypatch):
    _, added, _ = setup_geo(monkeypatch, [geo_item("near", 5), geo_item("nowhere", None)])
    assert len(added) == 2


def test_incident_moving_out_of_radius_is_removed(monkeypatch):
    hass, added, coord = setup_geo(monkeypatch, [geo_item("x", 5)], radius=50)
    coord.data = {"incidents": [geo_item("x", 80)]}
    coord.callback()
    assert hass.events[-1][0] == const.EVENT_REMOVED


def test_event_carries_distance_and_entity_hides_polygons(monkeypatch):
    hass, added, _ = setup_geo(monkeypatch, [geo_item("x", 12.3, **{const.ATTR_POLYGONS: [CBD_RING]})])
    payload = hass.events[0][1]
    assert payload["distance_km"] == 12.3 and payload["direction"] == "S"
    assert const.ATTR_POLYGONS not in added[0].extra_state_attributes
    assert added[0].distance == 12.3


def zone_hass(radius_m):
    zone = SimpleNamespace(attributes={"latitude": -35.0, "longitude": 138.0, "radius": radius_m,
                                       "friendly_name": "Home"})
    return SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: zone))


def test_zone_buffer_extends_zone_radius():
    hass = zone_hass(100)
    # ~5.6 km north of the zone centre
    assert not geo_location._point_in_zone(hass, -34.95, 138.0, "zone.home")
    assert geo_location._point_in_zone(hass, -34.95, 138.0, "zone.home", buffer_km=10)


def test_zone_inside_warning_area_matches_whatever_the_distance():
    ring = [(-34.9, 137.9), (-34.9, 138.1), (-35.1, 138.1), (-35.1, 137.9)]
    assert geo_location._get_zones_for_point(zone_hass(100), -30.0, 140.0, ["zone.home"], 0, [ring]) == ["Home"]
