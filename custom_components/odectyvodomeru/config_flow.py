"""Config flow for Odecty vodomeru."""

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.translation import async_get_translations

from .api import (
    Meter,
    PortalAccessRestrictedError,
    PortalAuthenticationError,
    PortalClient,
    PortalConnectionError,
    PortalError,
    PortalRateLimitError,
)
from .const import CONF_METERS, DOMAIN, NAME

LOGIN_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): TextSelector(),
        probatio.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _error_key(error: PortalError) -> str:
    """Map API failures to messages shared by setup and options."""
    if isinstance(error, PortalAccessRestrictedError):
        return "access_restricted"
    if isinstance(error, PortalAuthenticationError):
        return "invalid_auth"
    if isinstance(error, PortalRateLimitError):
        return "rate_limited"
    if isinstance(error, PortalConnectionError):
        return "cannot_connect"
    return "invalid_response"


async def _meter_schema(
    hass: HomeAssistant, meters: dict[str, Meter], selected: list[str]
) -> probatio.Schema:
    """Build a localized multi-select with explicit opt-in and saved defaults."""
    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    options = [
        SelectOptionDict(
            value=meter.id,
            label=" - ".join(
                filter(
                    None,
                    (
                        translations[
                            f"component.{DOMAIN}.selector.meter_type.options."
                            f"{meter.measurement_type}"
                        ],
                        meter.manufacturer,
                        meter.serial_number,
                        f"#{meter.id}",
                    ),
                )
            ),
        )
        for meter in meters.values()
    ]
    return probatio.Schema(
        {
            probatio.Required(CONF_METERS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST
                )
            )
        }
    )


def _selection_error(
    user_input: dict[str, Any], meters: dict[str, Meter]
) -> str | None:
    """Do not persist empty, invalid, or undiscovered selections."""
    selected = user_input.get(CONF_METERS)
    if not isinstance(selected, list) or not selected:
        return "select_meters"
    if any(not isinstance(item, str) or item not in meters for item in selected):
        return "invalid_selection"
    return None


class OdectyVodomeruConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate portal credentials before creating an entry."""

    VERSION = 1

    def __init__(self) -> None:
        """Keep credentials in memory until the user chooses meters."""
        self._credentials: dict[str, str] = {}
        self._meters: dict[str, Meter] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Allow existing accounts to select or change meters."""
        return MeterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup started by the user."""
        return await self._async_step_login("user", user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing account."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace credentials without changing the account."""
        return await self._async_step_login("reauth_confirm", user_input)

    async def _async_step_login(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Share authentication and error handling between setup and reauth."""
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            client = PortalClient(async_get_clientsession(self.hass))
            try:
                account_id = await client.async_authenticate(
                    credentials[CONF_USERNAME], credentials[CONF_PASSWORD]
                )
            except PortalError as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(account_id)
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    entry = self._get_reauth_entry()
                    title = entry.title
                    if title in {
                        NAME,
                        f"{NAME} ({entry.data[CONF_USERNAME].strip()})",
                    }:
                        title = f"{NAME} ({credentials[CONF_USERNAME]})"
                    return self.async_update_reload_and_abort(
                        entry, title=title, data_updates=credentials
                    )
                self._abort_if_unique_id_configured()
                try:
                    self._meters = await client.async_get_meters()
                except PortalError as err:
                    errors["base"] = _error_key(err)
                else:
                    if self._meters:
                        self._credentials = credentials
                        return await self.async_step_meters()
                    errors["base"] = "no_meters"

        return self.async_show_form(
            step_id=step_id, data_schema=LOGIN_SCHEMA, errors=errors
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user which discovered meters to use before creating an entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := _selection_error(user_input, self._meters):
                errors["base"] = error
            else:
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{NAME} ({self._credentials[CONF_USERNAME]})",
                    data=self._credentials,
                    options={CONF_METERS: list(dict.fromkeys(user_input[CONF_METERS]))},
                )
        return self.async_show_form(
            step_id="meters",
            data_schema=await _meter_schema(self.hass, self._meters, []),
            errors=errors,
        )


class MeterOptionsFlow(OptionsFlow):
    """Discover meters again when configuring an existing account."""

    def __init__(self) -> None:
        """Initialize discovery state for this options flow."""
        self._meters: dict[str, Meter] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Load the available meters and save a validated selection."""
        errors: dict[str, str] = {}
        if not self._meters:
            client = PortalClient(async_get_clientsession(self.hass))
            try:
                account_id = await client.async_authenticate(
                    self.config_entry.data[CONF_USERNAME],
                    self.config_entry.data[CONF_PASSWORD],
                )
                if account_id != self.config_entry.unique_id:
                    return self.async_abort(reason="unique_id_mismatch")
                self._meters = await client.async_get_meters()
            except PortalAuthenticationError:
                self.config_entry.async_start_reauth(self.hass)
                return self.async_abort(reason="reauth_required")
            except PortalError as err:
                errors["base"] = _error_key(err)
            else:
                if not self._meters:
                    errors["base"] = "no_meters"
            # A retry submits the empty form, not a meter selection.
            user_input = None

        if self._meters and user_input is not None:
            if error := _selection_error(user_input, self._meters):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        CONF_METERS: list(dict.fromkeys(user_input[CONF_METERS])),
                    },
                )

        selected = [
            meter_id
            for meter_id in self.config_entry.options.get(CONF_METERS, [])
            if meter_id in self._meters
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=(
                await _meter_schema(self.hass, self._meters, selected)
                if self._meters
                else probatio.Schema({})
            ),
            errors=errors,
        )
