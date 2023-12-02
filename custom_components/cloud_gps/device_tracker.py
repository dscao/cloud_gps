"""Support for the cloud_gps service."""
import logging
import time, datetime
import requests
import re
import json
import hashlib
import urllib.parse

from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.helpers.device_registry import DeviceEntryType

from .helper import gcj02towgs84, wgs84togcj02, gcj02_to_bd09

from homeassistant.const import (
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CLIENT_ID,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_HOME,
    STATE_NOT_HOME, 
    MAJOR_VERSION, 
    MINOR_VERSION,    
)

from .const import (
    COORDINATOR,
    DOMAIN,
    CONF_WEB_HOST,
    UNDO_UPDATE_LISTENER,
    CONF_ATTR_SHOW,
    MANUFACTURER,
    CONF_PRIVATE_KEY,
    CONF_MAP_GCJ_LAT,
    CONF_MAP_GCJ_LNG,
    CONF_MAP_BD_LAT,
    CONF_MAP_BD_LNG, 
    CONF_WITH_BAIDUMAP_CARD,
)

PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add cloud entities from a config_entry."""
    webhost = config_entry.data[CONF_WEB_HOST]
    attr_show = config_entry.options.get(CONF_ATTR_SHOW, True)
    with_baidumap_card = config_entry.options.get(CONF_WITH_BAIDUMAP_CARD, False)
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    
    for coordinatordata in coordinator.data:
        _LOGGER.debug("coordinatordata")
        _LOGGER.debug(coordinatordata)
        async_add_entities([CloudGPSEntity(hass, webhost, coordinatordata, attr_show, with_baidumap_card, coordinator)], False)


class CloudGPSEntity(TrackerEntity):
    """Representation of a tracker condition."""
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "cloud_device_tracker"
    def __init__(self, hass, webhost, imei, attr_show, with_baidumap_card, coordinator):
        self._hass = hass
        self._imei = imei
        self._webhost = webhost
        self.coordinator = coordinator   
        self._attr_show = attr_show
        self._with_baidumap_card = with_baidumap_card
        self._attrs = {}
        self._coords = [self.coordinator.data[self._imei]["thislon"], self.coordinator.data[self._imei]["thislat"]]

    @property
    def unique_id(self):
        """Return a unique_id for this entity."""
        _LOGGER.debug("device_tracker_unique_id: %s", self.coordinator.data[self._imei]["location_key"])
        return self.coordinator.data[self._imei]["location_key"]

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

    # @property
    # def available(self):
        # """Return True if entity is available."""
        # return self.trackerdata.last_update_success 

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:car"
        
    @property
    def source_type(self):
        return "gps"

    @property
    def latitude(self):                
        return self._coords[1]

    @property
    def longitude(self):
        return self._coords[0]
        
    @property
    def location_accuracy(self):
        return 0        

    @property
    def state_attributes(self): 
        attrs = super(CloudGPSEntity, self).state_attributes
        #data = self.trackerdata.get("result")
        if self.coordinator.data[self._imei]:             
            attrs["status"] = self.coordinator.data[self._imei]["status"]
            if attrs.get("imei"):
                attrs["imei"] = self.coordinator.data[self._imei]["imei"]
            if self._with_baidumap_card == True:
                attrs["custom_ui_more_info"] = "baidu-map"
            if self._attr_show == True:
                attrslist = self.coordinator.data[self._imei]["attrs"]
                for key, value in attrslist.items():
                    attrs[key] = value
                if self.coordinator.data[self._imei]["deviceinfo"].get("expiration"):
                    attrs["expiration"] = self.coordinator.data[self._imei]["deviceinfo"]["expiration"]
                
                gcjdata = wgs84togcj02(self.coordinator.data[self._imei]["thislon"], self.coordinator.data[self._imei]["thislat"])
                attrs[CONF_MAP_GCJ_LAT] = gcjdata[1]
                attrs[CONF_MAP_GCJ_LNG] = gcjdata[0]
                bddata = gcj02_to_bd09(gcjdata[0], gcjdata[1])
                attrs[CONF_MAP_BD_LAT] = bddata[1]
                attrs[CONF_MAP_BD_LNG] = bddata[0]
        return attrs


    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update cloud entity."""
        _LOGGER.debug("刷新device_tracker数据: %s %s", datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") )
        #await self.coordinator.async_request_refresh()
        if self.coordinator.data.get(self._imei):
            self._coords = [self.coordinator.data[self._imei]["thislon"], self.coordinator.data[self._imei]["thislat"]]

