"""Minimal HA interface stubs for fast, offline coordinator regression tests."""
import importlib.util
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Stand-in for HA's configured timezone
LOCAL_TZ = ZoneInfo("Australia/Adelaide")
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "aus_emergency"


def module(name, **attributes):
    obj = types.ModuleType(name)
    obj.__dict__.update(attributes)
    sys.modules[name] = obj
    return obj


class ConfigEntryNotReady(Exception):
    pass


class UpdateFailed(Exception):
    pass


class CoordinatorEntity:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        pass


class GeolocationEvent:
    def async_write_ha_state(self):
        pass


class DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.name = name
        self.update_interval = update_interval
        self.data = None


for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers", "custom_components", "custom_components.aus_emergency"):
    pkg = module(name)
    pkg.__path__ = [str(ROOT)] if name == "custom_components.aus_emergency" else []
module("homeassistant.util", __path__=[], slugify=lambda text: re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_"))
module("homeassistant.core", HomeAssistant=object, ServiceCall=object)
module("homeassistant.config_entries", ConfigEntry=object, ConfigEntryNotReady=ConfigEntryNotReady)
module("homeassistant.const", Platform=types.SimpleNamespace(GEO_LOCATION="geo_location", SENSOR="sensor"))
module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed, CoordinatorEntity=CoordinatorEntity)
module("homeassistant.components.geo_location", GeolocationEvent=GeolocationEvent)
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module("homeassistant.helpers.config_validation", string=str,
       config_entry_only_config_schema=lambda domain: (lambda config: config))
module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
module("homeassistant.helpers.device_registry", async_get=lambda hass: None)
module("homeassistant.helpers.entity_registry", async_get=lambda hass: None)
async def async_get_time_zone(name):
    return ZoneInfo(name)


def parse_datetime(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


module("homeassistant.util.dt", now=lambda: datetime.now(timezone.utc), parse_datetime=parse_datetime,
       as_local=lambda value: value.astimezone(LOCAL_TZ), async_get_time_zone=async_get_time_zone)


def load(name, filename):
    full_name = f"custom_components.aus_emergency.{name}"
    spec = importlib.util.spec_from_file_location(full_name, ROOT / filename)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = obj
    spec.loader.exec_module(obj)
    return obj


utils = load("utils", "utils.py")
const = load("const", "const.py")
coordinator = load("coordinator", "coordinator.py")
cap = load("cap_coordinator", "cap_coordinator.py")
integration = load("integration_under_test", "__init__.py")
geo_location = load("geo_location", "geo_location.py")
