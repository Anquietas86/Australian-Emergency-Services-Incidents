"""Minimal HA interface stubs for fast, offline coordinator regression tests."""
import importlib.util
import sys
import types
from pathlib import Path

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


class DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.name = name
        self.update_interval = update_interval
        self.data = None


for name in ("homeassistant", "homeassistant.helpers", "homeassistant.util", "custom_components", "custom_components.aus_emergency"):
    pkg = module(name)
    pkg.__path__ = [str(ROOT)] if name == "custom_components.aus_emergency" else []
module("homeassistant.core", HomeAssistant=object, ServiceCall=object)
module("homeassistant.config_entries", ConfigEntry=object, ConfigEntryNotReady=ConfigEntryNotReady)
module("homeassistant.const", Platform=types.SimpleNamespace(GEO_LOCATION="geo_location", SENSOR="sensor"))
module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed)
module("homeassistant.helpers.config_validation", string=str)
module("homeassistant.helpers.device_registry", async_get=lambda hass: None)
module("homeassistant.helpers.entity_registry", async_get=lambda hass: None)
module("homeassistant.util.dt", now=lambda: None, parse_datetime=lambda value: None, as_local=lambda value: value)


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
