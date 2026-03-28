import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_VERIFY_SSL, DOMAIN, SCAN_INTERVAL_SECONDS, build_base_url, make_ssl_context

_LOGGER = logging.getLogger(__name__)


class PenPlotterCoordinator(DataUpdateCoordinator):
    """Polls the penplotter-web API for status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.base_url = build_base_url(entry.data[CONF_HOST], entry.data[CONF_PORT])
        self._ssl = make_ssl_context(entry.data.get(CONF_VERIFY_SSL, True))
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Evil Mad Scientist",
            model="AxiDraw",
            configuration_url=self.base_url,
        )

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self.base_url}/api/status",
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with plotter: {err}") from err

    async def api_post(self, path: str) -> None:
        """POST to a plotter API endpoint."""
        session = async_get_clientsession(self.hass)
        async with session.post(
            f"{self.base_url}{path}",
            ssl=self._ssl,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
        await self.async_request_refresh()
