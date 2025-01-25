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
import math
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

varstinydict = {}

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
        self.deviceinfo = {}
        self.trackerdata = {}
        self.address = {}
        self.lastgpstime = datetime.datetime.now()
        
        headers = {
            'Host': 'ts.amap.com',
            'Accept': 'application/json',
            'sessionid': password.split("||")[1],
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'Cookie': 'sessionid=' + password.split("||")[1],
        }
        self.session_autoamap.headers.update(headers)
        
        global varstinydict
        _LOGGER.debug("varstinydict: %s", varstinydict)
        if not varstinydict.get("laststoptime_"+self.location_key):            
            varstinydict["laststoptime_"+self.location_key] = ""
        if not varstinydict.get("lastlat_"+self.location_key):
            varstinydict["lastlat_"+self.location_key] = 0
        if not varstinydict.get("lastlon_"+self.location_key):
            varstinydict["lastlon_"+self.location_key] = 0
        if not varstinydict.get("isonline_"+self.location_key):
            varstinydict["isonline_"+self.location_key] = "离线"
        if not varstinydict.get("lastonlinetime_"+self.location_key):
            varstinydict["lastonlinetime_"+self.location_key] = ""
        if not varstinydict.get("lastofflinetime_"+self.location_key):
            varstinydict["lastofflinetime_"+self.location_key] = ""        
        if not varstinydict.get("runorstop_"+self.location_key):
            varstinydict["runorstop_"+self.location_key] = "stop"
        if not varstinydict.get("course_"+self.location_key):
            varstinydict["course_"+self.location_key] = 0
        if not varstinydict.get("speed_"+self.location_key):
            varstinydict["speed_"+self.location_key] = 0



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
                
    
    def get_distance(self, lat1, lng1, lat2, lng2):
        earth_radius = 6378.137
        rad_lat1 = lat1 * math.pi / 180.0
        rad_lat2 = lat2 * math.pi / 180.0
        a = rad_lat1 - rad_lat2
        b = lng1 * math.pi / 180.0 - lng2 * math.pi / 180.0
        s = 2 * math.asin(math.sqrt(math.pow(math.sin(a / 2), 2) + math.cos(rad_lat1) * math.cos(rad_lat2) * math.pow(math.sin(b / 2), 2)))
        s = s * earth_radius
        return s * 1000
        
    def calculate_bearing(self, lat1, lng1, lat2, lng2):
        lat1 = math.radians(lat1)
        lat2 = math.radians(lat2)
        delta_lng = math.radians(lng2 - lng1)
        y = math.sin(delta_lng) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lng)
        bearing = math.degrees(math.atan2(y, x))
        return int((bearing + 360) % 360)
        

        
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
                    
                    distance = self.get_distance(thislat, thislon, varstinydict["lastlat_"+self.location_key], varstinydict["lastlon_"+self.location_key])
                    
                    if distance > 10:
                        _LOGGER.debug("状态为运动: %s ,%s ,%s", varstinydict,thislat,thislon)
                        status = "运动"
                        distancetime = (datetime.datetime.now() - self.lastgpstime).total_seconds()
                        if distancetime > 1 and distance < 10000:
                            varstinydict["speed_"+self.location_key] = round((distance / distancetime * 3.6), 1)
                            varstinydict["course_"+self.location_key] = self.calculate_bearing(thislat, thislon, varstinydict["lastlat_"+self.location_key], varstinydict["lastlon_"+self.location_key])
                        self.lastgpstime = datetime.datetime.now()
                        varstinydict["runorstop_"+self.location_key] = "run"
                        varstinydict["lastlat_"+self.location_key] = thislat
                        varstinydict["lastlon_"+self.location_key] = thislon
                    elif varstinydict["runorstop_"+self.location_key] == "run":
                        _LOGGER.debug("变成静止: %s", varstinydict)
                        status = "静止"
                        varstinydict["laststoptime_"+self.location_key] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        varstinydict["runorstop_"+self.location_key] = "stop"
                        varstinydict["speed_"+self.location_key] = 0
                        
                    if infodata['naviStatus'] == 1:
                        naviStatus = "导航中"
                        status = "导航中"
                    else:
                        naviStatus = "未导航"
                        
                    if infodata["onlineStatus"] == 1:
                        onlinestatus = "在线"
                    elif infodata["onlineStatus"] == 0:
                        onlinestatus = "离线"
                        status = "离线"
                    else:
                        onlinestatus = "未知"
                        
                    if onlinestatus == "离线" and (varstinydict["isonline_"+self.location_key] == "在线"):
                        varstinydict["lastofflinetime_"+self.location_key] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        varstinydict["isonline_"+self.location_key] = "离线"
                    if onlinestatus == "在线" and (varstinydict["isonline_"+self.location_key] == "离线"):
                        varstinydict["lastonlinetime_"+self.location_key] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        varstinydict["isonline_"+self.location_key] = "在线" 
                
                    lastofflinetime = varstinydict["lastofflinetime_"+self.location_key]
                    lastonlinetime = varstinydict["lastonlinetime_"+self.location_key]
                    onlinestatus = varstinydict["isonline_"+self.location_key]
                    laststoptime = varstinydict["laststoptime_"+self.location_key] 
                    runorstop =  varstinydict["runorstop_"+self.location_key]
                    speed =  varstinydict["speed_"+self.location_key]
                    course =  varstinydict["course_"+self.location_key]
                        
                    if laststoptime != "" and runorstop ==  "stop":
                        parkingtime=self.time_diff(int(time.mktime(time.strptime(laststoptime, "%Y-%m-%d %H:%M:%S")))) 
                    else:
                        parkingtime = "未知"
                    
                    attrs ={
                        "querytime": querytime,
                        "speed": speed,
                        "course": course,
                        "distance": distance,
                        "runorstop": runorstop,
                        "laststoptime": laststoptime,
                        "parkingtime": parkingtime,
                        "naviStatus": naviStatus,
                        "onlinestatus": onlinestatus,
                        "lastofflinetime":lastofflinetime,
                        "lastonlinetime":lastonlinetime
                    }
                
                    self.trackerdata[imei] = {"location_key":self.location_key+imei,"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"imei":imei,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
