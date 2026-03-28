from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PenPlotterCoordinator

BUTTONS = [
    ("pause", "Pause", "mdi:pause", "/api/pause"),
    ("resume", "Resume", "mdi:play", "/api/resume"),
    ("stop", "Stop", "mdi:stop", "/api/stop"),
    ("pen_up", "Pen Up", "mdi:arrow-up", "/api/pen/up"),
    ("pen_down", "Pen Down", "mdi:arrow-down", "/api/pen/down"),
    ("home", "Home", "mdi:home", "/api/home"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PenPlotterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PenPlotterButton(coordinator, entry, key, name, icon, path)
        for key, name, icon, path in BUTTONS
    ])


class PenPlotterButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, icon, api_path) -> None:
        super().__init__(coordinator)
        self._api_path = api_path
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.api_post(self._api_path)
