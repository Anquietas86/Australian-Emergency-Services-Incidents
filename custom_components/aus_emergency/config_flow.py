from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_STATE,
    CONF_UPDATE_INTERVAL,
    CONF_REMOVE_STALE,
    CONF_ZONES,
    DEFAULT_STATE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_REMOVE_STALE,
    SUPPORTED_STATES,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            # Handle zones - ensure it's a list
            zones = user_input.get(CONF_ZONES, [])
            if isinstance(zones, str):
                zones = [zones] if zones else []

            data = {
                CONF_STATE: user_input[CONF_STATE],
                CONF_UPDATE_INTERVAL: user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                CONF_REMOVE_STALE: user_input.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE),
                CONF_ZONES: zones,
            }

            return self.async_create_entry(
                title=f"Australian Emergency ({user_input[CONF_STATE]})",
                data=data
            )

        data_schema = vol.Schema({
            vol.Required(CONF_STATE, default=DEFAULT_STATE): vol.In(SUPPORTED_STATES),
            vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): int,
            vol.Optional(CONF_REMOVE_STALE, default=DEFAULT_REMOVE_STALE): bool,
            vol.Optional(CONF_ZONES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="zone",
                    multiple=True,
                )
            ),
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
            # Handle zones - ensure it's a list
            zones = user_input.get(CONF_ZONES, [])
            if isinstance(zones, str):
                zones = [zones] if zones else []

            return self.async_create_entry(
                title="",
                data={
                    CONF_STATE: user_input[CONF_STATE],
                    CONF_UPDATE_INTERVAL: user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                    CONF_REMOVE_STALE: user_input.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE),
                    CONF_ZONES: zones,
                }
            )

        # Merge data and options for defaults
        data = {**self.entry.data, **(self.entry.options or {})}

        data_schema = vol.Schema({
            vol.Required(
                CONF_STATE,
                default=data.get(CONF_STATE, DEFAULT_STATE)
            ): vol.In(SUPPORTED_STATES),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ): int,
            vol.Optional(
                CONF_REMOVE_STALE,
                default=data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE)
            ): bool,
            vol.Optional(
                CONF_ZONES,
                default=data.get(CONF_ZONES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="zone",
                    multiple=True,
                )
            ),
        })
        return self.async_show_form(step_id="init", data_schema=data_schema)
