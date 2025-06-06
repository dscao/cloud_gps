"""sensor Entities."""
import logging
import time, datetime
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COORDINATOR,
    DOMAIN,
    CONF_WEB_HOST,
    CONF_SENSORS,
    KEY_ADDRESS,
    KEY_LASTSTOPTIME,
    KEY_LASTSEEN,
    KEY_PARKING_TIME,
    KEY_SPEED,
    KEY_TOTALKM,
    KEY_STATUS,
    KEY_ACC,
    KEY_BATTERY,
    KEY_BATTERY_STATUS,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=KEY_ADDRESS,
        name="address",
        icon="mdi:map"
    ),
    SensorEntityDescription(
        key=KEY_PARKING_TIME,
        name="parkingtime",
        icon="mdi:parking"
    ),
    SensorEntityDescription(
        key=KEY_LASTSTOPTIME,
        name="laststoptime",
        icon="mdi:timer-stop"
    ),
    SensorEntityDescription(
        key=KEY_SPEED,
        name="speed",
        unit_of_measurement = "km/h",
        device_class = "speed"
    ),
    SensorEntityDescription(
        key=KEY_TOTALKM,
        name="totalkm",
        unit_of_measurement = "km",
        device_class = "distance"
    ),
    SensorEntityDescription(
        key=KEY_STATUS,
        name="status",
        icon="mdi:car-brake-alert"
    ),
    SensorEntityDescription(
        key=KEY_ACC,
        name="acc",
        icon="mdi:engine"
    ),
    SensorEntityDescription(
        key=KEY_BATTERY,
        name="powbattery",
        unit_of_measurement = "V",
        icon="mdi:car-battery"
    ),
    SensorEntityDescription(
        key=KEY_BATTERY_STATUS,
        name="battery_status",
        icon="mdi:battery"
    ),
    SensorEntityDescription(
        key=KEY_LASTSEEN,
        name="lastseen",
        icon="mdi:eye-check"
    )
)

SENSOR_TYPES_MAP = { description.key: description for description in SENSOR_TYPES }
#_LOGGER.debug("SENSOR_TYPES_MAP: %s" ,SENSOR_TYPES_MAP)

SENSOR_TYPES_KEYS = { description.key for description in SENSOR_TYPES }
#_LOGGER.debug("SENSOR_TYPES_KEYS: %s" ,SENSOR_TYPES_KEYS)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add tuqiang entities from a config_entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    webhost = config_entry.data[CONF_WEB_HOST]
    enabled_sensors = [s for s in config_entry.options.get(CONF_SENSORS, []) if s in SENSOR_TYPES_KEYS]
    
    _LOGGER.debug("coordinator sensors: %s", coordinator.data)
    _LOGGER.debug("enabled_sensors: %s" ,enabled_sensors)
    
    for coordinatordata in coordinator.data:
        _LOGGER.debug("coordinatordata")
        _LOGGER.debug(coordinatordata)
    
        sensors = []
        for sensor_type in enabled_sensors:
            _LOGGER.debug("sensor_type: %s" ,sensor_type)
            sensors.append(CloudGPSSensorEntity(webhost, coordinatordata, SENSOR_TYPES_MAP[sensor_type], coordinator))
            
        async_add_entities(sensors, False)

class CloudGPSSensorEntity(CoordinatorEntity):
    """Define an sensor entity with state restoration."""
    
    _attr_has_entity_name = True
      
    def __init__(self, webhost, imei, description, coordinator):
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._webhost = webhost
        self._imei = imei        
        self.coordinator = coordinator
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description.key}"

        self._attr_translation_key = f"{self.entity_description.name}"
        self._state = None
        self._attrs = {}
        
        # 立即尝试加载状态
        self._load_state()

        _LOGGER.debug(self._state)

    @property
    def unique_id(self):
        return self._unique_id
        
    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.data[self._imei]["location_key"])},
            "name": self._imei,
            "manufacturer": self._webhost,
            "entry_type": DeviceEntryType.SERVICE,
            "model": self.coordinator.data[self._imei]["deviceinfo"]["device_model"],
            "sw_version": self.coordinator.data[self._imei]["deviceinfo"]["sw_version"],
        }

    @property
    def should_poll(self):
        """Return the polling requirement of the entity."""
        return True

    @property
    def native_value(self):
        """Return battery value of the device."""
        return self._state

    @property
    def state(self):
        """Return the state."""
        return self._state
    
    @property
    def unit_of_measurement(self):
        """Return the unit_of_measurement."""
        if self.entity_description.unit_of_measurement:
            return self.entity_description.unit_of_measurement
        
    @property
    def device_class(self):
        """Return the unit_of_measurement."""
        if self.entity_description.device_class:
            return self.entity_description.device_class

    @property
    def state_attributes(self): 
        return self._attrs
    
    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


    async def async_update(self):
        """Update sensor entity."""
        _LOGGER.debug("Refreshing sensor data")
        self._load_state()

    def _load_state(self):
        """Load state from the coordinator data."""
        if self.coordinator.data.get(self._imei):
            # Set initial state based on the entity description key
            attrs = self.coordinator.data[self._imei]["attrs"]
            if self.entity_description.key == "parkingtime":
                self._state = attrs.get("parkingtime")
            elif self.entity_description.key == "laststoptime":
                self._state = attrs.get("laststoptime")
            elif self.entity_description.key == "lastseen":
                self._state = attrs.get("lastseen")
            elif self.entity_description.key == "address":
                self._state = attrs.get("address")
            elif self.entity_description.key == "speed":
                self._state = float(attrs.get("speed", 0))
            elif self.entity_description.key == "totalkm":
                self._state = float(attrs.get("totalKm", 0))
            elif self.entity_description.key == "acc":
                self._state = attrs.get("acc")
            elif self.entity_description.key == "powbattery":
                self._state = float(attrs.get("powbatteryvoltage", 0))
            elif self.entity_description.key == "battery_status":
                self._state = attrs.get("battery_status")
            elif self.entity_description.key == "status":
                self._state = self.coordinator.data[self._imei].get("status")
            
            self._attrs = {"querytime": attrs.get("querytime")}
            
        else:
            # 保持最后的有效状态
            _LOGGER.warning("Failed to obtain new coordinates, using last known state: %s", self._state)
 