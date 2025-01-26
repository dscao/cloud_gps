"""button Entities"""
import logging
import time
import datetime
import json
import re
import requests
from async_timeout import timeout
from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.components.button import (
    ButtonEntity, 
    ButtonEntityDescription
)

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    COORDINATOR,
    DOMAIN,
    CONF_WEB_HOST,
    CONF_BUTTONS,
)

_LOGGER = logging.getLogger(__name__)

HELLOBIKE_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
API_URL_HELLOBIKE = "https://a.hellobike.com/evehicle/api"

BUTTON_TYPES = {
    "bell": {
        "label": "bell",
        "name": "bell",
        "icon": "mdi:bell",
        "device_class": "restart",
        "action_body": "rent.order.bell"
    }
}


BUTTON_TYPES_KEYS = {key for key, description in BUTTON_TYPES.items()}

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add buttonentities from a config_entry."""      
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    webhost = config_entry.data[CONF_WEB_HOST]
    password = config_entry.data[CONF_PASSWORD]
    enabled_buttons = [s for s in config_entry.options.get(CONF_BUTTONS, []) if s in BUTTON_TYPES_KEYS]
    
    _LOGGER.debug("coordinator buttons: %s", coordinator.data)
    _LOGGER.debug("enabled_buttons: %s" ,enabled_buttons)
    for coordinatordata in coordinator.data:
        _LOGGER.debug("coordinatordata")
        _LOGGER.debug(coordinatordata)        
        buttons = []
        for button_type in enabled_buttons:
            _LOGGER.debug("button_type: %s" ,button_type)
            buttons.append(CloudGPSButtonEntity(hass, webhost, password, coordinatordata, BUTTON_TYPES[button_type], coordinator))
            
        async_add_entities(buttons, False)            


class CloudGPSButtonEntity(ButtonEntity):
    """Define an button entity."""
    _attr_has_entity_name = True
    
    def __init__(self, hass, webhost, password, imei, description, coordinator):
        """Initialize."""
        super().__init__()
        self._attr_icon = description['icon']
        self._hass = hass
        self._description = description
        self.session_hellobike = requests.session()
        self._webhost = webhost
        self._password = password
        self._imei = imei        
        self.coordinator = coordinator
        _LOGGER.debug("ButtonEntity coordinator: %s", coordinator.data)
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description['label']}"
        self._attr_available = False if self.coordinator.data[self._imei]["attrs"].get("onlinestatus") == "离线" else True
        self._attr_translation_key = f"{description['name']}"
        self._state = None
        headers = {
            'content-type': 'application/json; charset=utf-8',                    
            'User-Agent': HELLOBIKE_USER_AGENT
        }
        self.session_hellobike.headers.update(headers)

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
    def state(self):
        """Return the state."""
        return self._state

        
    @property
    def device_class(self):
        """Return the unit_of_measurement."""
        if self._description.get("device_class"):
            return self._description["device_class"]
           
        
    def press(self) -> None:
        """Handle the button press."""

    async def async_press(self) -> None:
        """Handle the button press."""
        if self._webhost == "hellobike.com":
            await self._hellobaike_bell_action()
        

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update entity."""
        #await self.coordinator.async_request_refresh()        
    
    def _post_data(self, url, p_data):
        resp = self.session_hellobike.post(url, data=json.dumps(p_data)).json()
        return resp
        
    async def _hellobaike_bell_action(self): 
        json_body = {
            "bikeNo" : str(self._imei),
            "token" : self._password,
            "action" : "rent.order.bell",
            "apiVersion": "2.23.0"
        }
        url =  API_URL_HELLOBIKE + "?rent.order.bell"
        
        try:
            async with timeout(10): 
                resdata = await self._hass.async_add_executor_job(self._post_data, url, json_body)
        except (
            ClientConnectorError
        ) as error:
            raise UpdateFailed(error)
        _LOGGER.debug("Requests remaining: %s", url)
        _LOGGER.debug(resdata)                        
        self._state = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _LOGGER.info("操作cloudgps: %s ", json_body)    
        return
        
