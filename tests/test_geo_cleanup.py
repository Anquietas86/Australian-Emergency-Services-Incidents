"""Incident entity registry cleanup must not erase live entities or failed feeds."""
from types import SimpleNamespace

from conftest import const, geo_location


class Registry:
    def __init__(self, entries):
        self.entries = entries
        self.removed = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)

    def async_get(self, entity_id):
        return next((e for e in self.entries if e.entity_id == entity_id), None)

    def async_get_entity_id(self, domain, platform, unique_id):
        return next((e.entity_id for e in self.entries
                     if e.unique_id == unique_id and e.entity_id.startswith(f"{domain}.")), None)


class Coordinator:
    source = "sa_cfs_gis"

    def __init__(self, success=True):
        self.last_update_success = success
        self.data = {"incidents": [{
            const.ATTR_INCIDENT_NO: "LIVE", const.ATTR_LATITUDE: -35.0,
            const.ATTR_LONGITUDE: 138.0, const.ATTR_STATUS: "Going",
        }]}
        self.callback = None

    def async_add_listener(self, callback):
        self.callback = callback
        return lambda: None


class Hass:
    def __init__(self):
        self.events = []
        self.bus = SimpleNamespace(async_fire=lambda kind, payload: self.events.append((kind, payload)))
        self.config = SimpleNamespace(latitude=-35.0, longitude=138.0)


class Entry:
    entry_id = "current"

    def __init__(self):
        self.unload_callbacks = []

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)


def entry(entity_id, *, config_id="current", platform="aus_emergency"):
    return SimpleNamespace(entity_id=entity_id, unique_id=entity_id.split(".", 1)[-1],
                           config_entry_id=config_id, platform=platform)


def prepare(monkeypatch, *, success=True, remove_stale=True):
    entries = [
        entry("geo_location.aus_emergency_sa_old"),
        entry("geo_location.aus_emergency_sa_live"),
        entry("geo_location.aus_emergency_sa_cap_current"),
        entry("geo_location.aus_emergency_nsw_old"),
        entry("sensor.sa_active_incidents"),
        entry("geo_location.aus_emergency_sa_someone_else", config_id="other"),
    ]
    registry = Registry(entries)
    monkeypatch.setattr(geo_location.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(geo_location.er, "async_entries_for_config_entry",
                        lambda r, config_id: [e for e in r.entries if e.config_entry_id == config_id], raising=False)
    coord, hass, added = Coordinator(success), Hass(), []
    geo_location._setup_incident_entities(
        hass, Entry(), lambda entities, **kw: added.extend(entities),
        coord, {}, [], remove_stale, False, "SA"
    )
    return registry, coord, added, hass


def test_default_enables_stale_cleanup():
    assert const.DEFAULT_REMOVE_STALE is True


def test_startup_prunes_only_orphans_owned_by_this_entry_and_state(monkeypatch):
    registry, coordinator, added, _ = prepare(monkeypatch)
    assert [e.entity_id for e in added] == ["geo_location.aus_emergency_sa_live"]
    assert registry.removed == ["geo_location.aus_emergency_sa_old"]


def test_no_registry_cleanup_on_feed_failure(monkeypatch):
    registry, _, _, _ = prepare(monkeypatch, success=False)
    assert registry.removed == []


def test_explicit_opt_out_preserves_old_registry_entries(monkeypatch):
    registry, _, _, _ = prepare(monkeypatch, remove_stale=False)
    assert registry.removed == []


def test_later_feed_failure_does_not_prune_registry(monkeypatch):
    registry, coordinator, _, _ = prepare(monkeypatch)
    registry.removed.clear()
    registry.entries.append(entry("geo_location.aus_emergency_sa_later"))
    coordinator.last_update_success = False
    coordinator.callback()
    assert registry.removed == []


def test_live_geolocation_becomes_unavailable_on_feed_failure_then_recovers(monkeypatch):
    _, coordinator, added, _ = prepare(monkeypatch)
    entity = added[0]
    assert entity.available
    coordinator.last_update_success = False
    coordinator.callback()
    assert not entity.available
    coordinator.last_update_success = True
    coordinator.callback()
    assert entity.available
