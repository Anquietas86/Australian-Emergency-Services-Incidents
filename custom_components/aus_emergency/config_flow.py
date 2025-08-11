
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN, CONF_STATE, CONF_UPDATE_INTERVAL, CONF_REMOVE_STALE,
    DEFAULT_STATE, DEFAULT_UPDATE_INTERVAL, DEFAULT_REMOVE_STALE
)

STATES = ["SA"]

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=f"Australian Emergency ({user_input[CONF_STATE]})",
                data=user_input
            )

        data_schema = vol.Schema({
            vol.Required(CONF_STATE, default=DEFAULT_STATE): vol.In(STATES),
            vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): int,
            vol.Optional(CONF_REMOVE_STALE, default=DEFAULT_REMOVE_STALE): bool,
        })
        return self.async_show_form(step_id="user", data_schema=data_schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow(config_entry)

class OptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self.entry.data, **(self.entry.options or {})}
        data_schema = vol.Schema({
            vol.Required(CONF_STATE, default=data.get(CONF_STATE, DEFAULT_STATE)): vol.In(STATES),
            vol.Optional(CONF_UPDATE_INTERVAL, default=data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): int,
            vol.Optional(CONF_REMOVE_STALE, default=data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=data_schema)
