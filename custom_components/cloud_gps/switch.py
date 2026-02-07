"""switch Entities"""
import logging
import time
import datetime
import json
import requests
from async_timeout import timeout
from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.components.switch import (
    SwitchEntity, 
    SwitchEntityDescription
)

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    COORDINATOR,
    DOMAIN,
    CONF_WEB_HOST,
    CONF_SWITCHS,
    MQTT_MANAGER,
)


_LOGGER = logging.getLogger(__name__)


SWITCH_TYPES: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        key="defence",
        name="defence",
        icon="mdi:shield"
    ),
    SwitchEntityDescription(
        key="open_lock",
        name="open_lock",
        icon="mdi:lock-open"
    ),
    SwitchEntityDescription(
        key="defencemode",
        name="defencemode",
        icon="mdi:lock-open"
    )
)

SWITCH_TYPES_MAP = { description.key: description for description in SWITCH_TYPES }
#_LOGGER.debug("SWITCH_TYPES_MAP: %s" ,SWITCH_TYPES_MAP)

SWITCH_TYPES_KEYS = { description.key for description in SWITCH_TYPES }
#_LOGGER.debug("SWITCH_TYPES_KEYS: %s" ,SWITCH_TYPES_KEYS)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add Switchentities from a config_entry."""      
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    webhost = config_entry.data[CONF_WEB_HOST]
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    enabled_switchs = [s for s in config_entry.options.get(CONF_SWITCHS, []) if s in SWITCH_TYPES_KEYS]
    
    _LOGGER.debug("coordinator switchs: %s", coordinator.data)
    _LOGGER.debug("enabled_switchs: %s" ,enabled_switchs)    
    
    for coordinatordata in coordinator.data:
        _LOGGER.debug("coordinatordata")
        _LOGGER.debug(coordinatordata)
    
        switchs = []

        for switch_type in enabled_switchs:
            _LOGGER.debug("switch_type: %s" ,switch_type)
            mqtt_manager = hass.data[DOMAIN][config_entry.entry_id].get(MQTT_MANAGER)
            switchs.append(CloudGPSSwitchEntity(hass, webhost, username, password, coordinatordata, SWITCH_TYPES_MAP[switch_type], coordinator, mqtt_manager))
            
        async_add_entities(switchs, False)           
            

class CloudGPSSwitchEntity(SwitchEntity):
    """Define an switch entity."""
    _attr_has_entity_name = True
      
    def __init__(self, hass, webhost, username, password, imei, description, coordinator, mqtt_manager=None):
        """Initialize."""
        super().__init__()
        self.entity_description = description
        self.session_hellobike = requests.session()
        self._hass = hass
        self._webhost = webhost
        self._username = username
        self._password = password
        self._imei = imei        
        self.coordinator = coordinator
        _LOGGER.debug("SwitchEntity coordinator: %s", coordinator.data)
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description.key}"
        self._attr_translation_key = f"{self.entity_description.name}"
        
        self._is_on = None
        self._doing = False
        
        if webhost == "tuqiang123.com":
            from .tuqiang123_data_fetcher import DataSwitch
        elif webhost == "hellobike.com":
            from .hellobike_data_fetcher import DataSwitch
        elif webhost == "gps_mqtt":
            from .gps_mqtt_data_fetcher import DataSwitch
        elif webhost == "niu.com":
            from .niu_data_fetcher import DataSwitch
        else:
            _LOGGER.error("配置的实体平台不支持，请不要启用此按钮实体！")
            return
        
        if webhost == "gps_mqtt":
            self._switch = DataSwitch(hass, username, password, imei, mqtt_manager)
        else:
            self._switch = DataSwitch(hass, username, password, imei)
        
     
   
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
    def is_on(self):
        """Check if switch is on."""        
        return self._is_on
        
    @property
    def available(self):
        """Return the available."""
        attr_available = True if (self.coordinator.data.get(self._imei, {}).get("attrs", {}).get("onlinestatus", "") == "在线" ) else False
        return attr_available
        
    @property
    def state_attributes(self): 
        attrs = {}
        if self.coordinator.data.get(self._imei):            
            attrs["querytime"] = self.coordinator.data[self._imei]["attrs"]["querytime"]        
        return attrs 

    async def async_turn_on(self, **kwargs):
        """Turn switch on."""        
        self._doing = True
        await self._switch._turn_on(self.entity_description.key)
        self._is_on = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn switch off."""        
        self._doing = True
        await self._switch._turn_off(self.entity_description.key)
        self._is_on = False
        await self.coordinator.async_request_refresh()
        
    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update entity."""
        _LOGGER.debug("刷新switch数据")
        # await self.coordinator.async_request_refresh()
        if self._doing == False:
            if self._webhost == "hellobike.com":
                if self.entity_description.key == "defence":
                    _LOGGER.debug("defence: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("defence")== "已设防"
                elif self.entity_description.key == "defencemod":
                    _LOGGER.debug("open_lock: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("acc")== "已开锁"
                    
            elif self._webhost == "tuqiang123.com":
                if self.entity_description.key == "defence":
                    _LOGGER.debug("defence: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("defence")== "已设防"
                elif self.entity_description.key == "defencemode":
                    _LOGGER.debug("open_lock: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("acc")== "已启动"
            elif self._webhost == "gps_mqtt":
                if self.entity_description.key == "open_lock":
                    _LOGGER.debug("open_lock: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("In1")== 1
            elif self._webhost == "niu.com":
                if self.entity_description.key == "open_lock":
                     self._is_on = self.coordinator.data[self._imei]["attrs"].get("acc") == "已开锁"
                    
        self._doing = False
    
    
