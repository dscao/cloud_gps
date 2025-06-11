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

TUQIANGNET_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
TUQIANGNET_API_HOST = "http://www.tuqiang.net"

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_tuqiangnet = requests.session()
        self.cloudpgs_token = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'User-Agent': TUQIANGNET_USER_AGENT
        }
        self.session_tuqiangnet.headers.update(headers)    
        
    def _login(self, username, password):
        p_data = {
            'timeZone': '28800',
            'token': '',
            'userName': username,
            'password': password,
            'lang': 'zh'
        }
        url = TUQIANGNET_API_HOST + '/loginVerification'
        response = self.session_tuqiangnet.post(url, data=p_data)
        _LOGGER.debug("TUQIANGNET_API_HOST cookies: %s", self.session_tuqiangnet.cookies)
        _LOGGER.debug(response.json())
        if response.json()['code'] == 0:
            self.cloudpgs_token = response.json()["data"]["token"]
            return True
        else:
            return False
            
    def _get_device_info(self, imei_sn):        
        url = TUQIANGNET_API_HOST + '/device/getDeviceList'
        p_data = {
            "imeis": imei_sn,
            "token": self.cloudpgs_token
        }
        resp = self.session_tuqiangnet.post(url, data=p_data)
        return resp.json()['data'][0]
            
    def _get_device_tracker(self, imei_sn):
        url = TUQIANGNET_API_HOST + '/redis/getGps'
        p_data = {
            "imei": imei_sn,
            "token": self.cloudpgs_token
        }
        resp = self.session_tuqiangnet.post(url, data=p_data)
        return resp.json()['data']
        
    def _get_device_totalMileage(self, imei_sn):
        url = TUQIANGNET_API_HOST + '/redis/getDeviceOther'
        p_data = {
            "imei": imei_sn,
            "token": self.cloudpgs_token
        }
        resp = self.session_tuqiangnet.post(url, data=p_data)
        _LOGGER.debug("result totalMileage: %s", resp.json())
        return round(float(resp.json()['data']['totalMileage'])/1000, 2) if resp.json()['data'].get('totalMileage')!= None else 0

            
    def _get_device_address(self, lat, lng):
        url = TUQIANGNET_API_HOST + '/comm/getGpsAddr'
        p_data = {
            "lat": lat,
            "lon": lng,
            "token": self.cloudpgs_token
        }
        resp = self.session_tuqiangnet.post(url, data=p_data)
        return resp.json()["data"]
    
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
            await self.hass.async_add_executor_job(self._login, self.username, self.password)        
        _LOGGER.debug(self.device_imei)
        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):
                infodata = None
                try:
                    async with timeout(10): 
                        infodata =  await self.hass.async_add_executor_job(self._get_device_info, imei)
                        _LOGGER.debug("途强物联 %s 最终数据结果: %s", imei, infodata)
                except ClientConnectorError as error:
                    _LOGGER.error("途强物联 %s 连接错误: %s", imei, error)
                except asyncio.TimeoutError:
                    _LOGGER.error("途强物联 %s 获取数据超时 (10秒)", imei)
                except Exception as e:
                    _LOGGER.error("途强物联 %s 未知错误: %s", imei, repr(e))

                if infodata:
                    self.deviceinfo[imei] =infodata
                    self.deviceinfo[imei]["device_model"] = "途强物联GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["deviceModel"]
                    self.deviceinfo[imei]["expiration"] = infodata["expirationTime"]
            
            data = None            
            try:
                async with timeout(10): 
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker, imei)
                    _LOGGER.debug("最终数据结果: %s", data)
            except ClientConnectorError as error:
                _LOGGER.error("连接错误: %s", error)
            except asyncio.TimeoutError:
                _LOGGER.error("获取数据超时 (10秒)")
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)                
                raise UpdateFailed(e)

            if data:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updatetime = data["hbTime"]                
                direction = data["direction"]
                speed = float(data.get("speed",0))             
                
                status = "停车"
                
                if data['acc'] == "1":
                    acc = "钥匙开启"
                    status = "钥匙开启"
                else:
                    acc = "钥匙关闭"
                     
                thislat = float(data["latitude"])
                thislon = float(data["longitude"])
                voltage = data['extVol']
                percentageElectricQuantity = data['percentageElectricQuantity']
                laststoptime = data["statusUpdateTime"]
                if speed == 0:
                    parkingtime = self.time_diff(int(time.mktime(time.strptime(laststoptime, "%Y-%m-%d %H:%M:%S"))))
                    runorstop = "静止"  
                else:
                    parkingtime = ""
                    runorstop = "运动"
                    status = "行驶"
                positionType = "GPS" if data["locType"] == "0" else "基站定位"
                if data['status'] == "2":
                    onlinestatus = "在线"
                elif data['status'] == "3":
                    onlinestatus = "在线"
                else:
                    status = "离线"
                    onlinestatus = "离线"
                    
                if data.get("oilState") == 1:
                    powerStatus = "已接通"
                else:
                    powerStatus = "已断开"
                    status = "外电已断开"
                    
                if self._lat_old != thislat or self._lon_old != thislon:
                    self.address[imei] = await self.hass.async_add_executor_job(self._get_device_address, thislat, thislon)
                    self.totalkm[imei] = await self.hass.async_add_executor_job(self._get_device_totalMileage, imei)
                    self._lat_old = thislat
                    self._lon_old = thislon                
                
                address = self.address[imei]
                totalKm = self.totalkm[imei]
                
                attrs = {
                    "course":direction,
                    "speed":speed,
                    "querytime":querytime,
                    "laststoptime":laststoptime,
                    "last_update":updatetime,
                    "runorstop":runorstop,
                    "onlinestatus": onlinestatus,
                    "acc":acc,
                    "powerStatus":powerStatus,
                    "parkingtime":parkingtime,
                    "address":address,
                    "powbatteryvoltage":voltage,
                    "percentageElectricQuantity": percentageElectricQuantity,
                    "totalKm":totalKm,
                    "positionType":positionType
                }
                
                self.trackerdata[imei] = {"location_key":self.location_key+str(imei),"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"imei":imei,"status":status,"attrs":attrs}

        return self.trackerdata


class GetDataError(Exception):
    """request error or response data is unexpected"""
