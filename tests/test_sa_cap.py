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
    assert "F2611240091-2026-11-24T14:37:50+10:30" in ids
