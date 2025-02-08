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
    },
    "nowtrack": {
        "label": "nowtrack",
        "name": "nowtrack",
        "icon": "mdi:map-marker-check",
        "device_class": "restart",
    }
}


BUTTON_TYPES_KEYS = {key for key, description in BUTTON_TYPES.items()}

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add buttonentities from a config_entry."""      
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    webhost = config_entry.data[CONF_WEB_HOST]
    username = config_entry.data[CONF_USERNAME]
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
            buttons.append(CloudGPSButtonEntity(hass, webhost, username, password, coordinatordata, BUTTON_TYPES[button_type], coordinator))
            
        async_add_entities(buttons, False)            


class CloudGPSButtonEntity(ButtonEntity):
    """Define an button entity."""
    _attr_has_entity_name = True
    
    def __init__(self, hass, webhost, username, password, imei, description, coordinator):
        """Initialize."""
        super().__init__()
        self._attr_icon = description['icon']
        self._hass = hass
        self._description = description
        self.session_hellobike = requests.session()
        self._webhost = webhost
        self._username = username
        self._password = password
        self._imei = imei        
        self.coordinator = coordinator
        _LOGGER.debug("ButtonEntity coordinator: %s", coordinator.data)
        self._unique_id = f"{self.coordinator.data[self._imei]['location_key']}-{description['label']}"
        self._attr_translation_key = f"{description['name']}"
        self._state = None
        if webhost == "tuqiang123.com":
            from .tuqiang123_data_fetcher import DataButton
        elif webhost == "hellobike.com":
            from .hellobike_data_fetcher import DataButton
        else:
            _LOGGER.error("配置的实体平台不支持，请不要启用此按钮实体！")
            return
        
        self._button = DataButton(hass, username, password, imei)
        

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
    def available(self):
        """Return the available."""
        attr_available = True if (self.coordinator.data.get(self._imei, {}).get("attrs", {}).get("onlinestatus", "") == "在线" ) else False
        return attr_available
        
    @property
    def device_class(self):
        """Return the unit_of_measurement."""
        if self._description.get("device_class"):
            return self._description["device_class"]
           
        
    def press(self) -> None:
        """Handle the button press."""

    async def async_press(self) -> None:
        """Handle the button press."""
        if self._webhost == "hellobike.com" and self._description['label']=="bell":
            self._state = await self._button._action("rent.order.bell")
        elif self._webhost == "tuqiang123.com" and self._description['label']=="nowtrack":
            self._state = await self._button._action("立即定位")
        

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update entity."""
        #await self.coordinator.async_request_refresh()        
    
        
