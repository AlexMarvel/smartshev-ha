"""Config flow for automatic discovery or manual ShevLogger setup."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    ShevLoggerApi,
    ShevLoggerAuthError,
    ShevLoggerCloudApi,
    ShevLoggerConnectionError,
    ShevLoggerError,
    normalize_host,
)
from .const import CLOUD_URL, CONF_CLOUD_TOKEN, CONF_TOKEN, DOMAIN


def _token_selector() -> TextSelector:
    """Keep both existing and newly entered credentials out of form defaults."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _device_info(api: ShevLoggerApi) -> dict[str, Any]:
    info = await api.async_get_state()
    device = info.get("device")
    if (
        not isinstance(device, dict)
        or not isinstance(device.get("id"), str)
        or not device["id"].strip()
    ):
        raise ShevLoggerConnectionError("Missing ShevLogger gateway identity")
    return device


class ShevLoggerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create a config entry without requiring a fixed gateway IP."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._cloud_ticket = ""
        self._cloud_devices: dict[str, str] = {}
        self._cloud_recovery: str | None = None

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        properties = discovery_info.properties
        device_id = properties.get("id")
        if not device_id:
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(device_id)
        self._abort_existing_lan(device_id, discovery_info.host)
        self._discovered_host = discovery_info.host
        self._discovered_name = properties.get("name") or discovery_info.name
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._discovered_host:
            return await self._validate_and_create(
                self._discovered_host, user_input[CONF_TOKEN], errors, "confirm"
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _token_selector()}),
            errors=errors,
            description_placeholders={"name": self._discovered_name or "SmartShev"},
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["lan", "cloud"])

    async def async_step_lan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self._validate_and_create(
                user_input[CONF_HOST], user_input[CONF_TOKEN], errors, "lan"
            )
        return self._manual_form(errors)

    def _manual_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="lan",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_TOKEN): _token_selector(),
                }
            ),
            errors=errors,
        )

    def _abort_existing_lan(self, device_id: str, host: str) -> None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id == device_id and CONF_CLOUD_TOKEN in entry.data:
                self._abort_if_unique_id_configured()
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._cloud_ticket = ""
            self._cloud_devices = {}
            client = ShevLoggerCloudApi(async_get_clientsession(self.hass))
            try:
                response = await client.async_exchange(user_input["code"])
                ticket = response.get("ticket")
                devices = response.get("devices")
                if not isinstance(ticket, str) or not ticket or not isinstance(devices, list):
                    raise ShevLoggerConnectionError("Invalid SmartShev device list")
                self._cloud_devices = {
                    device["id"]: (
                        device["id"] if device["name"] == device["id"]
                        else f"{device['name']} · {device['id']}"
                    )
                    for device in devices
                    if isinstance(device, dict) and isinstance(device.get("id"), str)
                    and isinstance(device.get("name"), str)
                }
            except ShevLoggerAuthError as error:
                errors["base"] = error.reason
            except ShevLoggerConnectionError:
                errors["base"] = "cloud_unreachable"
            else:
                if self._cloud_recovery:
                    entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                    if entry is None or entry.unique_id not in self._cloud_devices:
                        errors["base"] = "device_access_denied"
                    else:
                        self._cloud_devices = {entry.unique_id: self._cloud_devices[entry.unique_id]}
                elif not self._cloud_devices:
                    errors["base"] = "no_cloud_devices"
                if not errors:
                    self._cloud_ticket = ticket
                    return await self.async_step_cloud_device()
        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema({vol.Required("code"): _token_selector()}),
            description_placeholders={"account_url": f"{CLOUD_URL}/account/integrations"},
            errors=errors,
        )

    async def async_step_cloud_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._cloud_ticket:
            return await self.async_step_cloud()
        if user_input is not None and user_input["device"] in self._cloud_devices:
            device_id = user_input["device"]
            entry = None
            if self._cloud_recovery:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                if entry is None or entry.unique_id != device_id or CONF_CLOUD_TOKEN not in entry.data:
                    return self.async_abort(reason="entry_changed")
                original_data = entry.data
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
            client = ShevLoggerCloudApi(async_get_clientsession(self.hass), self._cloud_ticket)
            self._cloud_ticket = ""
            try:
                token = await client.async_connect(device_id)
            except ShevLoggerError:
                return self.async_abort(reason="cloud_link_failed")
            if entry is not None:
                if self.hass.config_entries.async_get_entry(entry.entry_id) is not entry or entry.data != original_data:
                    return self.async_abort(reason="entry_changed")
                return self.async_update_reload_and_abort(
                    entry, data={CONF_CLOUD_TOKEN: token}, reason=self._cloud_recovery,
                )
            return self.async_create_entry(
                title=self._cloud_devices[device_id], data={CONF_CLOUD_TOKEN: token},
            )
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(self._cloud_devices)}),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the address and optionally replace the existing LAN token."""
        return await self._update_connection("reconfigure", user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start recovery for the existing entry after a 401/403 response."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require a freshly entered token and allow correcting the address."""
        return await self._update_connection("reauth_confirm", user_input)

    async def _update_connection(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        # Use the entry lookup/update API available since our minimum HA version.
        # Never create a new entry or change its identity, options or entity IDs.
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason="entry_not_found")
        if not entry.unique_id:
            return self.async_abort(reason="missing_identity")
        if CONF_CLOUD_TOKEN in entry.data:
            self._cloud_recovery = (
                "reauth_successful" if step_id == "reauth_confirm" else "reconfigure_successful"
            )
            return await self.async_step_cloud()

        errors: dict[str, str] = {}
        host = entry.data[CONF_HOST]
        token_required = step_id == "reauth_confirm"
        if user_input is not None:
            host = user_input[CONF_HOST]
            submitted_token = user_input.get(CONF_TOKEN, "").strip()
            token = submitted_token or (
                "" if token_required else entry.data[CONF_TOKEN]
            )
            if not token:
                errors[CONF_TOKEN] = "token_required"
            else:
                original_data = entry.data
                try:
                    normalized_host = normalize_host(host)
                    device = await _device_info(
                        ShevLoggerApi(
                            async_get_clientsession(self.hass), normalized_host, token
                        )
                    )
                except ShevLoggerAuthError as error:
                    errors["base"] = error.reason
                except ShevLoggerConnectionError:
                    errors["base"] = "cannot_connect"
                except ValueError:
                    errors[CONF_HOST] = "invalid_host"
                else:
                    if device["id"] != entry.unique_id:
                        errors["base"] = "wrong_device"
                    elif (
                        self.hass.config_entries.async_get_entry(entry.entry_id)
                        is not entry
                        or entry.data != original_data
                    ):
                        return self.async_abort(reason="entry_changed")
                    else:
                        return self.async_update_reload_and_abort(
                            entry,
                            data={
                                **entry.data,
                                CONF_HOST: normalized_host,
                                CONF_TOKEN: token,
                            },
                            reason=(
                                "reauth_successful"
                                if token_required
                                else "reconfigure_successful"
                            ),
                        )

        token_field = (
            vol.Required(CONF_TOKEN) if token_required else vol.Optional(CONF_TOKEN)
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host): str,
                    token_field: _token_selector(),
                }
            ),
            errors=errors,
        )

    async def _validate_and_create(
        self,
        host: str,
        token: str,
        errors: dict[str, str],
        step_id: str,
    ) -> ConfigFlowResult:
        try:
            normalized_host = normalize_host(host)
            device = await _device_info(
                ShevLoggerApi(
                    async_get_clientsession(self.hass), normalized_host, token
                )
            )
            device_id = device["id"]
        except ShevLoggerAuthError as error:
            errors["base"] = error.reason
        except ShevLoggerConnectionError:
            errors["base"] = "cannot_connect"
        except ValueError:
            errors["base"] = "invalid_host"
        else:
            await self.async_set_unique_id(device_id)
            self._abort_existing_lan(device_id, normalized_host)
            return self.async_create_entry(
                title=str(device.get("name") or "SmartShev"),
                data={CONF_HOST: normalized_host, CONF_TOKEN: token.strip()},
            )

        if step_id == "confirm":
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({vol.Required(CONF_TOKEN): _token_selector()}),
                errors=errors,
                description_placeholders={
                    "name": self._discovered_name or "SmartShev"
                },
            )
        return self._manual_form(errors)
