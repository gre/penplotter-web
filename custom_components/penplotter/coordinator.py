import logging
from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_SECONDS, SSL_CONTEXT

_LOGGER = logging.getLogger(__name__)


class PenPlotterCoordinator(DataUpdateCoordinator):
    """Polls the penplotter-web API for status."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, entry_id: str, title: str) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.base_url = f"https://{host}:{port}"
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=title,
            manufacturer="Evil Mad Scientist",
            model="AxiDraw",
            configuration_url=self.base_url,
        )

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self.base_url}/api/status",
                ssl=SSL_CONTEXT,
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
            ssl=SSL_CONTEXT,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
        await self.async_request_refresh()
