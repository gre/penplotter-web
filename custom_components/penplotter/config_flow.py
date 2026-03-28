import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import DEFAULT_PORT, DOMAIN, SSL_CONTEXT


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
                    title=user_input.get(CONF_NAME, user_input[CONF_HOST]),
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(CONF_NAME, default="Pen Plotter"): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }),
            errors=errors,
        )

    async def _validate(self, user_input: dict) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/api/status",
                    ssl=SSL_CONTEXT,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return {"base": "cannot_connect"}
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        return {}
