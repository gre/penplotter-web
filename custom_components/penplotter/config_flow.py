import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DOMAIN, make_ssl_context


class PenPlotterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Pen Plotter."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_HOST],
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_VERIFY_SSL, default=False): bool,
            }),
            errors=errors,
        )

    async def _validate(self, user_input: dict) -> dict:
        try:
            session = async_get_clientsession(self.hass)
            ssl_ctx = make_ssl_context(user_input.get(CONF_VERIFY_SSL, False))
            async with session.get(
                f"https://{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/api/status",
                ssl=ssl_ctx,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return {"base": "cannot_connect"}
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        return {}
