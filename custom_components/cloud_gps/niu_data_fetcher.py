"""
get info
请求数据的核心代码来源： https://github.com/goxofy/home-assistant-niu-component/blob/master/custom_components/niu/sensor.py
"""

import logging
import requests
import re
import asyncio
import json
import time
import datetime
import hashlib
from time import gmtime, strftime
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

NIU_USER_AGENT = 'manager/4.6.48 (android; IN2020 11);lang=zh-CN;clientIdentifier=Domestic;timezone=Asia/Shanghai;model=IN2020;deviceName=IN2020;ostype=android'
NIU_API_HOST_TOKEN = "https://account.niu.com"
NIU_API_HOST_TRACKER = "https://app-api.niu.com"
NIU_LOGIN_URI = "/v3/api/oauth2/token"
NIU_MOTOR_BATTERY_API_URI = "/v3/motor_data/battery_info"
NIU_MOTOR_INDEX_API_URI = "/v5/scooter/motor_data/index_info"
NIU_MOTOINFO_LIST_API_URI = "/v5/scooter/list"
NIU_MOTOINFO_ALL_API_URI = "/motoinfo/overallTally"
NIU_TRACK_LIST_API_URI = "/v5/track/list/v2"

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        #self.session_niu = requests.session()
        self.cloudpgs_token = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}        
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'User-Agent': NIU_USER_AGENT
        }
        #self.session_niu.headers.update(headers)
    
    def _get_niu_token(self, username, password):
        url = NIU_API_HOST_TOKEN + '/v3/api/oauth2/token'
        md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
        data = {
            "account": username,
            "password": md5,
            "grant_type": "password",
            "scope": "base",
            "app_id": "niu_ktdrr960",
        }
        try:
            r = requests.post(url, data=data)
        except BaseException as e:
            print(e)
            return False
        data = json.loads(r.content.decode())
        return data["data"]["token"]["access_token"]


    def _get_niu_vehicles_info(self, token):

        url = NIU_API_HOST_TRACKER + '/v5/scooter/list'
        headers = {"token": token}
        try:
            r = requests.get(url, headers=headers, data=[])
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        return data


    def _get_niu_info(self, path, sn, token):
        url = NIU_API_HOST_TRACKER + path

        params = {"sn": sn}
        headers = {
            "token": token,
            "Accept-Language": "en-US",
            "user-agent": NIU_USER_AGENT
        }
        try:

            r = requests.get(url, headers=headers, params=params)

        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data


    def _post_niu_info(self, path, sn, token):
        url = NIU_API_HOST_TRACKER + path
        params = {}
        headers = {
            "token": token,
            "Accept-Language": "en-US",
            "User-Agent": NIU_USER_AGENT
        }
        try:
            r = requests.post(url, headers=headers, params=params, data={"sn": sn})
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data


    def _post_niu_info_track(self, path, sn, token):
        url = NIU_API_HOST_TRACKER + path
        params = {}
        headers = {
            "token": token,
            "Accept-Language": "en-US",
            "User-Agent": NIU_USER_AGENT
        }
        try:
            r = requests.post(
                url,
                headers=headers,
                params=params,
                json={"index": "0", "pagesize": 10, "sn": sn},
            )
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data
     
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

        if self.cloudpgs_token is None:
            self.cloudpgs_token = await self.hass.async_add_executor_job(self._get_niu_token, self.username, self.password)
            _LOGGER.debug("get niu token: %s", self.cloudpgs_token)  
            if self.cloudpgs_token:
                deviceslistinfo = await self.hass.async_add_executor_job(self._get_niu_vehicles_info, self.cloudpgs_token)
                for deviceinfo in deviceslistinfo["data"]["items"]:
                    self.deviceinfo[str(deviceinfo["sn_id"])] = {}
                for deviceinfo in deviceslistinfo["data"]["items"]:
                    self.deviceinfo[str(deviceinfo["sn_id"])]["device_model"] = "小牛电动车"
                    self.deviceinfo[str(deviceinfo["sn_id"])]["sw_version"] = "未知"
                    self.deviceinfo[str(deviceinfo["sn_id"])]["expiration"] = "永久"

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            
            if not self.deviceinfo.get(imei):
                self.deviceinfo[imei] = {}
                try:
                    async with timeout(10): 
                        infodata =  await self.hass.async_add_executor_job(self.post_niu_info, imei)
                except ClientConnectorError as error:
                    _LOGGER.error("连接错误: %s", error)
                except asyncio.TimeoutError:
                    _LOGGER.error("获取数据超时 (10秒)")
                except Exception as e:
                    _LOGGER.error("未知错误: %s", repr(e))
                finally:
                    _LOGGER.debug("最终数据结果: %s", infodata)
                
                if infodata:
                    self.deviceinfo[imei] =infodata
                    self.deviceinfo[imei]["device_model"] = "小牛电动车"
                    self.deviceinfo[imei]["sw_version"] = "未知"
                    self.deviceinfo[imei]["expiration"] = "永久"
            
            
            self.batterydata[imei] = {}
            try:
                async with timeout(10): 
                    batterydata =  await self.hass.async_add_executor_job(self._get_niu_info, "/v3/motor_data/battery_info", imei, self.cloudpgs_token)          
            except Exception as error:
                raise
            _LOGGER.debug("result battery data: %s", batterydata)
            if batterydata:
                self.batterydata[imei] = {
                    "BatteryCharge": batterydata["data"]["batteries"]["compartmentA"]["batteryCharging"],
                    "BatteryIsconnected": batterydata["data"]["batteries"]["compartmentA"]["isConnected"],
                    "BatteryTimesCharged": batterydata["data"]["batteries"]["compartmentA"]["chargedTimes"],
                    "BatterytemperatureDesc": batterydata["data"]["batteries"]["compartmentA"]["temperatureDesc"],
                    "BatteryTemperature": batterydata["data"]["batteries"]["compartmentA"]["temperature"],
                    "BatteryGrade": batterydata["data"]["batteries"]["compartmentA"]["gradeBattery"]
                }                
            
            
            self.motoinfodata[imei] = {}
            try:
                async with timeout(10): 
                    motoinfodata =  await self.hass.async_add_executor_job(self._post_niu_info, "/motoinfo/overallTally", imei, self.cloudpgs_token)
            except Exception as error:
                raise
            _LOGGER.debug("result motoinfo data: %s", motoinfodata)
            if motoinfodata:
                self.motoinfodata[imei] = {
                    "totalMileage": motoinfodata["data"]["totalMileage"],
                    "DaysInUse": motoinfodata["data"]["bindDaysCount"]
                }
                
                
            self.infotrackdata[imei] = {}
            try:
                async with timeout(10): 
                    infotrackdata =  await self.hass.async_add_executor_job(self._post_niu_info_track, "/v5/track/list/v2", imei, self.cloudpgs_token)
            except Exception as error:
                raise
            _LOGGER.debug("result infotrack data: %s", infotrackdata)
            if infotrackdata:
                self.infotrackdata[imei] = {
                    "LastTrackStartTime": datetime.fromtimestamp((infotrackdata["data"][0]["startTime"]) / 1000 ).strftime("%Y-%m-%d %H:%M:%S"),
                    "LastTrackEndTime": datetime.fromtimestamp((infotrackdata["data"][0]["endTime"]) / 1000 ).strftime("%Y-%m-%d %H:%M:%S"),
                    "LastTrackDistance": infotrackdata["data"][0]["distance"],
                    "LastTrackAverageSpeed"：infotrackdata["data"][0]["avespeed"],
                    "LastTrackRidingtime": strftime("%H:%M:%S", gmtime(infotrackdata["data"][0]["ridingtime"]))
                    "LastTrackThumb": infotrackdata["data"][0]["track_thumb"].replace("app-api.niucache.com", "app-api.niu.com"}


                
            self.motodata[imei] = {}
            try:
                async with timeout(10): 
                    motodata =  await self.hass.async_add_executor_job(self._get_niu_info, "/v5/scooter/motor_data/index_info", imei, self.cloudpgs_token)          
            except Exception as error:
                raise
            _LOGGER.debug("result moto data: %s", motodata)
            if motodata:
                self.motodata[imei] = {
                    "CurrentSpeed": motodata["data"]["nowSpeed"],
                    "ScooterConnected": motodata["data"]["isConnected"],
                    "IsCharging": motodata["data"]["isCharging"],
                    "IsLocked": motodata["data"]["lockStatus"],
                    "TimeLeft": motodata["data"]["leftTime"],
                    "EstimatedMileage": motodata["data"]["estimatedMileage"],
                    "centreCtrlBatt": motodata["data"]["centreCtrlBattery"],
                    "HDOP": motodata["data"]["hdop"],
                    "Distance": motodata["data"]["lastTrack"]["distance"],
                    "RidingTime": motodata["data"]["lastTrack"]["ridingTime"],
                    "Longitude": motodata["data"]["postion"]["lng"],
                    "Latitude": motodata["data"]["postion"]["lat"],
                }

            
            if self.motodata[imei]:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updatetime = ""
                course = ""
                speed = float(self.motodata[imei]["CurrentSpeed"])
                _LOGGER.debug("speed: %s", speed)
                
                status = "停车"
                
                if self.motodata[imei]["IsLocked"] == 1:
                    acc = "已锁车"
                elif data["HD_STATE"] == 0:
                    acc = "已开锁"
                    status = "钥匙开启"
                else:
                    acc = "未知"
    
                thislat = float(self.motodata[imei]["Latitude"])
                thislon = float(self.motodata[imei]["Longitude"])              
                laststoptime = self.motodata[imei]["TimeLeft"]
                parkingtime = self.time_diff(int(time.mktime(time.strptime(laststoptime, "%Y-%m-%d %H:%M:%S"))))                        
                positionType = "GPS"
                if speed == 0:
                    runorstop = "静止"
                else:
                    runorstop = "运动"
                    status = "行驶"
                    
                if self.motodata[imei]["ScooterConnected"] == 1:
                    onlinestatus = "在线"
                elif data["HD_STATE"] == 0:
                    onlinestatus = "离线"
                    status = "离线"
                else:
                    onlinestatusstatus = "未知"
                    
                attrs = {
                    "speed":speed,
                    "course":course,
                    "querytime":querytime,
                    "laststoptime":laststoptime,
                    "last_update":updatetime,
                    "acc":acc,
                    "runorstop":runorstop,
                    "onlinestatus", onlinestatus,
                    "parkingtime":parkingtime
                }
                
                attrs.update(self.infotrackdata[imei])
                attrs.update(self.batterydata[imei])                    
                
                self.trackerdata[imei] = {"location_key":self.location_key+str(imei),"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"status":status,"attrs": attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
