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

HELLOBIKE_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
HELLOBIKE_API_URL = "https://a.hellobike.com/evehicle/api"

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_hellobike = requests.session()
        self.cloudpgs_token = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'content-type': 'application/json; charset=utf-8',                    
            'User-Agent': HELLOBIKE_USER_AGENT
        }
        self.session_hellobike.headers.update(headers)
    
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

    def _devicelist_hellobike(self, token):
        url = HELLOBIKE_API_URL + "?rent.user.getUseBikePagePrimeInfoV3"
        p_data = {
            "token" : token,
            "action" : "rent.user.getUseBikePagePrimeInfoV3"
        }
        resp = self.session_hellobike.post(url, data=json.dumps(p_data)).json()        
        return resp
            
    def _get_device_tracker_hellobike(self, token, bikeNo):
        url = HELLOBIKE_API_URL + '?rent.order.getRentBikeStatus'
        p_data = {"bikeNo" : bikeNo,"token" : token,"action" : "rent.order.getRentBikeStatus"}
        resp = self.session_hellobike.post(url, data=json.dumps(p_data)).json()   
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
            deviceslistinfo = await self.hass.async_add_executor_job(self._devicelist_hellobike, self.password)
            _LOGGER.debug("deviceslistinfo: %s", deviceslistinfo)
            if deviceslistinfo.get("code") != 0:
                _LOGGER.error("请求api错误: %s", deviceslistinfo.get("msg"))
                return
            for deviceinfo in deviceslistinfo["data"].get("userBikeList"):
                self.deviceinfo[str(deviceinfo["bikeNo"])] = {}
            for deviceinfo in deviceslistinfo["data"].get("userBikeList"):
                self.deviceinfo[str(deviceinfo["bikeNo"])]["device_model"] = deviceinfo["modelName"]
                self.deviceinfo[str(deviceinfo["bikeNo"])]["sw_version"] = deviceinfo["tboxType"] + str(deviceinfo["pageVersionCode"]) +"." + str(deviceinfo["projectVersion"])
                self.deviceinfo[str(deviceinfo["bikeNo"])]["expiration"] = ""
             

        for imei in self.device_imei:
            _LOGGER.debug("Requests bikeNo: %s", imei)
            
            self.trackerdata[imei] = {}
                               
            try:
                async with timeout(10): 
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker_hellobike, self.password, imei)           
            except Exception as error:
                raise

            _LOGGER.debug("result data: %s", data)
            
            if data:
                defenceStatus = data["data"]["defenceStatus"]
                cusionSensorState = data["data"]["cusionSensorState"]
                mainBatteryEletric = data["data"]["mainBatteryEletric"]
                simRssi = data["data"]["simRssi"]
                lastHeartbeatTime = data["data"]["lastHeartbeatTime"]
                lastReportTimeNew = data["data"]["lastReportTimeNew"]
                lost = data["data"]["lost"]
                smallBatteryIslose = data["data"]["smallBatteryIslose"]
                supportBleProtocol = data["data"]["supportBleProtocol"]
                mainBatteryEletricWitchDecimal = data["data"]["mainBatteryEletricWitchDecimal"]
                smartCharge = data["data"]["smartCharge"]
                mileage = data["data"]["mileage"]
                headLampState = data["data"]["headLampState"]
                lastGpsLocTime = data["data"]["lastGpsLocTime"]
                smallBatteryResidueDays = data["data"]["smallBatteryResidueDays"]
                referPosition = data["data"]["referPosition"]
                batteryPercentTimeStamp = data["data"]["batteryPercentTimeStamp"]
                mainBatLossPercent = data["data"]["mainBatLossPercent"]
                electricityLevel = data["data"]["electricityLevel"]
                batteryPercent = data["data"]["batteryPercent"]
                position = data["data"]["position"]
                lastReportTime = data["data"]["lastReportTime"]
                mainBatChargeLeftTime = data["data"]["mainBatChargeLeftTime"]
                positionTimeStamp = data["data"]["positionTimeStamp"]
                smallEletric = data["data"]["smallEletric"]
                lockStatus = data["data"]["lockStatus"]
                lockLocalTime = data["data"]["lockLocalTime"]
                lockStatusTimeStamp = data["data"]["lockStatusTimeStamp"]
                address = data["data"]["address"]
                batteryVoltage = int(data["data"]["batteryVoltage"])/1000
                smallBatteryPercent = data["data"]["smallBatteryPercent"]
                requestTime = data["data"]["requestTime"]
            
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                lastreporttime = datetime.datetime.fromtimestamp(int(lastReportTime)/1000).strftime("%Y-%m-%d %H:%M:%S")
                lastreporttimenew = datetime.datetime.fromtimestamp(int(lastReportTimeNew)/1000).strftime("%Y-%m-%d %H:%M:%S")
                requesttime = datetime.datetime.fromtimestamp(int(requestTime)/1000).strftime("%Y-%m-%d %H:%M:%S")
                positiontime = datetime.datetime.fromtimestamp(int(positionTimeStamp)/1000).strftime("%Y-%m-%d %H:%M:%S")
                lockstatustime = datetime.datetime.fromtimestamp(int(lockStatusTimeStamp)/1000).strftime("%Y-%m-%d %H:%M:%S")
                speed = 0
                course = 0
                battery = batteryPercent
                
                if lockStatus == 0:
                    acc = "已锁车"
                    parkingtime = self.time_diff(int(time.mktime(time.strptime(lastreporttime, "%Y-%m-%d %H:%M:%S"))))
                elif lockStatus == 1:
                    acc = "已启动"
                    parkingtime = ""
                else:
                    acc = "未知"
                    
                if defenceStatus == 1:
                    status = "已设防"
                elif defenceStatus == 0:
                    status = "未设防"
                else:
                    status = "未知"
                    
                onlinestatus = "在线" if lost == 0 else "离线"
                _LOGGER.debug("position: %s", position)
                positions = list(map(float, position.split(",")))                  
                thislat = float(positions[1])
                thislon = float(positions[0])   
                laststoptime = lastreporttime
                updatetime = positiontime
                if speed == 0:
                    runorstop = "静止"                    
                else:
                    runorstop = "运动"
                    
                
                attrs = {
                    "speed":speed,
                    "course":course,
                    "querytime":querytime,
                    "laststoptime":laststoptime,
                    "last_update":updatetime,
                    "runorstop":runorstop,
                    "parkingtime":parkingtime,
                    "address":address,
                    "onlinestatus":onlinestatus,
                    "mileage":mileage,
                    "defence":status,
                    "acc":acc,
                    "lockstatustime":lockstatustime,
                    "battery":battery,
                    "powbatteryvoltage":mainBatteryEletricWitchDecimal,
                    "batteryvoltage":batteryVoltage,
                    "smallBatteryPercent":smallBatteryPercent,
                    "requesttime":requesttime,
                    "lastreporttimenew":lastreporttimenew,
                    "smartCharge":smartCharge
                }
                
                self.trackerdata[imei] = {"location_key":self.location_key+str(imei),"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
