"""SA AlertSA CAP feed: Atom-wrapped CAP, warnings vs routine incident notices.

The sample mirrors the live combined-feed.alert.sa.gov.au layout (Nov 2026).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import cap, coordinator, const, UpdateFailed

NOW = datetime.now(timezone(timedelta(hours=10, minutes=30)))
SENT = NOW.replace(microsecond=0).isoformat()


def _entry(identifier, warning_level, status="Going", area="", name="BACK VALLEY"):
    return f"""
  <entry><id>{identifier}</id>
    <content type="text/xml"><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
      <identifier>{identifier}</identifier><sent>{SENT}</sent><status>Actual</status>
      <info><category>Fire</category><event>Grass Fire</event><severity>Unknown</severity>
        <headline>{name} : GRASS FIRE</headline>
        <parameter><valueName>IncidentName</valueName><value>{name}</value></parameter>
        <parameter><valueName>Status</valueName><value>{status}</value></parameter>
        <parameter><valueName>WarningLevel</valueName><value>{warning_level}</value></parameter>
        <area><areaDesc>{name}</areaDesc>{area}<circle>-35.526807,138.431979 0</circle></area>
      </info></alert></content></entry>"""


POLYGON = "<polygon>-35.5,138.4 -35.5,138.5 -35.6,138.5 -35.5,138.4</polygon>"

ATOM_FEED = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:georss="http://www.georss.org/georss">
  <id>https://www.cfs.sa.gov.au</id>
  {_entry("F2611240091-2026-11-24T14:37:50+10:30", "Incident")}
  {_entry("F2611240091-2026-11-27T10:06:04+10:30-20261127101949", "Update")}
  {_entry("W1-2026-11-27T12:00:00+10:30", "Watch and Act", area=POLYGON, name="MILTALIE")}
</feed>"""


class Response:
    def __init__(self, status, body, content_type):
        self.status, self.body, self.content_type = status, body, content_type

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

    def __init__(self, body, content_type="application/atom+xml"):
        self.response = Response(200, body, content_type)

    def get(self, url, timeout):
        return self.response


def test_cap_coordinator_reads_atom_wrapped_alerts_and_keeps_only_warnings():
    obj = cap.CFSCAPDataCoordinator(None, "SA", 600)
    obj._session = Session(ATOM_FEED)
    alerts = asyncio.run(obj._fetch_data())["alerts"]
    assert [a["id"] for a in alerts] == ["W1-2026-11-27T12:00:00+10:30"]
    assert alerts[0]["warning_level"] == "Watch and Act"
    assert alerts[0]["areas"][0]["polygon"]


def test_cap_coordinator_still_reads_bare_cap_alerts():
    xml = """<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
      <identifier>A1</identifier><info><event>Bushfire</event>
      <area><areaDesc>Somewhere</areaDesc></area></info></alert>"""
    obj = cap.CFSCAPDataCoordinator(None, "SA", 600)
    obj._session = Session(xml, "application/xml")
    assert [a["id"] for a in asyncio.run(obj._fetch_data())["alerts"]] == ["A1"]


def test_empty_atom_feed_is_a_quiet_day():
    obj = cap.CFSCAPDataCoordinator(None, "SA", 600)
    obj._session = Session('<feed xmlns="http://www.w3.org/2005/Atom"><id>x</id></feed>')
    assert asyncio.run(obj._fetch_data()) == {"alerts": []}


def test_unrecognised_cap_layout_is_a_failure_not_zero_alerts():
    obj = cap.CFSCAPDataCoordinator(None, "SA", 600)
    obj._session = Session("<rss><channel><item/></channel></rss>", "application/xml")
    with pytest.raises(UpdateFailed, match="layout"):
        asyncio.run(obj._fetch_data())


def test_sa_incident_merge_leaves_warnings_to_cap_entities():
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    ids = [i[const.ATTR_INCIDENT_NO] for i in obj._parse_sa_cap(ATOM_FEED)["incidents"]]
    assert "W1-2026-11-27T12:00:00+10:30" not in ids
    assert ids == ["F2611240091"]


def _feed(*entries):
    return ('<feed xmlns="http://www.w3.org/2005/Atom"><id>x</id>'
            + "".join(entries) + "</feed>")


def _sent_entry(identifier, sent, status="Going", warning_level="Incident"):
    return _entry(identifier, warning_level, status=status).replace(SENT, sent)


def test_sa_cap_keys_incidents_by_base_id_and_keeps_newest_message():
    older = (NOW - timedelta(hours=2)).replace(microsecond=0).isoformat()
    feed = _feed(
        _sent_entry("F2611240091-2026-11-24T14:37:50+10:30", older),
        _sent_entry("F2611240091-2026-11-27T10:06:04+10:30-20261127101949", SENT, status="Contained"),
    )
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    incidents = obj._parse_sa_cap(feed)["incidents"]
    assert [i[const.ATTR_INCIDENT_NO] for i in incidents] == ["F2611240091"]
    assert incidents[0][const.ATTR_STATUS] == "Contained"
    assert incidents[0][const.ATTR_TYPE] == "Grass Fire"


def test_sa_cap_skips_finished_jobs():
    feed = _feed(_entry("699308", "Incident", status="Completed"), _entry("699413", "Incident"))
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    assert [i[const.ATTR_INCIDENT_NO] for i in obj._parse_sa_cap(feed)["incidents"]] == ["699413"]


def _feature(ident):
    return {"attributes": {
        "ident": ident, "inc_status": "Going", "lat": -35.0, "long": 138.0,
        "updated": int(datetime.now(timezone.utc).timestamp() * 1000),
    }}


class SAFeeds:
    closed = False

    def __init__(self, features, cap):
        self.features, self.cap = features, cap

    def get(self, url, timeout):
        if "alert.sa.gov.au" in url:
            if isinstance(self.cap, BaseException):
                raise self.cap
            return Response(200, self.cap, "application/atom+xml")
        return Response(200, json.dumps({"features": self.features}), "application/json")


def _sa(features, cap_feed):
    obj = coordinator.IncidentDataCoordinator(None, "SA", 600)
    obj._session = SAFeeds(features, cap_feed)
    return obj


def test_sa_merge_adds_only_cap_incidents_missing_from_featureserver():
    feed = _feed(_entry("F2611240091-2026-11-24T14:37:50+10:30", "Incident"), _entry("699413", "Incident"))
    result = asyncio.run(_sa([_feature("F2611240091")], feed)._fetch_data())
    assert sorted(i[const.ATTR_INCIDENT_NO] for i in result["incidents"]) == ["699413", "F2611240091"]


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("boom"), RuntimeError("defused")])
def test_sa_cap_failure_falls_back_to_featureserver(error):
    result = asyncio.run(_sa([_feature("F1")], error)._fetch_data())
    assert [i[const.ATTR_INCIDENT_NO] for i in result["incidents"]] == ["F1"]


def test_sa_stale_featureserver_is_confirmed_by_cap():
    stale = {"attributes": {"ident": "OLD", "updated": 1787831280000}}
    result = asyncio.run(_sa([stale], _feed(_entry("699413", "Incident")))._fetch_data())
    assert [i[const.ATTR_INCIDENT_NO] for i in result["incidents"]] == ["699413"]


def test_sa_stale_featureserver_without_cap_is_a_failure():
    stale = {"attributes": {"ident": "OLD", "updated": 1787831280000}}
    with pytest.raises(UpdateFailed, match="stale"):
        asyncio.run(_sa([stale], TimeoutError())._fetch_data())
