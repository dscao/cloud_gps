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

TUQIANG_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36'
TUQIANG123_API_HOST = "https://www.tuqiang123.com"   # http://www.tuqiangol.com 或者 http://www.tuqiang123.com

class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.session_tuqiang123 = requests.session()
        self.userid = None
        self.usertype = None
        self._lat_old = 0
        self._lon_old = 0
        self.deviceinfo = {}
        self.trackerdata = {}
        self.today_mileagedata = {}
        self.yesterday_mileagedata = {}
        self.month_mileagedata = {}
        self.year_mileagedata = {}
        self.address = {}
        self.totalkm = {}
        
        headers = {
            'User-Agent': TUQIANG_USER_AGENT
        }
        self.session_tuqiang123.headers.update(headers)    

    def _encode(self, code):
        en_code = ''
        for s in code:
            en_code = en_code + str(ord(s)) + '|'
        return en_code[:-1]

    def _login(self, username, password):
        p_data = {
            'ver': '1',
            'method': 'login',
            'account': username,
            'password': self._encode(password),
            'language': 'zh'
        }
        url = TUQIANG123_API_HOST + '/api/regdc'
        response = self.session_tuqiang123.post(url, data=p_data)
        _LOGGER.debug("TUQIANG123_API_HOST cookies: %s", self.session_tuqiang123.cookies)
        _LOGGER.debug(response.json())
        if response.json()['code'] == 0:
            self._get_userid()
            return True
        else:
            return False

    def _get_userid(self):
        url = TUQIANG123_API_HOST + '/customer/getProviderList'
        resp = self.session_tuqiang123.post(url, data=None).json()
        self.userid = resp['data']['user']['userId']
        self.usertype = resp['data']['user']['type']

    def _get_device_info(self, imei_sn):        
        url = TUQIANG123_API_HOST + '/device/list'
        p_data = {
            'dateType': 'activation',
            'equipment.userId': self.userid
        }
        resp = self.session_tuqiang123.post(url, data=p_data)

        return resp.json()['data']['result'][0]
            
    def _get_device_tracker(self, imei_sn):
        url = TUQIANG123_API_HOST + '/console/refresh'
        p_data = {
            'choiceUserId': self.userid,
            'normalImeis': str(imei_sn),
            'userType': self.usertype,
            'followImeis': '',
            'userId': self.userid,
            'stock': '2'
        }
        resp = self.session_tuqiang123.post(url, data=p_data)
        return resp.json()['data']['normalList'][0]

    def _get_device_today_mileage(self, imei_sn):
        url = TUQIANG123_API_HOST + '/mileageReportController/getList'
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        start_time = f"{today_str} 00:00"
        end_time = f"{today_str} 23:59"
        p_data = {
            'imeis': str(imei_sn),
            'userType': self.usertype,
            'followImeis': '',
            'userId': self.userid,
            'stock': '2',
            'startTime': start_time,
            'endTime': end_time,
            'pageNo': '1',
            'startRow': '1',
            'pageSize': '20',
            'type': 'segment'
        }
        try:
            resp = self.session_tuqiang123.post(url, data=p_data)
            _LOGGER.debug(resp.json()['data']['result'][0])
            return resp.json()['data']['result'][0]
        except Exception as e:
            _LOGGER.debug(e)
            return None


    def _get_device_yesterday_mileage(self, imei_sn):
        url = TUQIANG123_API_HOST + '/mileageReportController/getList'
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')
        start_time = f"{yesterday_str} 00:00"
        end_time = f"{yesterday_str} 23:59"
        p_data = {
            'imeis': str(imei_sn),
            'userType': self.usertype,
            'followImeis': '',
            'userId': self.userid,
            'stock': '2',
            'startTime': start_time,
            'endTime': end_time,
            'pageNo': '1',
            'startRow': '1',
            'pageSize': '20',
            'type': 'segment'
        }
        try:
            resp = self.session_tuqiang123.post(url, data=p_data)
            _LOGGER.debug(resp.json()['data']['result'][0])
            return resp.json()['data']['result'][0]
        except Exception as e:
            _LOGGER.debug(e)
            return None

    def _get_device_month_mileage(self, imei_sn):
        url = TUQIANG123_API_HOST + '/mileageReportController/getList'
        current_month = datetime.datetime.now().strftime("%Y-%m")
        start_time = f"{current_month}-01 00:00"
        end_time = f"{current_month}-{datetime.datetime.now().day} 23:59"
        p_data = {
            'imeis': str(imei_sn),
            'userType': self.usertype,
            'followImeis': '',
            'userId': self.userid,
            'stock': '2',
            'startTime': start_time,
            'endTime': end_time,
            'pageNo': '1',
            'startRow': '1',
            'pageSize': '20',
            'type': 'segment'
        }
        try:
            resp = self.session_tuqiang123.post(url, data=p_data)
            _LOGGER.debug(resp.json()['data']['result'][0])
            return resp.json()['data']['result'][0]
        except Exception as e:
            _LOGGER.debug(e)
            return None

    def _get_device_year_mileage(self, imei_sn):
        url = TUQIANG123_API_HOST + '/mileageReportController/getList'
        current_year = datetime.datetime.now().strftime("%Y")
        start_time = f"{current_year}-01-01 00:00"
        end_time = f"{current_year}-12-31 23:59"
        p_data = {
            'imeis': str(imei_sn),
            'userType': self.usertype,
            'followImeis': '',
            'userId': self.userid,
            'stock': '2',
            'startTime': start_time,
            'endTime': end_time,
            'pageNo': '1',
            'startRow': '1',
            'pageSize': '20',
            'type': 'segment'
        }
        try:
            resp = self.session_tuqiang123.post(url, data=p_data)
            _LOGGER.debug(resp.json()['data']['result'][0])
            return resp.json()['data']['result'][0]
        except Exception as e:
            _LOGGER.debug(e)
            return None

    def _get_device_address(self, lat, lng):
        url = TUQIANG123_API_HOST + '/getAddress?lat='+str(lat)+'&lng='+str(lng)+'&mapType=baiduMap&poiList='
        resp = self.session_tuqiang123.get(url)
        return resp.json()['msg']
    
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
    
        _LOGGER.debug(self.device_imei)
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10): 
                        infodata =  await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                    ClientConnectorError
                ) as error:
                    raise

                _LOGGER.debug("result infodata: %s", infodata)
                
                if infodata:
                    self.deviceinfo[imei] =infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["mcType"]
                    self.deviceinfo[imei]["expiration"] = infodata["expiration"]
            data = None        
            try:
                async with timeout(10): 
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker, imei)
                    _LOGGER.debug("途强在线 %s 最终数据结果: %s", imei, data)
            except ClientConnectorError as error:
                _LOGGER.error("途强在线 %s 连接错误: %s", imei, error)
            except asyncio.TimeoutError:
                _LOGGER.error("途强在线 %s 获取数据超时 (10秒)", imei)
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)
                raise UpdateFailed(e)
 
            
            if data:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updatetime = data["hbTime"]
                imei = data["imei"]
                
                direction = data["direction"]
                speed = data.get("speed",0)
                gpssignal = data.get("gPSSignal", 0)

                onlinestatus = "在线"
                status = "停车"

                if data['acc'] == "1":
                    acc = "钥匙启动"
                    status = "钥匙启动"
                else:
                    acc = "钥匙关闭"

                thislat = float(data["lat"])
                thislon = float(data["lng"])

                if data["status"] == "STATIC":
                    runorstop = "静止"
                    speed = 0
                    parkingtime = data["statusStr"]
                    statustime = data["statusStr"]
                elif data["status"] == "MOVE":
                    runorstop = "运动"
                    speed = float(data.get("speed",0))
                    parkingtime = ""
                    statustime = data["statusStr"]
                    status = "行驶"
                elif data["status"] == "OFFLINE":
                    runorstop = "离线"
                    onlinestatus = "离线"
                    status = "离线"
                    speed = 0
                    parkingtime = data.get("statusAbstract")
                    statustime = data["statusStr"]
                else:
                    runorstop = "未知"
                    speed = 0
                    parkingtime = ""
                    statustime = ""

                if data.get("powerStatus") == "1":
                    powerStatus = "已接通"
                else:
                    powerStatus = "已断开"
                    status = "外电已断开"

                voltage = "0" if data["voltage"]=="" else data["voltage"]
                laststoptime = data["gpsTime"]
                positionType = data["positionType"] if speed==0 else ""

                if self._lat_old != thislat or self._lon_old != thislon:
                    self.address[imei] = await self.hass.async_add_executor_job(self._get_device_address, thislat, thislon)
                    self._lat_old = thislat
                    self._lon_old = thislon

                address = self.address.get(imei, "未知")

                try:
                    totalKm = float(data.get("totalKm", self.totalkm.get(imei, 0)))
                except (ValueError, TypeError):
                    _LOGGER.warning(f"无效的里程数据: {data.get('totalKm')}, 设备IMEI: {imei}")
                    totalKm = self.totalkm.get(imei, 0)

                attrs ={
                    "course":direction,
                    "speed":speed,
                    "gpssignal": gpssignal,
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
                    "totalKm":totalKm,
                    "positionType":positionType,
                    "statustime": statustime
                }
                
                self.trackerdata[imei] = {"location_key":self.location_key+imei,"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"imei":imei,"status":status,"attrs":attrs}

        return self.trackerdata

    async def get_today_mileage(self):

        _LOGGER.debug(self.device_imei)
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10):
                        infodata = await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                        ClientConnectorError
                ) as error:
                    raise

                _LOGGER.debug("result infodata: %s", infodata)

                if infodata:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["mcType"]
                    self.deviceinfo[imei]["expiration"] = infodata["expiration"]
            data = None
            try:
                async with timeout(10):
                    data = await self.hass.async_add_executor_job(self._get_device_today_mileage, imei)
                    _LOGGER.debug("途强在线 %s 最终数据结果: %s", imei, data)
            except ClientConnectorError as error:
                _LOGGER.error("途强在线 %s 连接错误: %s", imei, error)
            except asyncio.TimeoutError:
                _LOGGER.error("途强在线 %s 获取数据超时 (10秒)", imei)
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)
                raise UpdateFailed(e)

            if data:
                imei = data["imei"]
                today_dis = data["dis"]
                attrs = {
                    "today_dis": today_dis,
                }
                self.today_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "today_dis": today_dis,
                    "attrs": attrs
                }
            else:
                today_dis = 0
                attrs = {
                    "today_dis": 0,
                }
                self.today_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "today_dis": today_dis,
                    "attrs": attrs
                }
        return self.today_mileagedata

    async def get_yesterday_mileage(self):

        _LOGGER.debug(self.device_imei)
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10):
                        infodata = await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                        ClientConnectorError
                ) as error:
                    raise

                _LOGGER.debug("result infodata: %s", infodata)

                if infodata:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["mcType"]
                    self.deviceinfo[imei]["expiration"] = infodata["expiration"]
            data = None
            try:
                async with timeout(10):
                    data = await self.hass.async_add_executor_job(self._get_device_yesterday_mileage, imei)
                    _LOGGER.debug("途强在线 %s 最终数据结果: %s", imei, data)
            except ClientConnectorError as error:
                _LOGGER.error("途强在线 %s 连接错误: %s", imei, error)
            except asyncio.TimeoutError:
                _LOGGER.error("途强在线 %s 获取数据超时 (10秒)", imei)
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)
                raise UpdateFailed(e)

            if data:
                imei = data["imei"]
                yesterday_dis = data["dis"]
                attrs = {
                    "yesterday_dis": yesterday_dis,
                }
                self.yesterday_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "yesterday_dis": yesterday_dis,
                    "attrs": attrs
                }
            else:
                yesterday_dis = 0
                attrs = {
                    "yesterday_dis": 0,
                }
                self.yesterday_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "yesterday_dis": yesterday_dis,
                    "attrs": attrs
                }
        return self.yesterday_mileagedata

    async def get_month_mileage(self):

        _LOGGER.debug(self.device_imei)
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10):
                        infodata = await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                        ClientConnectorError
                ) as error:
                    raise

                _LOGGER.debug("result infodata: %s", infodata)

                if infodata:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["mcType"]
                    self.deviceinfo[imei]["expiration"] = infodata["expiration"]
            data = None
            try:
                async with timeout(10):
                    data = await self.hass.async_add_executor_job(self._get_device_month_mileage, imei)
                    _LOGGER.debug("途强在线 %s 最终数据结果: %s", imei, data)
            except ClientConnectorError as error:
                _LOGGER.error("途强在线 %s 连接错误: %s", imei, error)
            except asyncio.TimeoutError:
                _LOGGER.error("途强在线 %s 获取数据超时 (10秒)", imei)
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)
                raise UpdateFailed(e)

            if data:
                imei = data["imei"]
                month_dis = data["dis"]
                attrs = {
                    "month_dis": month_dis,
                }
                self.month_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "month_dis": month_dis,
                    "attrs": attrs
                }
            else:
                month_dis = 0
                attrs = {
                    "month_dis": 0,
                }
                self.month_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "month_dis": month_dis,
                    "attrs": attrs
                }
        return self.month_mileagedata


    async def get_year_mileage(self):

        _LOGGER.debug(self.device_imei)
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            _LOGGER.debug("Requests imei: %s", imei)
            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10):
                        infodata = await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                        ClientConnectorError
                ) as error:
                    raise

                _LOGGER.debug("result infodata: %s", infodata)

                if infodata:
                    self.deviceinfo[imei] = infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata["mcType"]
                    self.deviceinfo[imei]["expiration"] = infodata["expiration"]
            data = None
            try:
                async with timeout(10):
                    data = await self.hass.async_add_executor_job(self._get_device_year_mileage, imei)
                    _LOGGER.debug("途强在线 %s 最终数据结果: %s", imei, data)
            except ClientConnectorError as error:
                _LOGGER.error("途强在线 %s 连接错误: %s", imei, error)
            except asyncio.TimeoutError:
                _LOGGER.error("途强在线 %s 获取数据超时 (10秒)", imei)
            except Exception as e:
                await self.hass.async_add_executor_job(self._login, self.username, self.password)
                raise UpdateFailed(e)

            if data:
                imei = data["imei"]
                year_dis = data["dis"]
                attrs = {
                    "year_dis": year_dis,
                }
                self.year_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "year_dis": year_dis,
                    "attrs": attrs
                }
            else:
                year_dis = 0
                attrs = {
                    "year_dis": 0,
                }
                self.year_mileagedata[imei] = {
                    "location_key": self.location_key + imei,
                    "deviceinfo": self.deviceinfo.get(imei, {}),
                    "year_dis": year_dis,
                    "attrs": attrs
                }
        return self.year_mileagedata
class GetDataError(Exception):
    """request error or response data is unexpected"""


class DataButton:

    def __init__(self, hass, username, password, device_imei):
        self.hass = hass
        self._username = username
        self._password = password
        self.device_imei = device_imei        
        self.session_tuqiang123 = requests.session()
        self.userid = None
        self.usertype = None
        
        headers = {
            'User-Agent': TUQIANG_USER_AGENT
        }
        self.session_tuqiang123.headers.update(headers)  
    
    def _encode(self, code):
        en_code = ''
        for s in code:
            en_code = en_code + str(ord(s)) + '|'
        return en_code[:-1]

    def _login(self, username, password):
        p_data = {
            'ver': '1',
            'method': 'login',
            'account': username,
            'password': self._encode(password),
            'language': 'zh'
        }
        url = TUQIANG123_API_HOST + '/api/regdc'
        response = self.session_tuqiang123.post(url, data=p_data)
        _LOGGER.debug("TUQIANG123_API_HOST cookies: %s", self.session_tuqiang123.cookies)
        _LOGGER.debug(response.json())
        if response.json()['code'] == 0:
            self._get_userid()
            return True
        else:
            return False

    def _get_userid(self):
        url = TUQIANG123_API_HOST + '/customer/getProviderList'
        resp = self.session_tuqiang123.post(url, data=None).json()
        self.userid = resp['data']['user']['userId']
        self.usertype = resp['data']['user']['type']
        
    def _do_action(self, action):
        url = TUQIANG123_API_HOST + '/device/sendIns'
        p_data = {
            'imei': self.device_imei,
            'orderContent': 'GPSON#',
            'instructionId': 111845,
            'instructionName': action,
            'instructionPwd': '',
            'isUsePwd': 0,
            'isOffLine': 1
        }
        resp = self.session_tuqiang123.post(url, data=p_data)
        return resp.json()
        
    async def _action(self, action): 
        
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        resp = await self.hass.async_add_executor_job(self._do_action, action)
        _LOGGER.debug(resp)                        
        state = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  
        return state
        
        
class DataSwitch:

    def __init__(self, hass, username, password, device_imei):
        self.hass = hass
        self._username = username
        self._password = password
        self.device_imei = device_imei
        self.session_tuqiang123 = requests.session()
        self.userid = None
        self.usertype = None
        
        headers = {
            'User-Agent': TUQIANG_USER_AGENT
        }
        self.session_tuqiang123.headers.update(headers)  
    
    def _encode(self, code):
        en_code = ''
        for s in code:
            en_code = en_code + str(ord(s)) + '|'
        return en_code[:-1]

    def _login(self, username, password):
        p_data = {
            'ver': '1',
            'method': 'login',
            'account': username,
            'password': self._encode(password),
            'language': 'zh'
        }
        url = TUQIANG123_API_HOST + '/api/regdc'
        response = self.session_tuqiang123.post(url, data=p_data)
        _LOGGER.debug("TUQIANG123_API_HOST cookies: %s", self.session_tuqiang123.cookies)
        _LOGGER.debug(response.json())
        if response.json()['code'] == 0:
            self._get_userid()
            return True
        else:
            return False

    def _get_userid(self):
        url = TUQIANG123_API_HOST + '/customer/getProviderList'
        resp = self.session_tuqiang123.post(url, data=None).json()
        self.userid = resp['data']['user']['userId']
        self.usertype = resp['data']['user']['type']
        
    def _do_action(self, url, body):
        url = url
        p_data = body
        resp = self.session_tuqiang123.post(url, data=p_data)
        return resp.json()       
        
    async def _turn_on(self, action): 
        
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        if action == "defence":
            url = TUQIANG123_API_HOST + '/device/sendIns'
            json_body = {
                'imei': self.device_imei,
                'orderContent': '111#',
                'instructionId': 97,
                'instructionName': "设防",
                'instructionPwd': '',
                'isUsePwd': 0,
                'isOffLine': 1
            }
            resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
            _LOGGER.debug("Requests remaining: %s", url)
            _LOGGER.debug(resp)  
        elif action == "defencemode":
            url = TUQIANG123_API_HOST + '/device/sendIns'
            json_body = {
                'imei': self.device_imei,
                'orderContent': 'DEFMODE,{0}#',
                'instructionId': 98,
                'instructionName': "设防模式",
                'param': '22342,0',
                'instructionPwd': '',
                'isUsePwd': 0,
                'isOffLine': 1
            }
            resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
            _LOGGER.debug("Requests remaining: %s", url)
            _LOGGER.debug(resp)  

            
    async def _turn_off(self, action): 
        
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        if action == "defence":
            url = TUQIANG123_API_HOST + '/device/sendIns'
            json_body = {
                'imei': self.device_imei,
                'orderContent': '000#',
                'instructionId': 118,
                'instructionName': "撤防",
                'instructionPwd': '',
                'isUsePwd': 0,
                'isOffLine': 1
            }
            resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
            _LOGGER.debug("Requests remaining: %s", url)
            _LOGGER.debug(resp.text())  
            
        elif action == "defencemode":
            url = TUQIANG123_API_HOST + '/device/sendIns'
            json_body = {
                'imei': self.device_imei,
                'orderContent': 'DEFMODE,{0}#',
                'instructionId': 98,
                'instructionName': "设防模式",
                'param': '22342,1',
                'instructionPwd': '',
                'isUsePwd': 0,
                'isOffLine': 1
            }
            resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
            _LOGGER.debug("Requests remaining: %s", url)
            _LOGGER.debug(resp)
