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
    KEY_PARKING_TIME,
    KEY_SPEED,
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
        icon="mdi:timer-stop-outline"
    ),
    SensorEntityDescription(
        key=KEY_LASTSTOPTIME,
        name="laststoptime",
        icon="mdi:timer-stop"
    ),
    SensorEntityDescription(
        key=KEY_SPEED,
        name="speed",
        icon="mdi:speedometer",
        unit_of_measurement = "km/h",
        device_class = "speed"
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
    """Define an sensor entity."""
    
    _attr_has_entity_name = True
      
    def __init__(self, webhost, imei, description, coordinator):
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._webhost = webhost
        self._imei = imei        
        self.coordinator = coordinator
        _LOGGER.debug("SensorEntity coordinator: %s", coordinator.data)
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description.key}"

        self._attr_translation_key = f"{self.entity_description.name}"
        if self.entity_description.key == "parkingtime":
            self._state = self.coordinator.data[self._imei]["attrs"].get("parkingtime")
            self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
        elif self.entity_description.key == "laststoptime":
            self._state = self.coordinator.data[self._imei]["attrs"].get("laststoptime")
            self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
        elif self.entity_description.key == "address":         
            self._state = self.coordinator.data[self._imei]["attrs"].get("address")
            self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
        elif self.entity_description.key == "speed":         
            self._state = float(self.coordinator.data[self._imei]["attrs"].get("speed"))
            self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
        
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
        attrs = {}
        if self.coordinator.data.get(self._imei):            
            attrs["querytime"] = self.coordinator.data[self._imei]["attrs"]["querytime"]        
        return attrs 
    
    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update tuqiang entity."""
        _LOGGER.debug("刷新sensor数据")
        #await self.coordinator.async_request_refresh()
        if self.coordinator.data.get(self._imei):
            if self.entity_description.key == "parkingtime":
                self._state = self.coordinator.data[self._imei]["attrs"].get("parkingtime")
                self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
            elif self.entity_description.key == "laststoptime":
                self._state = self.coordinator.data[self._imei]["attrs"].get("laststoptime")
                self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
            elif self.entity_description.key == "address":         
                self._state = self.coordinator.data[self._imei]["attrs"].get("address")
                self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
            elif self.entity_description.key == "speed":         
                self._state = self.coordinator.data[self._imei]["attrs"].get("speed")
                self._attrs = {"querytime": self.coordinator.data[self._imei]["attrs"].get("querytime")}
            
        
        
        
