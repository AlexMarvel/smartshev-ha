"""Config flow for automatic discovery or manual ShevLogger setup."""

from __future__ import annotations

import re
from collections.abc import Mapping
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Any

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
from .api import (
    ShevLoggerApi,
    ShevLoggerAuthError,
    ShevLoggerCloudApi,
    ShevLoggerConnectionError,
    ShevLoggerError,
    normalize_host,
)
from .const import CLOUD_URL, CONF_CLOUD_TOKEN, CONF_TOKEN, DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo


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
        self._cloud_recovery: str | None = None
        self._account_link: tuple[str, str] | None = None
        self._account_ticket = ""
        self._account_devices: dict[str, tuple[str, list[str]]] = {}
        self._account_device = ""
        self._account_lan_token = ""

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
        return self.async_show_menu(step_id="user", menu_options=["account", "lan"])

    async def async_step_account(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        client = ShevLoggerCloudApi(async_get_clientsession(self.hass))
        if user_input is not None and self._account_link:
            try:
                payload = await client.async_poll_account_link(self._account_link[0])
            except ShevLoggerAuthError:
                self._account_link = None
                errors["base"] = "account_link_expired"
            except ShevLoggerConnectionError:
                errors["base"] = "cloud_unreachable"
            else:
                if payload.get("pending") is True:
                    errors["base"] = "account_link_pending"
                else:
                    ticket, devices = payload.get("ticket"), payload.get("devices")
                    if not isinstance(ticket, str) or not ticket or not isinstance(devices, list):
                        return self.async_abort(reason="cloud_link_failed")
                    self._account_link = None
                    self._account_devices = {
                        device["id"]: (
                            device["name"] if device["name"] == device["id"]
                            else f"{device['name']} · {device['id']}",
                            [mode for mode in ("lan", "cloud") if device.get(mode) is True],
                        )
                        for device in devices
                        if isinstance(device, dict) and isinstance(device.get("id"), str)
                        and device["id"] and isinstance(device.get("name"), str)
                        and (device.get("lan") is True or device.get("cloud") is True)
                    }
                    if not self._account_devices:
                        return self.async_abort(reason="no_account_gateways")
                    self._account_ticket = ticket
                    if self._cloud_recovery:
                        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                        device = self._account_devices.get(entry.unique_id) if entry else None
                        if not device or "cloud" not in device[1]:
                            return self.async_abort(reason="cloud_link_failed")
                        self._account_device = entry.unique_id
                        return await self.async_step_account_cloud()
                    return await self.async_step_account_device()
        if not self._account_link:
            try:
                payload = await client.async_begin_account_link()
                code, approval = payload.get("code"), payload.get("approval")
                if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{32}", value)
                           for value in (code, approval)):
                    raise ShevLoggerConnectionError("Invalid SmartShev login link")
                self._account_link = (code, approval)
            except ShevLoggerError:
                return self.async_abort(reason="account_link_unavailable")
        return self.async_show_form(
            step_id="account", data_schema=vol.Schema({}), errors=errors,
            description_placeholders={
                "account_url": f"{CLOUD_URL}/account/connect-home-assistant#{self._account_link[1]}",
            },
        )

    async def async_step_account_device(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None and user_input["device"] in self._account_devices:
            self._account_device = user_input["device"]
            return await self.async_step_account_mode()
        return self.async_show_form(
            step_id="account_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In({
                device_id: label for device_id, (label, _) in self._account_devices.items()
            })}),
        )

    async def async_step_account_mode(self, user_input=None) -> ConfigFlowResult:
        label, modes = self._account_devices[self._account_device]
        return self.async_show_menu(
            step_id="account_mode",
            menu_options=[f"account_{mode}" for mode in modes],
            description_placeholders={"name": label},
        )

    async def _claim_account_credential(self, mode: str) -> str:
        device_id = self._account_device
        if not self._cloud_recovery:
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured()
        client = ShevLoggerCloudApi(async_get_clientsession(self.hass), self._account_ticket)
        self._account_ticket = ""
        return await client.async_connect_account(device_id, mode)

    async def async_step_account_cloud(self, user_input=None) -> ConfigFlowResult:
        entry = None
        if self._cloud_recovery:
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            if entry is None or entry.unique_id != self._account_device or CONF_CLOUD_TOKEN not in entry.data:
                return self.async_abort(reason="entry_changed")
            original_data = entry.data
        try:
            token = await self._claim_account_credential("cloud")
        except ShevLoggerError:
            return self.async_abort(reason="cloud_link_failed")
        if entry is not None:
            if self.hass.config_entries.async_get_entry(entry.entry_id) is not entry or entry.data != original_data:
                return self.async_abort(reason="entry_changed")
            return self.async_update_reload_and_abort(
                entry, data={CONF_CLOUD_TOKEN: token}, reason=self._cloud_recovery,
            )
        return self.async_create_entry(
            title=self._account_devices[self._account_device][0], data={CONF_CLOUD_TOKEN: token},
        )

    async def async_step_account_lan(self, user_input=None) -> ConfigFlowResult:
        device_id = self._account_device
        default_host = f"shevlogger-{device_id.lower()}.local"
        host = user_input[CONF_HOST] if user_input is not None else default_host
        errors: dict[str, str] = {}
        try:
            normalized_host = normalize_host(host)
            if normalized_host != default_host:
                address = IPv4Address(normalized_host)
                if not address.is_private or address.is_loopback or address.is_unspecified:
                    raise ValueError("gateway must be on a private network")
            elif not re.fullmatch(r"shevlogger-[a-z0-9-]{1,51}\.local", normalized_host):
                raise ValueError("invalid local gateway hostname")
        except ValueError:
            errors[CONF_HOST] = "invalid_local_address"
        else:
            if not self._account_lan_token:
                try:
                    self._account_lan_token = await self._claim_account_credential("lan")
                except ShevLoggerError:
                    return self.async_abort(reason="cloud_link_failed")
            try:
                device = await _device_info(ShevLoggerApi(
                    async_get_clientsession(self.hass), normalized_host, self._account_lan_token
                ))
            except ShevLoggerAuthError as error:
                errors["base"] = error.reason
            except ShevLoggerConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if device["id"] == device_id:
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=self._account_devices[device_id][0],
                        data={CONF_HOST: normalized_host, CONF_TOKEN: self._account_lan_token},
                    )
                errors["base"] = "wrong_device"
        return self.async_show_form(
            step_id="account_lan",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
            description_placeholders={"name": self._account_devices[device_id][0]},
            errors=errors,
        )

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
            return await self.async_step_account()

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
