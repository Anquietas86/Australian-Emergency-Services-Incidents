"""Regressions for real provider payload shapes and failure semantics."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from conftest import cap, coordinator, const, integration, ConfigEntryNotReady, UpdateFailed


class Response:
    def __init__(self, status, body, content_type="application/json"):
        self.status = status
        self.body = body
        self.content_type = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self, content_type=None):
        return json.loads(self.body)

    async def text(self):
        return self.body


class Session:
    closed = False

    def __init__(self, response):
        self.response = response

    def get(self, url, timeout):
        return self.response


@pytest.mark.parametrize("status,body,content_type", [
    (200, "<html>SA ESS - File Unavailable</html>", "text/html"),
    (503, "unavailable", "text/plain"),
])
def test_failed_incident_feed_is_not_zero_incidents(status, body, content_type):
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = Session(Response(status, body, content_type))
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


@pytest.mark.parametrize("status,body,content_type", [
    (200, "<html>SA ESS - File Unavailable</html>", "text/html"),
    (503, "unavailable", "text/plain"),
])
def test_failed_cap_feed_is_not_zero_alerts(status, body, content_type):
    obj = cap.CFSCAPDataCoordinator(None, "SA", 600)
    obj._session = Session(Response(status, body, content_type))
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


def test_qld_octet_stream_json_is_supported():
    obj = coordinator.IncidentDataCoordinator(None, "QLD", 600)
    obj._session = Session(Response(200, json.dumps({"features": []}), "binary/octet-stream"))
    assert asyncio.run(obj._fetch_data()) == {"incidents": []}


def test_wa_warning_failure_does_not_publish_incomplete_incident_count():
    class TwoFeedSession:
        closed = False
        def get(self, url, timeout):
            if "warnings" in url:
                return Response(503, "unavailable", "text/plain")
            return Response(200, json.dumps({"incidents": []}))
    obj = coordinator.IncidentDataCoordinator(None, "WA", 600)
    obj._session = TwoFeedSession()
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


def test_sa_uses_official_map_fallback_when_json_feed_is_down():
    class SAFeeds:
        closed = False
        def get(self, url, timeout):
            if "cfsdata.geohub.sa.gov.au" in url:
                return Response(200, json.dumps({"features": [{"attributes": {
                    "ident": "F2610020106", "event": "Structure Fire", "inc_status": "Going",
                    "warn_level": "Incident", "inc_name": "MUNNO PARA WEST",
                    "location": "PONDEROSA, MUNNO PARA WEST", "authority": "SA CFS",
                    "lat": -34.665717, "long": 138.670983,
                    "updated": int(datetime.now(timezone.utc).timestamp() * 1000),
                }}]}))
            return Response(200, "<html>SA ESS - File Unavailable</html>", "text/html")
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = SAFeeds()
    result = asyncio.run(obj._fetch_data())
    assert result["fallback_source"] == "sa_cfs_gis"
    assert len(result["incidents"]) == 1
    assert result["incidents"][0][const.ATTR_INCIDENT_NO] == "F2610020106"
    assert result["incidents"][0][const.ATTR_LATITUDE] == -34.665717
    assert result["incidents"][0][const.ATTR_STATUS] == "Going"


@pytest.mark.parametrize("updated", [1787831280000, None])
def test_sa_map_does_not_report_stale_or_undated_incidents_as_active(updated):
    class OldMap:
        closed = False
        def get(self, url, timeout):
            return Response(200, json.dumps({"features": [{"attributes": {
                "ident": "F2608270132", "event": "Burn Off",
                "inc_status": "Controlled", "updated": updated,
                "lat": -34.8, "long": 138.7,
            }}]}))
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = OldMap()
    with pytest.raises(UpdateFailed, match="stale|timestamp"):
        asyncio.run(obj._fetch_sa_map_data())


def test_sa_map_excludes_stale_record_when_current_record_exists():
    current = int(datetime.now(timezone.utc).timestamp() * 1000)
    class MixedMap:
        closed = False
        def get(self, url, timeout):
            return Response(200, json.dumps({"features": [
                {"attributes": {"ident": "recent", "updated": current, "inc_status": "Going"}},
                {"attributes": {"ident": "old", "updated": 1787831280000, "inc_status": "Controlled"}},
            ]}))
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = MixedMap()
    result = asyncio.run(obj._fetch_sa_map_data())
    assert [item[const.ATTR_INCIDENT_NO] for item in result["incidents"]] == ["recent"]
    assert result["excluded_stale_count"] == 1


def test_sa_rejects_failed_map_fallback_too():
    class BrokenFeeds:
        closed = False
        def get(self, url, timeout):
            return Response(200, "<html>unavailable</html>", "text/html")
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = BrokenFeeds()
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


def test_tas_missing_feed_is_unavailable_not_zero():
    obj = coordinator.IncidentDataCoordinator(None, "TAS", 600)
    obj._session = Session(None)
    with pytest.raises(UpdateFailed):
        asyncio.run(obj._fetch_data())


def test_vic_live_payload_fields():
    item = {"incidentNo": 295949, "incidentType": "ASSIST OTHER AGENCY", "incidentLocation": "BUNYIP", "incidentStatus": "Responding", "latitude": -38.0958, "longitude": 145.7157, "lastUpdateDateTime": "02/10/2026 18:28:00", "agency": "CFA", "feedType": "incident", "name": "PRINCESS ST"}
    obj = coordinator.IncidentDataCoordinator(None, "VIC", 600)
    inc = asyncio.run(obj._parse_vic_data(Response(200, json.dumps({"results": [item]}))))["incidents"][0]
    assert inc[const.ATTR_INCIDENT_NO] == 295949
    assert inc[const.ATTR_LATITUDE] == -38.0958
    assert inc[const.ATTR_LONGITUDE] == 145.7157
    assert inc[const.ATTR_STATUS] == "Responding"
    assert inc[const.ATTR_LOCATION_NAME] == "BUNYIP"
    assert inc[const.ATTR_AGENCY] == "CFA"


def test_nsw_category_is_alert_level():
    props = {"guid": "incident/123", "category": "Watch and Act", "title": "TEST ROAD", "description": "ALERT LEVEL: Watch and Act <br />STATUS: Being controlled"}
    obj = coordinator.IncidentDataCoordinator(None, "NSW", 600)
    inc = asyncio.run(obj._parse_nsw_data(Response(200, json.dumps({"features": [{"properties": props, "geometry": {"type": "Point", "coordinates": [151.0, -32.0]}}]}))))["incidents"][0]
    assert inc[const.ATTR_SEVERITY] == "watch_and_act"
    assert inc[const.ATTR_LEVEL] == "Watch and Act"
    assert inc[const.ATTR_STATUS] == "Being controlled"


def test_one_failed_state_and_cap_do_not_block_healthy_state(monkeypatch):
    class FakeCoord:
        def __init__(self, hass, state, update_seconds):
            self.state = state
            self.name = f"{state} test feed"
        async def async_config_entry_first_refresh(self):
            if self.state == "SA":
                raise ConfigEntryNotReady("feed unavailable")
        async def async_close(self):
            pass
    class Hass:
        def __init__(self):
            self.data = {}
            self.config_entries = self
            self.forwarded = False
        async def async_forward_entry_setups(self, entry, platforms):
            self.forwarded = True
    class Entry:
        entry_id = "test"
        data = {"states": ["SA", "VIC"]}
        options = {}
        def add_update_listener(self, listener):
            return lambda: None
        def async_on_unload(self, callback):
            pass
    monkeypatch.setattr(integration, "IncidentDataCoordinator", FakeCoord)
    monkeypatch.setattr(integration, "CFSCAPDataCoordinator", FakeCoord)
    hass = Hass()
    assert asyncio.run(integration.async_setup_entry(hass, Entry())) is True
    assert hass.forwarded
    assert set(hass.data[const.DOMAIN]["test"]["incident_coordinators"]) == {"SA", "VIC"}
