from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_STATE,
    CONF_STATES,
    CONF_UPDATE_INTERVAL,
    CONF_REMOVE_STALE,
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_ZONES,
    CONF_RADIUS,
    CONF_ZONE_BUFFER,
    CONF_ALERT_RADIUS,
    CONF_ALERT_MIN_SEVERITY,
    DEFAULT_STATE,
    DEFAULT_STATES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_REMOVE_STALE,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
    DEFAULT_RADIUS,
    DEFAULT_ZONE_BUFFER,
    DEFAULT_ALERT_RADIUS,
    DEFAULT_ALERT_MIN_SEVERITY,
    MAX_RADIUS_KM,
    SEVERITY_ORDER,
    MIN_UPDATE_INTERVAL,
    MAX_UPDATE_INTERVAL,
    SELECTABLE_STATES,
)

UPDATE_INTERVAL_VALIDATOR = vol.All(
    vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL)
)


def _km_selector(minimum: float = 0) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=MAX_RADIUS_KM,
            step=1,
            unit_of_measurement="km",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


SEVERITY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=SEVERITY_ORDER,
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="severity",
    )
)


def _proximity_schema(data: dict) -> dict:
    """Distance-related fields shared by the config and options flows."""
    return {
        vol.Optional(
            CONF_RADIUS, default=data.get(CONF_RADIUS, DEFAULT_RADIUS)
        ): _km_selector(),
        vol.Optional(
            CONF_ALERT_RADIUS, default=data.get(CONF_ALERT_RADIUS, DEFAULT_ALERT_RADIUS)
        ): _km_selector(1),
        vol.Optional(
            CONF_ALERT_MIN_SEVERITY,
            default=data.get(CONF_ALERT_MIN_SEVERITY, DEFAULT_ALERT_MIN_SEVERITY),
        ): SEVERITY_SELECTOR,
        vol.Optional(
            CONF_ZONE_BUFFER, default=data.get(CONF_ZONE_BUFFER, DEFAULT_ZONE_BUFFER)
        ): _km_selector(),
    }


def _proximity_data(user_input: dict) -> dict:
    """Read the distance fields, falling back to defaults."""
    return {
        CONF_RADIUS: user_input.get(CONF_RADIUS, DEFAULT_RADIUS),
        CONF_ALERT_RADIUS: user_input.get(CONF_ALERT_RADIUS, DEFAULT_ALERT_RADIUS),
        CONF_ALERT_MIN_SEVERITY: user_input.get(CONF_ALERT_MIN_SEVERITY, DEFAULT_ALERT_MIN_SEVERITY),
        CONF_ZONE_BUFFER: user_input.get(CONF_ZONE_BUFFER, DEFAULT_ZONE_BUFFER),
    }


def _states_selector(current: list[str] | None = None) -> selector.SelectSelector:
    """Offer working states, plus any retired state an existing entry still uses."""
    options = list(SELECTABLE_STATES)
    options.extend(s for s in current or [] if s not in options)
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # Handle zones - ensure it's a list
            zones = user_input.get(CONF_ZONES, [])
            if isinstance(zones, str):
                zones = [zones] if zones else []

            # Handle states - ensure it's a list
            states = user_input.get(CONF_STATES, [])
            if isinstance(states, str):
                states = [states] if states else []
            if not states:
                states = DEFAULT_STATES

            data = {
                CONF_STATES: states,
                CONF_UPDATE_INTERVAL: user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                CONF_REMOVE_STALE: user_input.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE),
                CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
                CONF_ZONES: zones,
                **_proximity_data(user_input),
            }

            return self.async_create_entry(
                title="Australian Emergency Services",
                data=data
            )

        data_schema = vol.Schema({
            vol.Required(CONF_STATES, default=DEFAULT_STATES): _states_selector(),
            vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): UPDATE_INTERVAL_VALIDATOR,
            vol.Optional(CONF_REMOVE_STALE, default=DEFAULT_REMOVE_STALE): bool,
            vol.Optional(CONF_EXPOSE_TO_ASSISTANTS, default=DEFAULT_EXPOSE_TO_ASSISTANTS): bool,
            vol.Optional(CONF_ZONES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="zone",
                    multiple=True,
                )
            ),
            **_proximity_schema({}),
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

            # Handle states - ensure it's a list
            states = user_input.get(CONF_STATES, [])
            if isinstance(states, str):
                states = [states] if states else []
            if not states:
                states = DEFAULT_STATES

            return self.async_create_entry(
                title="",
                data={
                    CONF_STATES: states,
                    CONF_UPDATE_INTERVAL: user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                    CONF_REMOVE_STALE: user_input.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE),
                    CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
                    CONF_ZONES: zones,
                    **_proximity_data(user_input),
                }
            )

        # Merge data and options for defaults
        data = {**self.entry.data, **(self.entry.options or {})}

        # Handle migration from single state to multi-state
        default_states = data.get(CONF_STATES)
        if not default_states:
            # Migrate from old single-state config
            old_state = data.get(CONF_STATE, DEFAULT_STATE)
            default_states = [old_state] if old_state else DEFAULT_STATES

        data_schema = vol.Schema({
            vol.Required(
                CONF_STATES,
                default=default_states
            ): _states_selector(default_states),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ): UPDATE_INTERVAL_VALIDATOR,
            vol.Optional(
                CONF_REMOVE_STALE,
                default=data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE)
            ): bool,
            vol.Optional(
                CONF_EXPOSE_TO_ASSISTANTS,
                default=data.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS)
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
            **_proximity_schema(data),
        })
        return self.async_show_form(step_id="init", data_schema=data_schema)
