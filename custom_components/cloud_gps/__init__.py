'''
Support for cloud_gps
Author        : dscao
Github        : https://github.com/dscao
Description   : 
Date          : 2023-11-16
LastEditors   : dscao
LastEditTime  : 2025-6-11
'''
"""    
Component to integrate with Cloud_GPS.

For more details about this component, please refer to
https://github.com/dscao/cloud_gps
"""
import logging
import asyncio
import json
import time, datetime
import requests
import re
import hashlib
import urllib.parse
import math
from importlib import import_module
from aiohttp.client_exceptions import ClientConnectorError
from async_timeout import timeout
from dateutil.relativedelta import relativedelta 
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA
from requests import ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError
import homeassistant.util.dt as dt_util
from homeassistant.components import zone
from homeassistant.components.device_tracker import PLATFORM_SCHEMA
from homeassistant.components.device_tracker.const import CONF_SCAN_INTERVAL
from homeassistant.components.device_tracker.legacy import DeviceScanner
from homeassistant.core import HomeAssistant, callback
from homeassistant.core_config import Config
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import slugify
from homeassistant.helpers.event import track_utc_time_change
from homeassistant.util import slugify
from homeassistant.util.location import distance
from homeassistant.util.json import load_json
from homeassistant.helpers.json import save_json
from .helper import gcj02towgs84, wgs84togcj02, gcj02_to_bd09, bd09_to_gcj02, bd09_to_wgs84, wgs84_to_bd09

from homeassistant.const import (
    Platform,
    CONF_USERNAME,
    CONF_PASSWORD,
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
    CONF_GPS_CONVER,
    CONF_DEVICE_IMEI,
    UNDO_UPDATE_LISTENER,
    CONF_ATTR_SHOW,
    CONF_UPDATE_ADDRESSDISTANCE,
    CONF_ADDRESSAPI,
    CONF_ADDRESSAPI_KEY,
    CONF_PRIVATE_KEY,
    CONF_UPDATE_INTERVAL,
)

TYPE_GEOFENCE = "Geofence"
__version__ = '2025.6.11'

_LOGGER = logging.getLogger(__name__)
    
PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]
   
WAY_BAIDU = ["/directionlite/v1/driving","/directionlite/v1/riding","/directionlite/v1/walking","/directionlite/v1/transit"]
WAY_GAODE = ["/v3/direction/driving","/v4/direction/bicycling","/v3/direction/walking","/v3/direction/transit/integrated"]
WAY_QQ = ["/ws/direction/v1/driving/","/ws/direction/v1/bicycling/","/ws/direction/v1/walking/","/ws/direction/v1/transit/","/ws/direction/v1/ebicycling/"]
TACTICS_BAIDU = [0,1,2,3,4,5]
TACTICS_GAODE = [0,13,4,2,1,5]
TACTICS_QQ = ["LEAST_TIME","AVOID_HIGHWAY","REAL_TRAFFIC","LEAST_TIME","LEAST_FEE","HIGHROAD_FIRST"]

# 平台与模块映射关系
PLATFORM_MODULE_MAP = {
    "gooddriver.cn": "gooddriver_data_fetcher",
    "tuqiang123.com": "tuqiang123_data_fetcher",
    "tuqiang.net": "tuqiangnet_data_fetcher",
    "cmobd.com": "cmobd_data_fetcher",
    "niu.com": "niu_data_fetcher",
    "hellobike.com": "hellobike_data_fetcher",
    "auto.amap.com": "autoamap_data_fetcher",
    "macless_haystack": "macless_haystack_data_fetcher",
}

   
async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Set up configured cloud_gps."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up cloud_gps as config entry."""        
    titlename = entry.title
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    webhost = entry.data[CONF_WEB_HOST]
    gps_conver = entry.options.get(CONF_GPS_CONVER, ["wgs84"])
    device_imei = entry.options.get(CONF_DEVICE_IMEI, [])
    update_interval_seconds = entry.options.get(CONF_UPDATE_INTERVAL, 60)
    attr_show = entry.options.get(CONF_ATTR_SHOW, True)
    address_distance = entry.options.get(CONF_UPDATE_ADDRESSDISTANCE, 50)
    addressapi = entry.options.get(CONF_ADDRESSAPI, "none")
    api_key = entry.options.get(CONF_ADDRESSAPI_KEY, "")
    private_key = entry.options.get(CONF_PRIVATE_KEY, "")
    location_key = entry.unique_id
    
    # 异步导入模块
    try:
        module = await async_import_data_fetcher(hass, webhost)
    except (ValueError, ImportError) as e:
        raise ConfigEntryNotReady(str(e))
    data_fetcher_class = module.DataFetcher
    _LOGGER.debug("%s 集成条目中已启用设备 %s", titlename, device_imei)
    if not device_imei:
        _LOGGER.error("%s 配置中未启用任何设备，请进入配置中设置启用的设备唯一编号。", titlename)
    coordinator = CloudDataUpdateCoordinator(
        hass, data_fetcher_class, username, password, webhost, gps_conver, device_imei, location_key, update_interval_seconds, address_distance, addressapi, api_key, private_key
    )
    
    await coordinator.async_refresh()
        
    for imei in device_imei:

        if not coordinator.data.get(imei):
            _LOGGER.warning("%s Initial data fetch failed, entities will be created when data becomes available", imei)
            
            async def check_data_and_create_entities(_now):
                _LOGGER.debug("%s entities try to creat again", imei)
                await coordinator.async_refresh()
                if coordinator.data.get(imei) and not coordinator._entity_created:
                    await coordinator.ensure_entities_created()

            # 立即启动定时器，并获取用于取消它的函数
            cancel_timer = async_track_time_interval(
                hass,
                check_data_and_create_entities,
                datetime.timedelta(seconds=30),
            )

            # 将取消函数注册到卸载事件中，以确保在卸载集成时停止定时器
            entry.async_on_unload(cancel_timer)
            
            break

    undo_listener = entry.add_update_listener(update_listener)

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        UNDO_UPDATE_LISTENER: undo_listener,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    hass.data[DOMAIN][entry.entry_id][UNDO_UPDATE_LISTENER]()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def update_listener(hass, entry):
    """Update listener with entity creation check"""
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    
    # 如果数据已存在但实体未创建，立即创建
    if coordinator.data and not coordinator._entity_created:
        await coordinator.ensure_entities_created()
    else:
        await hass.config_entries.async_reload(entry.entry_id)

async def async_import_data_fetcher(hass, webhost):
    """异步导入数据获取模块"""
    module_name = PLATFORM_MODULE_MAP.get(webhost)
    if not module_name:
        raise ValueError(f"Unsupported platform: {webhost}")

    try:
        return await hass.async_add_executor_job(
            lambda: import_module(f".{module_name}", __package__)
        )
    except ImportError as e:
        _LOGGER.error("模块导入失败: %s", e)
        raise

        
class CloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching cloud data API."""

    def __init__(self, hass, data_fetcher_class, username, password, webhost, gps_conver, device_imei, location_key, update_interval_seconds, address_distance, addressapi, api_key, private_key):
        """Initialize."""
        self._hass = hass
        update_interval = (
            datetime.timedelta(seconds=int(update_interval_seconds))
        )
        _LOGGER.debug("Data %s , %s will be update every %s", webhost, device_imei, update_interval)

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        
        self._gps_conver = gps_conver
        self.device_imei = device_imei
        self._location_key = location_key

        self._address_distance = address_distance
        self._addressapi = addressapi
        self._api_key = api_key
        self._private_key = private_key
        self.data = {}
        self._coords = {}
        self._coords_old = {}
        self._address = {}
        self._fetcher = data_fetcher_class(hass, username, password, device_imei, location_key)
        
        self._entity_created = False
        self._retry_count = 0


    async def _async_update_data(self):
        """Update data via library."""  
        try:
            async with timeout(20):
                data = await self._fetcher.get_data()
                _LOGGER.debug("%s update_data: %s", self.device_imei, data)                
                _LOGGER.debug("%s gps_conver: %s", self.device_imei, self._gps_conver)
                    
                if data:
                    for imei in self.device_imei:
                        if data.get(imei):
                            if self._gps_conver == "gcj02":
                                data[imei]["thislon"], data[imei]["thislat"] = gcj02towgs84(data[imei]["thislon"], data[imei]["thislat"])
                            if self._gps_conver == "bd09":
                                data[imei]["thislon"], data[imei]["thislat"] = bd09_to_wgs84(data[imei]["thislon"], data[imei]["thislat"])
                            
                            self._coords[imei] = [data[imei]["thislon"], data[imei]["thislat"]]
                            _LOGGER.debug("self._coords[%s]: %s", imei, self._coords[imei])
                            
                            if not self._coords_old.get(imei):
                                self._coords_old[imei] = [0, 0]
                                
                            if self._addressapi != "none" and self._addressapi != None:
                                distance = self.get_distance(self._coords[imei][1], self._coords[imei][0], self._coords_old.get(imei)[1], self._coords_old.get(imei)[0])
                                if distance > self._address_distance:
                                    self._address[imei] = await self._get_address_frome_api(imei, self._addressapi, self._api_key, self._private_key)
                                    _LOGGER.debug("api_get_address: %s", self._address.get(imei))
                                data[imei]["attrs"]["address"] = self._address.get(imei)
                    # 保存新数据
                    self.data = data
        
                elif not data:
                    _LOGGER.error("%s No data available from API", self.device_imei)
                    
        except (asyncio.TimeoutError, ClientConnectorError) as err:
            self._retry_count += 1
            _LOGGER.warning(
                "[%s]Error communicating with API (retry #%s): %s",
                self.device_imei,
                self._retry_count,
                err,
            )
            
        except Exception as error:
            self._retry_count += 1
            _LOGGER.error(
                "[%s]Unexpected error updating data (retry #%s): %s",
                self.device_imei,
                self._retry_count,
                error,
                exc_info=True,
            )
            
        return self.data or {}

    async def ensure_entities_created(self):
        """Ensure entities are created once data is available"""
        if not self._entity_created and self.data:
            self._entity_created = True
            _LOGGER.info("Data now available, triggering entity creation")
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        
    async def _get_address_frome_api(self, imei, addressapi, api_key, private_key):
        try:
            async with timeout(5):
                if addressapi == "baidu" and api_key:
                    _LOGGER.debug("baidu:"+api_key)
                    addressdata = await self._hass.async_add_executor_job(self.get_baidu_geocoding, self._coords[imei][1], self._coords[imei][0], api_key, private_key)
                    if addressdata['status'] == 0:
                        self._coords_old[imei] = self._coords[imei]
                        return addressdata['result']['formatted_address'] + addressdata['result']['sematic_description']
                    else:
                        return addressdata['message']                
                elif addressapi == "gaode" and api_key:
                    _LOGGER.debug("gaode:"+api_key)
                    gcjdata = wgs84togcj02(self._coords[imei][0], self._coords[imei][1])
                    addressdata = await self._hass.async_add_executor_job(self.get_gaode_geocoding, gcjdata[1], gcjdata[0], api_key, private_key)
                    if addressdata['status'] == "1":
                        self._coords_old[imei] = self._coords[imei]
                        return addressdata['regeocode']['formatted_address']
                    else: 
                        return addressdata['info']
                    
                elif addressapi == "tencent" and api_key:
                    _LOGGER.debug("tencent:"+api_key)
                    gcjdata = wgs84togcj02(self._coords[imei][0], self._coords[imei][1])
                    addressdata = await self._hass.async_add_executor_job(self.get_tencent_geocoding, gcjdata[1], gcjdata[0], api_key, private_key)
                    if addressdata['status'] == 0:
                        self._coords_old[imei] = self._coords[imei]
                        return addressdata['result']['formatted_addresses']['recommend']
                    else: 
                        return addressdata['message']                
                elif addressapi == "free":
                    _LOGGER.debug("free")
                    gcjdata = wgs84togcj02(self._coords[imei][0], self._coords[imei][1])
                    bddata = gcj02_to_bd09(gcjdata[0], gcjdata[1])
                    addressdata = await self._hass.async_add_executor_job(self.get_free_geocoding, bddata[1], bddata[0])
                    if addressdata['status'] == 'OK':
                        self._coords_old[imei] = self._coords[imei]
                        return addressdata['result']['formatted_address']
                    else:
                        return 'free接口返回错误'                
                else:
                    return ""            
        except ClientConnectorError as error:
            return("连接错误: %s", error)
        except asyncio.TimeoutError:
            return("获取数据超时 (5秒)")
        except Exception as e:
            return("未知错误: %s", repr(e))

            
    def get_data(self, url):
        json_text = requests.get(url).content
        json_text = json_text.decode('utf-8')
        json_text = re.sub(r'\\','',json_text)
        json_text = re.sub(r'"{','{',json_text)
        json_text = re.sub(r'}"','}',json_text)
        resdata = json.loads(json_text)
        return resdata
            
    def get_free_geocoding(self, lat, lng):
        api_url = 'https://api.map.baidu.com/geocoder'
        location = str("{:.6f}".format(lat))+','+str("{:.6f}".format(lng))
        url = api_url+'?&output=json&location='+location
        _LOGGER.debug(url)
        response = self.get_data(url)
        _LOGGER.debug(response)
        return response
    
    def get_tencent_geocoding(self, lat, lng, api_key, private_key):
        api_url = 'https://apis.map.qq.com/ws/geocoder/v1/'
        location = str("{:.6f}".format(lat))+','+str("{:.6f}".format(lng))
        sig = ''
        if private_key:
            params = '/ws/geocoder/v1/?get_poi=1&key='+api_key+'&location='+location+'&output=json'
            sig = self.tencent_sk(params, private_key)
        url = api_url+'?key='+api_key+'&output=json&get_poi=1&location='+location+'&sig='+sig
        _LOGGER.debug(url)
        response = self.get_data(url)
        _LOGGER.debug(response)
        return response
        
    def get_baidu_geocoding(self, lat, lng, api_key, private_key):
        api_url = 'https://api.map.baidu.com/reverse_geocoding/v3/'
        location = str("{:.6f}".format(lat))+','+str("{:.6f}".format(lng))
        sn = ''
        if private_key:
            params = '/reverse_geocoding/v3/?ak='+api_key+'&output=json&coordtype=wgs84ll&extensions_poi=1&location='+location
            sn = self.baidu_sn(params, private_key)
        url = api_url+'?ak='+api_key+'&output=json&coordtype=wgs84ll&extensions_poi=1&location='+location+'&sn='+sn
        _LOGGER.debug(url)
        response = self.get_data(url)
        _LOGGER.debug(response)
        return response
        
    def get_gaode_geocoding(self, lat, lng, api_key, private_key):
        api_url = 'https://restapi.amap.com/v3/geocode/regeo'
        location = str("{:.6f}".format(lng))+','+str("{:.6f}".format(lat))        
        sig = ''
        if private_key:
            params = {'key': api_key, 'output': 'json', 'extensions': 'base', 'location': location}
            sig = self.generate_signature(params, private_key)
        url = api_url+'?key='+api_key+'&output=json&extensions=base&location='+location+'&sig='+sig
        _LOGGER.debug(url)
        response = self.get_data(url)
        _LOGGER.debug(response)
        return response

    def generate_signature(self, params, private_key):
        sorted_params = sorted(params.items(), key=lambda x: x[0])  # 按参数名的升序排序
        param_str = '&'.join([f'{key}={value}' for key, value in sorted_params])  # 构建参数字符串
        param_str += private_key  # 加私钥
        signature = hashlib.md5(param_str.encode()).hexdigest()  # 计算MD5摘要
        return signature  #根据私钥计算出web服务数字签名
        
    def baidu_sn(self, params, private_key):
        param_str = urllib.parse.quote(params, safe="/:=&?#+!$,;'@()*[]")
        param_str += private_key
        signature = hashlib.md5(urllib.parse.quote_plus(param_str).encode()).hexdigest()
        return signature
        
    def tencent_sk(self, params, private_key):
        param_str = params + private_key
        signature = hashlib.md5(param_str.encode()).hexdigest()
        return signature
        
    def get_distance(self, lat1, lng1, lat2, lng2):
        earth_radius = 6378.137
        rad_lat1 = lat1 * math.pi / 180.0
        rad_lat2 = lat2 * math.pi / 180.0
        a = rad_lat1 - rad_lat2
        b = lng1 * math.pi / 180.0 - lng2 * math.pi / 180.0
        s = 2 * math.asin(math.sqrt(math.pow(math.sin(a / 2), 2) + math.cos(rad_lat1) * math.cos(rad_lat2) * math.pow(math.sin(b / 2), 2)))
        s = s * earth_radius
        return s * 1000