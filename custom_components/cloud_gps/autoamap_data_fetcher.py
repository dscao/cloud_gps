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
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify
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

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)
        
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
        self.vardata = {}
        self.address = {}
        self.lastgpstime = datetime.datetime.now()
        
        # 使用自定义编码器的存储
        self._store = Store(
            hass, 
            version=1, 
            key=f"cloud_gps_{slugify(location_key)}",
            private=False,
            encoder=DateTimeEncoder  # 使用自定义编码器
        )

        # 使用简单标志而不是立即加载
        self._persisted_data_loaded = False
        
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
        
    async def _load_persisted_data(self):
        """异步加载持久化数据"""
        try:
            self._persisted_data = await self._store.async_load() or {}
            _LOGGER.debug("%s Loaded persisted data: %s", self.device_imei, self._persisted_data)

        except Exception as e:
            _LOGGER.error("%s Error loading persisted data: %s", self.device_imei, e)
            self._persisted_data = {}
    
    async def _persist_data(self):
        """异步保存数据到持久化存储"""
        try:
            # 准备要保存的数据，确保所有 datetime 对象都被转换为字符串
            data_to_save = {
                "vardata": self.vardata,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            await self._store.async_save(data_to_save)
            _LOGGER.debug("%s Persisted data saved", self.device_imei)
        except Exception as e:
            _LOGGER.error("%s Error saving persisted data: %s", self.device_imei, e)
        
    async def get_data(self): 
        # 延迟加载持久化数据（仅在第一次更新时）
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
            
        try:
            async with timeout(10): 
                devicesinfodata =  await self.hass.async_add_executor_job(self._get_devices_info)
                _LOGGER.debug("高德机车 %s 最终数据结果: %s", self.device_imei, devicesinfodata)
        except ClientConnectorError as error:
            _LOGGER.error("高德机车 %s 连接错误: %s", self.device_imei, error)
        except asyncio.TimeoutError:
            _LOGGER.error("高德机车 %s 获取数据超时 (10秒)", self.device_imei)
        except Exception as e:
            _LOGGER.error("高德机车 %s 未知错误: %s", self.device_imei, repr(e))
                
        for imei in self.device_imei:
            _LOGGER.debug("get info imei: %s", imei)
            #启动后第一次加载重启前保留的数据
            self.vardata[imei] = self._persisted_data.get("vardata", {}).get(imei,{})

            for infodata in devicesinfodata:
                if infodata.get("tid") == imei:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "高德地图车机版"
                    self.deviceinfo[imei]["sw_version"] = infodata["sysInfo"]["autodiv"]
            
                    querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    thislat = infodata["naviLocInfo"]["lat"]
                    thislon = infodata["naviLocInfo"]["lon"]
                    lastlat = self.vardata[imei].get("lastlat",0)
                    lastlon = self.vardata[imei].get("lastlon",0)
                    
                    distance = self.get_distance(thislat, thislon, lastlat, lastlon)
                    status = "停车"
                    if distance > 10:
                        _LOGGER.debug("状态为运动: %s ,%s", thislat,thislon)
                        status = "行驶"
                        distancetime = (datetime.datetime.now() - self.lastgpstime).total_seconds()
                        if distancetime > 1 and distance < 10000:
                            self.vardata[imei]["speed"] = round((distance / distancetime * 3.6), 1)
                            self.vardata[imei]["course"] = self.calculate_bearing(thislat, thislon, lastlat, lastlon)
                        self.lastgpstime = datetime.datetime.now()
                        if self.vardata[imei].get("runorstop","run") == "stop":
                            _LOGGER.debug("变成运动: %s ,%s", thislat,thislon)
                            self.vardata[imei]["lastruntime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
                        self.vardata[imei]["runorstop"] = "run"
                        self.vardata[imei]["lastlat"] = thislat
                        self.vardata[imei]["lastlon"] = thislon 
                        
                    elif self.vardata[imei].get("runorstop","run") == "run":
                        _LOGGER.debug("变成静止: %s ,%s", thislat,thislon)
                        status = "静止"
                        self.vardata[imei]["laststoptime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.vardata[imei]["runorstop"] = "stop"
                        self.vardata[imei]["speed"] = 0
                        
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
                        
                    if onlinestatus == "离线" and self.vardata[imei].get("isonline","在线") == "在线":
                        self.vardata[imei]["lastofflinetime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.vardata[imei]["isonline"] = "离线"
                    if onlinestatus == "在线" and self.vardata[imei].get("isonline","离线") == "离线":
                        self.vardata[imei]["lastonlinetime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.vardata[imei]["isonline"] = "在线"
                
                    lastofflinetime = self.vardata[imei].get("lastofflinetime","")
                    lastonlinetime = self.vardata[imei].get("lastonlinetime","")
                    onlinestatus = self.vardata[imei].get("isonline","离线")
                    laststoptime = self.vardata[imei].get("laststoptime","")
                    lastruntime = self.vardata[imei].get("lastruntime","")
                    runorstop =  self.vardata[imei].get("runorstop","运动")
                    speed =  self.vardata[imei].get("speed",0)
                    course =  self.vardata[imei].get("course",0)
                    await self._persist_data()
                    
                    if laststoptime != "" and runorstop ==  "stop":
                        parkingtime=self.time_diff(int(time.mktime(time.strptime(laststoptime, "%Y-%m-%d %H:%M:%S")))) 
                    else:
                        parkingtime = ""
                    
                    attrs ={
                        "querytime": querytime,
                        "speed": speed,
                        "course": course,
                        "distance": distance,
                        "runorstop": runorstop,
                        "lastruntime": lastruntime,
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
