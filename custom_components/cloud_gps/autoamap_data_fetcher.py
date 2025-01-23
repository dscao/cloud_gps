"""
get info
"""

import logging
import requests
import re
import asyncio
import json
import time
import datetime
from async_timeout import timeout
from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CLIENT_ID,
)

from .const import (
    COORDINATOR,
    DOMAIN,
    CONF_WEB_HOST,
    CONF_DEVICE_IMEI,
    UNDO_UPDATE_LISTENER,
    CONF_ATTR_SHOW,
    CONF_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

AUTOAMAP_API_HOST = "http://ts.amap.com/ws/tservice/internal/link/mobile/get?ent=2&in="

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_autoamap = requests.session()
        self.userid = None
        self.usertype = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}
        self.address = {}
        
        headers = {
            'Host': 'ts.amap.com',
            'Accept': 'application/json',
            'sessionid': password.split("||")[1],
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'Cookie': 'sessionid=' + password.split("||")[1],
        }
        self.session_autoamap.headers.update(headers)    



    def _get_devices_info(self):        
        url = str.format(AUTOAMAP_API_HOST + self.password.split("||")[0])
        p_data = self.password.split("||")[2]
        resp = self.session_autoamap.post(url, data=p_data).json()["data"]["carLinkInfoList"]
        return resp

    
    def time_diff(self, timestamp):
            result = datetime.datetime.now() - datetime.datetime.fromtimestamp(timestamp)
            hours = int(result.seconds / 3600)
            minutes = int(result.seconds % 3600 / 60)
            seconds = result.seconds%3600%60
            if result.days > 0:
                return("{0}天{1}小时{2}分钟".format(result.days,hours,minutes))
            elif hours > 0:
                return("{0}小时{1}分钟".format(hours,minutes))
            elif minutes > 0:
                return("{0}分钟{1}秒".format(minutes,seconds))
            else:
                return("{0}秒".format(seconds))
        
    async def get_data(self): 
        
        try:
            async with timeout(10): 
                devicesinfodata =  await self.hass.async_add_executor_job(self._get_devices_info)
        except (
            ClientConnectorError
        ) as error:
            raise

        _LOGGER.debug("result devicesinfodata: %s", devicesinfodata)
                
        for imei in self.device_imei:
            _LOGGER.debug("get info imei: %s", imei)
            self.trackerdata[imei] = {}
            for infodata in devicesinfodata:
                if infodata.get("tid") == imei:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "高德地图车机版"
                    self.deviceinfo[imei]["sw_version"] = infodata["sysInfo"]["autodiv"]
            
                    querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    thislat = infodata["naviLocInfo"]["lat"]
                    thislon = infodata["naviLocInfo"]["lon"]
                    
                    if infodata["onlineStatus"] == 1:
                        status = "在线"
                    elif infodata["onlineStatus"] == 0:
                        status = "离线"
                    else:
                        status = "未知"
                        
                    if infodata['naviStatus'] == 1:
                        naviStatus = "导航中"
                    else:
                        naviStatus = "未导航"
                    
                    attrs ={
                        "querytime": querytime,
                        "naviStatus": naviStatus
                    }
                
                    self.trackerdata[imei] = {"location_key":self.location_key+imei,"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"imei":imei,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
