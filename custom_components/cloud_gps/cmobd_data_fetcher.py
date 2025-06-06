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
import hashlib
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

CMOBD_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
CMOBD_API_URL = "https://lsapp.cmobd.com/v360/iovsaas"

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_cmobd = requests.session()
        self.cloudpgs_token = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}        
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'Host': 'lsapp.cmobd.com',                    
            'agent': 'Lushang/5.0.0',
            'Cookie': 'node-ls-api=' + password,
            'content-type': 'application/json',                    
            'User-Agent': CMOBD_USER_AGENT,
            'Referer': 'https://servicewechat.com/wx351871af12293380/31/page-frame.html'
        }
        self.session_cmobd.headers.update(headers)
    
    def _is_json(self, jsonstr):
        try:
            json.loads(jsonstr)
        except ValueError:
            return False
        return True
        
    def md5_hash(self, text):
        md5 = hashlib.md5()        
        md5.update(text.encode('utf-8'))        
        encrypted_text = md5.hexdigest()        
        return encrypted_text

    def _devicelist_cmobd(self, token):
        url = CMOBD_API_URL
        p_data = {
            "cmd":"userVehicles",
            "ver":1,
            "token": token,
            "pageNo":0,
            "pageSize":10
        }
        resp = self.session_cmobd.post(url, data=p_data).json()        
        return resp
            
    def _get_device_tracker(self, token, vehicleid):
        url = CMOBD_API_URL
        p_data = {
           "cmd": "weappVehicleRunStatus", 
           "ver": 1, 
           "token": token, 
           "vehicleId": vehicleid, 
           "isNeedGps": "1", 
           "gpsStartTime": ""
        }
        resp = self.session_cmobd.post(url, data=p_data).json()   
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
    
        if self.deviceinfo == {}:
            deviceslistinfo = await self.hass.async_add_executor_job(self._devicelist_cmobd, self.password)
            _LOGGER.debug("deviceslistinfo: %s", deviceslistinfo)
            if deviceslistinfo.get("result") != 0:
                _LOGGER.error("请求api错误: %s", deviceslistinfo.get("note"))
                return
            for deviceinfo in deviceslistinfo["dataList"]:
                self.deviceinfo[str(deviceinfo["vehicleID"])] = {}
            for deviceinfo in deviceslistinfo["dataList"]:
                self.deviceinfo[str(deviceinfo["vehicleID"])]["device_model"] = "中移行车卫士" + deviceinfo["deviceList"][0]["deviceTypeName"]
                self.deviceinfo[str(deviceinfo["vehicleID"])]["sw_version"] = deviceinfo["deviceList"][0]["modelName"]
                self.deviceinfo[str(deviceinfo["vehicleID"])]["expiration"] = "永久"
             

        for imei in self.device_imei:
            _LOGGER.debug("Requests vehicleID: %s", imei)
                               
            try:
                async with timeout(10): 
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker, self.password, imei)
            except ClientConnectorError as error:
                _LOGGER.error("连接错误: %s", error)
            except asyncio.TimeoutError:
                _LOGGER.error("获取数据超时 (10秒)")
            except Exception as e:
                _LOGGER.error("未知错误: %s", repr(e))
            finally:
                _LOGGER.debug("最终数据结果: %s", data)
            
            if data.get("result") == 0:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updatetime = data.get("sampleTime")
                speed = float(data.get("vehicleSpeed", 0))
                course = data.get("posDirection", 0)
                address = data.get("realLocation","")
                battery = int(data.get("soc", 0))/10
                
                status = "停车"
                
                if data["vehicleStatus"] == "1":
                    acc = "钥匙开启"
                    status = "钥匙开启"
                elif data["vehicleStatus"] == "0":
                    acc = "钥匙关闭"
                else:
                    acc = "未知"
                    
            
                thislat = float(data["posLatitude"])
                thislon = float(data["posLongitude"])   
                
                if data["stopTime"]:
                    laststoptime = data["stopTime"]
                    parkingtime = self.time_diff(int(time.mktime(time.strptime(data["stopTime"], "%Y-%m-%d %H:%M:%S"))))
                else:
                    laststoptime = None
                    parkingtime = ""

                if speed == 0:
                    runorstop = "静止"
                else:
                    runorstop = "运动"
                    status = "行驶"
                    
                if data["onlineStatus"] == "2":
                    onlinestatus = "在线" 
                elif data["onlineStatus"] == "1":
                    onlinestatus = "待机"
                else:
                    onlinestatus = "离线"
                    status = "离线"
  
                if data["powerStatus"] != "0":
                    status = "外电已断开"
                
                attrs = {
                    "speed":speed,
                    "course":course,
                    "querytime":querytime,
                    "laststoptime":laststoptime,
                    "last_update":updatetime,
                    "runorstop":runorstop,
                    "acc":acc,
                    "parkingtime":parkingtime,
                    "address":address,
                    "onlinestatus":onlinestatus,
                    "battery":battery
                }
                
                self.trackerdata[imei] = {"location_key":self.location_key+str(imei),"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
