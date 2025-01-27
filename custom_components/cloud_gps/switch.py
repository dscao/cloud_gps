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
)


_LOGGER = logging.getLogger(__name__)

HELLOBIKE_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
API_URL_HELLOBIKE = "https://a.hellobike.com/evehicle/api"

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
            switchs.append(CloudGPSSwitchEntity(hass, webhost, password, coordinatordata, SWITCH_TYPES_MAP[switch_type], coordinator))
            
        async_add_entities(switchs, False)           
            

class CloudGPSSwitchEntity(SwitchEntity):
    """Define an switch entity."""
    _attr_has_entity_name = True
      
    def __init__(self, hass, webhost, password, imei, description, coordinator):
        """Initialize."""
        super().__init__()
        self.entity_description = description
        self.session_hellobike = requests.session()
        self._hass = hass
        self._webhost = webhost
        self._password = password
        self._imei = imei        
        self.coordinator = coordinator
        _LOGGER.debug("SwitchEntity coordinator: %s", coordinator.data)
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description.key}"
        self._attr_translation_key = f"{self.entity_description.name}"
        
        self._is_on = None
        self._doing = False
        
        headers = {
            'content-type': 'application/json; charset=utf-8',                    
            'User-Agent': HELLOBIKE_USER_AGENT
        }
        self.session_hellobike.headers.update(headers)
        
        if self._webhost == "hellobike.com":
            if self.entity_description.key == "defence":
                _LOGGER.debug("defence: %s", self.coordinator.data[self._imei])
                if self.coordinator.data[self._imei].get("attrs"):
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("defence")== "已设防"
            elif self.entity_description.key == "open_lock":
                _LOGGER.debug("open_lock: %s", self.coordinator.data[self._imei])
                if self.coordinator.data[self._imei].get("attrs"):
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("acc")== "已启动"
     
   
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
        attr_available = False if self.coordinator.data[self._imei]["attrs"].get("onlinestatus") == "离线" else True
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
        if self.entity_description.key == "defence":
            if self._webhost == "hellobike.com":
                url = "https://a.hellobike.com/evehicle/api?rent.order.setUpDefence"
                json_body = {
                    "action": "rent.order.setUpDefence",
                    "maction": "SET_DEFENCE",
                    "bikeNo": self._imei,
                    "token": self._password,
                    "apiVersion": "2.23.0"
                }
                await self._switch(url, json_body)
        elif self.entity_description.key == "open_lock":
            if self._webhost == "hellobike.com":
                url = "https://a.hellobike.com/evehicle/api?rent.order.openLock"
                json_body = {
                    "action": "rent.order.openLock",
                    "bikeNo": self._imei,
                    "token": self._password,
                    "apiVersion": "2.23.0"
                }
                await self._switch(url, json_body)
        self._is_on = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn switch off."""        
        self._doing = True
        if self.entity_description.key == "defence":
            if self._webhost == "hellobike.com":
                url = "https://a.hellobike.com/evehicle/api?rent.order.withdrawDefence"
                json_body = {
                    "action": "rent.order.withdrawDefence",
                    "maction": "WITHDRAW_DEFENCE",
                    "bikeNo": self._imei,
                    "token": self._password,
                    "apiVersion": "2.23.0"
                }
                await self._switch(url, json_body)
        elif self.entity_description.key == "open_lock":
            if self._webhost == "hellobike.com":
                url = "https://a.hellobike.com/evehicle/api?rent.order.closeLockCommand"
                json_body = {
                    "action": "rent.order.closeLockCommand",
                    "bikeNo": self._imei,
                    "token": self._password,
                    "apiVersion": "2.23.0"
                }
                await self._switch(url, json_body)
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
        await self.coordinator.async_request_refresh()
        if self._doing == False:
            if self._webhost == "hellobike.com":
                if self.entity_description.key == "defence":
                    _LOGGER.debug("defence: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("defence")== "已设防"
                elif self.entity_description.key == "open_lock":
                    _LOGGER.debug("open_lock: %s", self.coordinator.data[self._imei])
                    self._is_on = self.coordinator.data[self._imei]["attrs"].get("acc")== "已启动"
        self._doing = False
    
    def _post_data(self, url, p_data):
        _LOGGER.debug("Requests remaining: %s , body: %s", url, json.dumps(p_data))
        resp = self.session_hellobike.post(url, data=json.dumps(p_data)).json()        
        
        return resp
        
    async def _switch(self, url, action_body):       
        try:
            async with timeout(5): 
                resdata = await self._hass.async_add_executor_job(self._post_data, url, action_body)
        except (
            ClientConnectorError
        ) as error:
            raise UpdateFailed(error)
        _LOGGER.info("操作cloudgps: %s ", resdata) 
        return "OK"
