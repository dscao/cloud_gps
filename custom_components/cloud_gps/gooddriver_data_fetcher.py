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

USER_AGENT = 'gooddriver/7.9.1 CFNetwork/1410.0.3 Darwin/22.6.0'
GOODDRIVER_API_HOST_TOKEN = "https://ssl.gooddriver.cn"
GOODDRIVER_API_TRACKER_URL = "http://restcore.gooddriver.cn/API/Values/HudDeviceDetail/" 

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_gooddriver = requests.session()
        self.cloudpgs_token = None
        self.u_id = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}        
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'User-Agent': USER_AGENT,
            'SDF': '6928FAA6-B970-F5A5-85F0-73D4299D99A8',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self.session_gooddriver.headers.update(headers)
    
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

    def _login(self, username, password):
        p_data = {
            'U_ACCOUNT': username,
            'U_PASSWORD': self.md5_hash(password)
        }
        url = GOODDRIVER_API_HOST_TOKEN + '/UserServices/Login2018'
        response = self.session_gooddriver.post(url, data=json.dumps(p_data))       
        if response.json()['ERROR_CODE'] == 0:
            #self.cloudpgs_token = response.json()["MESSAGE"]["U_ACCESS_TOKEN"]
            return response.json()["MESSAGE"]
        else:
            _LOGGER.error(response.json())
            return None   
            
    def _get_device_tracker(self, uv_id):
        url = GOODDRIVER_API_TRACKER_URL + str(uv_id)        
        resp = self.session_gooddriver.get(url)
        return resp.json()['MESSAGE']
     
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
    
        if self.u_id is None:
            deviceslistinfo = await self.hass.async_add_executor_job(self._login, self.username, self.password)
            _LOGGER.debug("deviceslistinfo: %s", deviceslistinfo)
            for deviceinfo in deviceslistinfo["USER_VEHICLEs"]:
                self.deviceinfo[str(deviceinfo["UV_ID"])] = {}
            for deviceinfo in deviceslistinfo["USER_VEHICLEs"]:
                self.deviceinfo[str(deviceinfo["UV_ID"])]["device_model"] = deviceinfo["DEVICE"]["P_MODEL"]
                self.deviceinfo[str(deviceinfo["UV_ID"])]["sw_version"] = deviceinfo["DEVICE"]["D_ATI_VERSION"]
                self.deviceinfo[str(deviceinfo["UV_ID"])]["expiration"] = "永久"
                self.totalkm[str(deviceinfo["UV_ID"])] = deviceinfo["UV_CURRENT_MILEAGE"]
                

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            self.trackerdata[imei] = {}
                               
            try:
                async with timeout(10): 
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker, imei)
            except (
                ClientConnectorError
            ) as error:
                raise
            
            except Exception as error:
                raise UpdateFailed(error)

            _LOGGER.debug("result data: %s", data)
            
            if data:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updatetime = data["HD_STATE_TIME"]
                imei = str(data["UV_ID"])
                recent_location = json.loads(data["HD_RECENT_LOCATION"])
                course = recent_location["Course"]
                speed = float(recent_location["Speed"])
                _LOGGER.debug("speed: %s", speed)
                if data["HD_STATE"] == 1:
                    acc = "车辆点火"
                elif data["HD_STATE"] == 2:
                    acc = "车辆熄火"
                else:
                    acc = "未知"
                                  
                thislat = float(recent_location["Lat"])
                thislon = float(recent_location["Lng"])              
                laststoptime = recent_location["Time"]
                                      
                positionType = "GPS"
                if speed == 0:
                    runorstop = "静止"
                    parkingtime = self.time_diff(int(time.mktime(time.strptime(laststoptime, "%Y-%m-%d %H:%M:%S"))))  
                else:
                    runorstop = "运动"
                    parkingtime = ""
                    
                status = runorstop

                totalKm = self.totalkm[imei]
                
                attrs = {
                    "speed":speed,
                    "course":course,
                    "querytime":querytime,
                    "laststoptime":laststoptime,
                    "last_update":updatetime,
                    "runorstop":runorstop,
                    "acc":acc,
                    "parkingtime":parkingtime,
                    "totalKm":totalKm
                }
                
                self.trackerdata[imei] = {"location_key":self.location_key+str(imei),"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
