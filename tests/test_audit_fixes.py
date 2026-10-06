"""Regressions for the 2026-10 code audit fixes."""
import asyncio
import json
from datetime import timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from conftest import coordinator, const, geo_location, integration
from test_feeds import Response
from test_geo_cleanup import Coordinator, Entry, Registry, entry


# --- timestamps -------------------------------------------------------------

def test_nsw_pubdate_is_parsed_as_utc():
    # Live feed: pubDate "5/10/2026 4:15:00 AM" alongside "UPDATED: 5 Oct 2026 15:15" (AEDT)
    parsed = coordinator._parse_incident_datetime("5/10/2026 4:15:00 AM", None, timezone.utc)
    sydney = parsed.astimezone(ZoneInfo("Australia/Sydney"))
    assert (sydney.day, sydney.hour, sydney.minute) == (5, 15, 15)


def test_naive_local_time_uses_provider_timezone():
    parsed = coordinator._parse_incident_datetime(
        "06/10/2026 16:31:00", None, ZoneInfo("Australia/Melbourne"))
    assert parsed.tzinfo is not None
    melbourne = parsed.astimezone(ZoneInfo("Australia/Melbourne"))
    assert (melbourne.hour, melbourne.minute) == (16, 31)


def test_trailing_z_is_utc_not_local():
    parsed = coordinator._parse_incident_datetime(
        "2026-10-06T05:00:00Z", None, ZoneInfo("Australia/Perth"))
    assert parsed.astimezone(timezone.utc).hour == 5


def test_nsw_parser_fills_incident_datetime():
    props = {"guid": "https://incidents.rfs.nsw.gov.au/api/v1/incidents/680191",
             "category": "Advice", "pubDate": "5/10/2026 4:15:00 AM"}
    obj = coordinator.IncidentDataCoordinator(None, "NSW", 600)
    body = json.dumps({"features": [{"properties": props,
                                     "geometry": {"type": "Point", "coordinates": [151.0, -32.0]}}]})
    inc = asyncio.run(obj._parse_nsw_data(Response(200, body)))["incidents"][0]
    assert inc[const.ATTR_INCIDENT_DATETIME] is not None


# --- coordinates ------------------------------------------------------------

def test_coordinates_are_coerced_to_float():
    class Session:
        closed = False
        def get(self, url, timeout):
            return Response(200, json.dumps({"features": [
                {"properties": {"UniqueID": "a", "Latitude": "-27.5", "Longitude": "153.0"}},
                {"properties": {"UniqueID": "b"}, "geometry": {"type": "Point", "coordinates": [153.0]}},
            ]}), "binary/octet-stream")
    obj = coordinator.IncidentDataCoordinator(None, "QLD", 600)
    obj._session = Session()
    first, second = asyncio.run(obj._fetch_data())["incidents"]
    assert (first[const.ATTR_LATITUDE], first[const.ATTR_LONGITUDE]) == (-27.5, 153.0)
    assert second[const.ATTR_LATITUDE] is None


# --- CAP geometry -----------------------------------------------------------

def cap_entity(alert):
    coord = SimpleNamespace(data={"alerts": [alert]})
    return geo_location.CAPAlertGeolocation(SimpleNamespace(), coord, Entry(), alert["id"], state_code="SA")


def test_cap_polygon_with_newlines_and_bad_pairs_does_not_crash():
    ent = cap_entity({"id": "x", "areas": [{"areaDesc": "Hills",
                                            "polygon": ["-34.0,138.0\n   -36.0,140.0  junk -35.0,139.0,0"]}]})
    assert (ent.latitude, ent.longitude) == (-35.0, 139.0)


def test_cap_circle_center_is_used():
    ent = cap_entity({"id": "x", "areas": [{"circle": ["-34.5,138.5 10"]}]})
    assert (ent.latitude, ent.longitude) == (-34.5, 138.5)


def test_cap_alert_without_area_has_a_name():
    ent = cap_entity({"id": "x", "event": "Bushfire", "areas": []})
    assert ent.name == "Bushfire for Unknown Area"


# --- entity lifecycle -------------------------------------------------------

def setup_incidents(monkeypatch, registered=(), remove_stale=True):
    registry = Registry([entry(f"geo_location.{uid}") for uid in registered])
    monkeypatch.setattr(geo_location.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(geo_location.er, "async_entries_for_config_entry",
                        lambda r, config_id: [e for e in r.entries if e.config_entry_id == config_id],
                        raising=False)
    coord = Coordinator()
    hass = SimpleNamespace(events=[], config=SimpleNamespace(latitude=-35.0, longitude=138.0))
    hass.bus = SimpleNamespace(async_fire=lambda kind, payload: hass.events.append(kind))
    added, ent_entry = [], Entry()
    geo_location._setup_incident_entities(
        hass, ent_entry, lambda entities, **kw: added.extend(entities),
        coord, {}, [], remove_stale, True, "SA")
    return coord, hass, added, ent_entry


def test_restart_does_not_reannounce_known_incident(monkeypatch):
    _, hass, added, _ = setup_incidents(monkeypatch, registered=["aus_emergency_sa_live"])
    assert const.EVENT_CREATED not in hass.events
    assert added[0].expose_on_add is False


def test_new_incident_is_announced_and_exposed_once_registered(monkeypatch):
    _, hass, added, _ = setup_incidents(monkeypatch)
    assert hass.events == [const.EVENT_CREATED]
    assert added[0].expose_on_add is True


def test_listener_is_released_on_unload(monkeypatch):
    _, _, _, ent_entry = setup_incidents(monkeypatch)
    assert len(ent_entry.unload_callbacks) == 1


def test_keep_stale_mode_marks_ended_incident_and_restores_it(monkeypatch):
    coord, hass, added, _ = setup_incidents(monkeypatch, remove_stale=False)
    entity = added[0]
    live = coord.data
    writes = []
    entity.async_write_ha_state = lambda: writes.append(entity.available)

    coord.data = {"incidents": []}
    coord.callback()
    assert not entity.available and writes == [False]
    assert hass.events[-1] == const.EVENT_REMOVED

    coord.callback()  # still gone: no duplicate removal event
    assert hass.events.count(const.EVENT_REMOVED) == 1

    coord.data = live
    coord.callback()
    assert entity.available and writes[-1] is True
    assert len(added) == 1  # same entity, no duplicate unique_id
    assert hass.events[-1] == const.EVENT_CREATED


# --- setup / unload ---------------------------------------------------------

class FakeCoord:
    created = []

    def __init__(self, hass, state, update_seconds):
        self.name = state
        FakeCoord.created.append(update_seconds)

    async def async_config_entry_first_refresh(self):
        pass

    async def async_close(self):
        pass


class Hass:
    def __init__(self):
        self.data = {}
        self.config_entries = self
        self.removed_services = []
        self.services = SimpleNamespace(async_remove=lambda *a: self.removed_services.append(a))

    async def async_forward_entry_setups(self, entry, platforms):
        pass

    async def async_unload_platforms(self, entry, platforms):
        return True


class ConfigEntry:
    entry_id = "test"
    options = {}

    def __init__(self, interval):
        self.data = {"states": ["VIC"], "update_interval": interval}

    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, callback):
        pass


def test_invalid_stored_interval_is_clamped(monkeypatch):
    monkeypatch.setattr(integration, "IncidentDataCoordinator", FakeCoord)
    FakeCoord.created.clear()
    asyncio.run(integration.async_setup_entry(Hass(), ConfigEntry(0)))
    assert FakeCoord.created == [const.MIN_UPDATE_INTERVAL]


def test_unloading_last_entry_keeps_services(monkeypatch):
    monkeypatch.setattr(integration, "IncidentDataCoordinator", FakeCoord)
    hass, cfg = Hass(), ConfigEntry(600)
    asyncio.run(integration.async_setup_entry(hass, cfg))
    assert asyncio.run(integration.async_unload_entry(hass, cfg)) is True
    assert hass.removed_services == []
