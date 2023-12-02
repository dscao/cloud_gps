"""Adds config flow for cloud."""
import logging
import asyncio
import json
import time, datetime
import requests
import re
import hashlib
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME, CONF_USERNAME, CONF_PASSWORD, CONF_CLIENT_ID
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from collections import OrderedDict
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_GPS_CONVER,
    CONF_UPDATE_INTERVAL,
    CONF_ATTR_SHOW,
    DOMAIN,
    CONF_WEB_HOST,
    CONF_DEVICES,
    CONF_DEVICE_IMEI,
    CONF_SENSORS,
    CONF_SWITCHS,
    CONF_BUTTONS,
    KEY_QUERYTIME,
    KEY_PARKING_TIME,
    KEY_LASTSTOPTIME,
    KEY_ADDRESS,
    KEY_SPEED,
    CONF_ADDRESSAPI,
    CONF_ADDRESSAPI_KEY,
    CONF_PRIVATE_KEY,
    CONF_WITH_BAIDUMAP_CARD,
)

import voluptuous as vol

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
USER_AGENT_CMOBD = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
USER_AGENT_NIU = 'manager/4.6.48 (android; IN2020 11);lang=zh-CN;clientIdentifier=Domestic;timezone=Asia/Shanghai;model=IN2020;deviceName=IN2020;ostype=android'
USER_AGENT_GOODDRIVER = 'gooddriver/7.9.1 CFNetwork/1410.0.3 Darwin/22.6.0'

WEBHOST = {    
    "tuqiang123.com": "途强在线",
    "tuqiang.net": "途强物联",
    "gooddriver.cn": "优驾盒子联网版",    
    "cmobd.com": "中移行车卫士（*用户密码填写token，暂未支持）",
    "niu.com": "小牛电动车（暂未调试，不支持）",
    "hellobike.com": "哈啰智能芯（*用户密码填写token，暂未支持）"
}

API_HOST_TUQIANG123 = "http://www.tuqiang123.com"   # http://www.tuqiangol.com 或者 http://www.tuqiang123.com
API_HOST_TUQIANGNET = "https://www.tuqiang.net"
API_HOST_TOKEN_GOODDRIVER = "https://ssl.gooddriver.cn"  # "https://ssl.gooddriver.cn" 或者 "http://121.41.101.95:8080"
API_URL_GOODDRIVER = "http://restcore.gooddriver.cn/API/Values/HudDeviceDetail/"
API_HOST_TOKEN_NIU = "https://account.niu.com"
API_URL_NIU = "https://app-api.niu.com"
API_URL_CMOBD = "https://lsapp.cmobd.com/v360/iovsaas"
API_URL_HELLOBIKE = "https://a.hellobike.com/evehicle/api"

_LOGGER = logging.getLogger(__name__)

@config_entries.HANDLERS.register(DOMAIN)
class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlow(config_entry)

    def __init__(self):
        """Initialize."""
        self._errors = {}
        self.session = requests.session()
        self.userid = None
        self.usertype = None
        self.cloudpgs_token = None
            
    def __encode(self, code):
        en_code = ''
        for s in code:
            en_code = en_code + str(ord(s)) + '|'
        return en_code[:-1]
        
    def md5_hash(self, text):
        md5 = hashlib.md5()        
        md5.update(text.encode('utf-8'))        
        encrypted_text = md5.hexdigest()        
        return encrypted_text

    def _login_tuqiang123(self, username, password):
        p_data = {
            'ver': '1',
            'method': 'login',
            'account': username,
            'password': self.__encode(password),
            'language': 'zh'
        }
        url = API_HOST_TUQIANG123 + '/api/regdc'
        response = self.session.post(url, data=p_data)
        _LOGGER.debug("headers: %s", self.session.headers)
        _LOGGER.debug("cookies: %s", self.session.cookies)
        _LOGGER.info(response.json())
        if response.json()['code'] == 0:
            url = API_HOST_TUQIANG123 + '/customer/getProviderList'
            resp = self.session.post(url, data=None).json()
            _LOGGER.debug(resp)
            self.userid = resp['data']['user']['userId']
            self.usertype = resp['data']['user']['type']
        return response.json()
        
        
    def _devicelist_tuqiang123(self):
        url = API_HOST_TUQIANG123 + '/device/list'
        p_data = {
            'dateType': 'activation',
            'equipment.userId': self.userid
        }
        resp = self.session.post(url, data=p_data).json()
        return resp
        
    def _login_tuqiangnet(self, username, password):
        p_data = {
            'timeZone': '28800',
            'token': '',
            'userName': username,
            'password': password,
            'lang': 'zh'
        }
        url = API_HOST_TUQIANGNET + '/loginVerification'
        response = self.session.post(url, data=p_data)
        _LOGGER.debug("headers: %s", self.session.headers)
        _LOGGER.debug("cookies: %s", self.session.cookies)
        _LOGGER.debug(response)
        if response.json()['code'] == 0:
            _LOGGER.info(response.json())
            self.cloudpgs_token = response.json()["data"]["token"]
        return response.json()
            
    def _devicelist_tuqiangnet(self):
        url = API_HOST_TUQIANGNET + '/device/getDeviceList'
        p_data = {
            'token': self.cloudpgs_token,
            'userId': self.userid
        }
        resp = self.session.post(url, data=p_data).json()        
        return resp
        
    def _login_gooddriver(self, username, password):
        p_data = {
            'U_ACCOUNT': username,
            'U_PASSWORD': self.md5_hash(password)
        }
        url = API_HOST_TOKEN_GOODDRIVER + '/UserServices/Login2018'
        response = self.session.post(url, data=json.dumps(p_data))
        return response.json()

    def _devicelist_cmobd(self, token):
        url = API_URL_CMOBD
        p_data = {
            "cmd":"userVehicles",
            "ver":1,
            "token": token,
            "pageNo":0,
            "pageSize":10
        }
        resp = self.session.post(url, data=p_data).json()        
        return resp
        
    def _get_cmobd_tracker(self, token, vehicleid):
        url = API_URL_CMOBD
        p_data = {
           "cmd": "weappVehicleRunStatus", 
           "ver": 1, 
           "token": token, 
           "vehicleId": vehicleid, 
           "isNeedGps": "1", 
           "gpsStartTime": ""
        }
        resp = self.session.post(url, data=p_data).json()
        return resp
        
    def _get_niu_token(self, username, password):
        url = API_HOST_TOKEN_NIU + '/v3/api/oauth2/token'
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
        _LOGGER.debug("get niu token data: %s", data)
        return data

    def _get_niu_vehicles_info(self, token):

        url = API_URL_NIU + '/v5/scooter/list'
        headers = {"token": token}
        try:
            r = requests.get(url, headers=headers, data=[])
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        return data
        
    def _devicelist_hellobike(self, token):
        url = API_URL_HELLOBIKE + "?"
        p_data = {
            "token" : token,
            "action" : ""
        }
        resp = self.session.post(url, data=p_data).json()        
        return resp
        
    def _get_hellobike_tracker(self, token, bikeNo):
        url = API_URL_HELLOBIKE + "?rent.order.getRentBikeStatus"
        p_data = {
            "bikeNo" : bikeNo,
            "token" : token,
            "action" : "rent.order.getRentBikeStatus"
        }
        resp = self.session.post(url, data=p_data).json()
        return resp
        
    async def async_step_user(self, user_input={}):
        self._errors = {}
        if user_input is not None:
            # Check if entered host is already in HomeAssistant
            existing = await self._check_existing(user_input[CONF_NAME])
            if existing:
                return self.async_abort(reason="already_configured")

            # If it is not, continue with communication test
            config_data = {}           
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            webhost = user_input[CONF_WEB_HOST]
            
            devices = []

            if webhost=="tuqiang.net":
                headers = {
                    'User-Agent': USER_AGENT
                }
                self.session.headers = headers

                status = await self.hass.async_add_executor_job(self._login_tuqiangnet, username, password)                    
                if status.get("code") == 0:
                    deviceslist_data = await self.hass.async_add_executor_job(self._devicelist_tuqiangnet)
                    _LOGGER.debug(deviceslist_data)
                    if deviceslist_data.get("code") == 0:
                        for deviceslist in deviceslist_data["data"]:
                            devices.append(str(deviceslist["imei"]))
                
                    await self.async_set_unique_id(f"cloudpgs-{user_input[CONF_USERNAME]}-{user_input[CONF_WEB_HOST]}".replace(".","_"))
                    self._abort_if_unique_id_configured()
                    
                    config_data[CONF_USERNAME] = username
                    config_data[CONF_PASSWORD] = password
                    config_data[CONF_DEVICES] = devices
                    config_data[CONF_WEB_HOST] = webhost
                    
                    _LOGGER.debug(devices)
                    
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=config_data
                    )
                else:
                    self._errors["base"] = status.get("msg")
            elif webhost=="tuqiang123.com":
                headers = {
                    'User-Agent': USER_AGENT
                }
                self.session.headers = headers

                status = await self.hass.async_add_executor_job(self._login_tuqiang123, username, password)
                if status.get("code") == 0:
                    deviceslist_data = await self.hass.async_add_executor_job(self._devicelist_tuqiang123)
                    _LOGGER.debug(deviceslist_data)
                    if deviceslist_data.get("code") == 0:
                        for deviceslist in deviceslist_data["data"]["result"]:
                            devices.append(str(deviceslist["equipmentDetail"]["imei"]))
                
                    await self.async_set_unique_id(f"cloudpgs-{user_input[CONF_USERNAME]}-{user_input[CONF_WEB_HOST]}".replace(".","_"))
                    self._abort_if_unique_id_configured()
                    
                    config_data[CONF_USERNAME] = username
                    config_data[CONF_PASSWORD] = password
                    config_data[CONF_DEVICES] = devices
                    config_data[CONF_WEB_HOST] = webhost
                    
                    _LOGGER.debug(devices)
                    
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=config_data
                    )
                else:
                    self._errors["base"] = status.get("msg")

            elif webhost=="gooddriver.cn":
                headers = {
                    'User-Agent': USER_AGENT_GOODDRIVER,
                    'SDF': '6928FAA6-B970-F5A5-85F0-73D4299D99A8',
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
                self.session.headers = headers

                self.session.verify = True
                status = await self.hass.async_add_executor_job(self._login_gooddriver, username, password)
                _LOGGER.debug(status)
                if status.get("ERROR_CODE") == 0:
                    deviceslist_data = status["MESSAGE"]["USER_VEHICLEs"]
                    _LOGGER.debug(deviceslist_data)
                    for deviceslist in deviceslist_data:
                        url = API_URL_GOODDRIVER + str(deviceslist["UV_ID"])        
                        resp = await self.hass.async_add_executor_job(self.session.get, url)
                        if resp.json()['ERROR_CODE'] == 0:
                            devices.append(str(deviceslist["UV_ID"]))
                
                    await self.async_set_unique_id(f"cloudpgs-{user_input[CONF_USERNAME]}-{user_input[CONF_WEB_HOST]}".replace(".","_"))
                    self._abort_if_unique_id_configured()
                    
                    config_data[CONF_USERNAME] = username
                    config_data[CONF_PASSWORD] = password
                    config_data[CONF_DEVICES] = devices
                    config_data[CONF_WEB_HOST] = webhost
                    
                    _LOGGER.debug(devices)
                    
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=config_data
                    )
                else:
                    self._errors["base"] = status.get("ERROR_MESSAGE")
            elif webhost=="cmobd.com":
                headers = {
                    'Host': 'lsapp.cmobd.com',                    
                    'agent': 'Lushang/5.0.0',
                    'Cookie': 'node-ls-api=' + password,
                    'content-type': 'application/json',                    
                    'User-Agent': USER_AGENT_CMOBD,
                    'Referer': 'https://servicewechat.com/wx351871af12293380/31/page-frame.html'
                }
                
                self.session.headers = headers

                self.session.verify = True
                status = await self.hass.async_add_executor_job(self._devicelist_cmobd, password)
                _LOGGER.debug(status)
                if status.get("result") != 0:
                    self._errors["base"] = status.get("note")
                    return await self._show_config_form(user_input)   
                if status:
                    deviceslist_data = status.get("dataList")
                    _LOGGER.debug(deviceslist_data)
                    for deviceslist in deviceslist_data:
                        resp = await self.hass.async_add_executor_job(self._get_cmobd_tracker, password, str(deviceslist["vehicleID"]))
                        if resp['result'] == 0:
                            devices.append(str(deviceslist["vehicleID"]))
                
                    await self.async_set_unique_id(f"cloudpgs-{user_input[CONF_USERNAME]}-{user_input[CONF_WEB_HOST]}".replace(".","_"))
                    self._abort_if_unique_id_configured()
                    
                    config_data[CONF_USERNAME] = username
                    config_data[CONF_PASSWORD] = password
                    config_data[CONF_DEVICES] = devices
                    config_data[CONF_WEB_HOST] = webhost
                    
                    _LOGGER.debug(devices)
                    
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=config_data
                    )
                else:
                    self._errors["base"] = "communication"            
            else:
                self._errors["base"] = "未选择有效平台"

            return await self._show_config_form(user_input)

        return await self._show_config_form(user_input)

    async def _show_config_form(self, user_input):

        # Defaults
        device_name = "平台名称GPS"
        data_schema = OrderedDict()
        data_schema[vol.Required(CONF_NAME, default=device_name)] = str
        data_schema[vol.Required(CONF_USERNAME ,default ="")] = str
        data_schema[vol.Required(CONF_PASSWORD ,default ="")] = str
        data_schema[vol.Required(CONF_WEB_HOST, default="")] = vol.All(str, vol.In(WEBHOST))

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=self._errors
        )

    async def async_step_import(self, user_input):
        """Import a config entry.

        Special type of import, we're not actually going to store any data.
        Instead, we're going to rely on the values that are in config file.
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(title="configuration.yaml", data={})

    async def _check_existing(self, host):
        for entry in self._async_current_entries():
            if host == entry.data.get(CONF_NAME):
                return True

class OptionsFlow(config_entries.OptionsFlow):
    """Config flow options for cloud."""

    def __init__(self, config_entry):
        """Initialize cloud options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
            
        listoptions = []  
        for deviceconfig in self.config_entry.data.get(CONF_DEVICES,[]):
            listoptions.append({"value": deviceconfig, "label": deviceconfig})

        SENSORSLIST = [
            {"value": KEY_PARKING_TIME, "label": "parkingtime"},
            {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
            {"value": KEY_ADDRESS, "label": "address"},
            {"value": KEY_SPEED, "label": "speed"}
        ]
        if self.config_entry.data.get(CONF_WEB_HOST) == "hellobike.com" or self.config_entry.data.get(CONF_WEB_HOST) == "niu.com":
            SWITCHSLIST = [
                {"value": "defence", "label": "defence"},
                {"value": "open_lock", "label": "open_lock"},
            ]
            
            BUTTONSLIST = [
                {"value": "bell", "label": "bell"}
            ]
        else:
            SWITCHSLIST = []
            BUTTONSLIST = []
                
        return self.async_show_form(
            step_id="user",            
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_IMEI, 
                        default=self.config_entry.options.get(CONF_DEVICE_IMEI,[])): SelectSelector(
                        SelectSelectorConfig(
                            options=listoptions,
                            multiple=True,translation_key=CONF_DEVICE_IMEI
                            )
                    ),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=self.config_entry.options.get(CONF_UPDATE_INTERVAL, 60),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)), 
                    vol.Optional(
                        CONF_GPS_CONVER,
                        default=self.config_entry.options.get(CONF_GPS_CONVER,"wgs84")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": "wgs84", "label": "wgs84"},
                                {"value": "gcj02", "label": "gcj02"},
                                {"value": "bd09", "label": "bd09"}
                            ],
                            multiple=False,translation_key=CONF_GPS_CONVER
                        )
                    ),
                    vol.Optional(
                        CONF_ATTR_SHOW,
                        default=self.config_entry.options.get(CONF_ATTR_SHOW, True),
                    ): bool,
                    vol.Optional(
                        CONF_WITH_BAIDUMAP_CARD,
                        default=self.config_entry.options.get(CONF_WITH_BAIDUMAP_CARD, False),
                    ): bool,
                    vol.Optional(
                        CONF_SENSORS, 
                        default=self.config_entry.options.get(CONF_SENSORS,[])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=SENSORSLIST,
                            multiple=True,translation_key=CONF_SENSORS
                        )
                    ),
                    vol.Optional(
                        CONF_SWITCHS, 
                        default=self.config_entry.options.get(CONF_SWITCHS,[])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=SWITCHSLIST,
                            multiple=True,translation_key=CONF_SWITCHS
                        )
                    ),
                    vol.Optional(
                        CONF_BUTTONS, 
                        default=self.config_entry.options.get(CONF_BUTTONS,[])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=BUTTONSLIST,
                            multiple=True,translation_key=CONF_BUTTONS
                        )
                    ),
                    vol.Optional(
                        CONF_ADDRESSAPI, 
                        default=self.config_entry.options.get(CONF_ADDRESSAPI,"none")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": "none", "label": "none"},
                                {"value": "free", "label": "free"},
                                {"value": "gaode", "label": "gaode"},
                                {"value": "baidu", "label": "baidu"},
                                {"value": "tencent", "label": "tencent"}
                            ], 
                            multiple=False,translation_key=CONF_ADDRESSAPI
                        )
                    ),                    
                    vol.Optional(
                        CONF_ADDRESSAPI_KEY, 
                        default=self.config_entry.options.get(CONF_ADDRESSAPI_KEY,"")
                    ): str, 
                    vol.Optional(
                        CONF_PRIVATE_KEY, 
                        default=self.config_entry.options.get(CONF_PRIVATE_KEY,"")
                    ): str,
                }
            ),
        )

