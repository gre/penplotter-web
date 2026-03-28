from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PenPlotterCoordinator

EXTRA_ATTRS = ("current_file", "elapsed", "can_home", "error")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PenPlotterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PenPlotterStateSensor(coordinator, entry),
        PenPlotterProgressSensor(coordinator, entry),
    ])


class PenPlotterStateSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:printer-3d-nozzle"
    _attr_has_entity_name = True

    def __init__(self, coordinator: PenPlotterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_state"
        self._attr_name = "State"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("state") if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {k: v for k in EXTRA_ATTRS if (v := self.coordinator.data.get(k)) is not None}


class PenPlotterProgressSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True

    def __init__(self, coordinator: PenPlotterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_progress"
        self._attr_name = "Progress"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("progress", 0) if self.coordinator.data else None
