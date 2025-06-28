"""Adds config flow for cloud."""
import logging
import asyncio
import json
import time, datetime
import requests
import re
import hashlib
import base64
import paho.mqtt.client as mqtt
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
    PWD_NOT_CHANGED,
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
    KEY_LASTRUNTIME,
    KEY_LASTSEEN,
    KEY_ADDRESS,
    KEY_SPEED,
    KEY_TOTALKM,
    KEY_STATUS,
    KEY_ACC,
    KEY_BATTERY,
    KEY_BATTERY_STATUS,
    KEY_RUNORSTOP,
    KEY_SHAKE,
    CONF_UPDATE_ADDRESSDISTANCE,
    CONF_ADDRESSAPI,
    CONF_ADDRESSAPI_KEY,
    CONF_PRIVATE_KEY,
    CONF_WITH_MAP_CARD,
)

import voluptuous as vol

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36'
USER_AGENT_CMOBD = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN'
USER_AGENT_NIU = 'manager/4.6.48 (android; IN2020 11);lang=zh-CN;clientIdentifier=Domestic;timezone=Asia/Shanghai;model=IN2020;deviceName=IN2020;ostype=android'
USER_AGENT_GOODDRIVER = 'gooddriver/7.9.1 CFNetwork/1410.0.3 Darwin/22.6.0'


WEBHOST = {    
    "tuqiang123.com": "途强在线",
    "tuqiang.net": "途强物联",
    "gooddriver.cn": "优驾盒子联网版", 
    "niu.com": "小牛电动车（暂未调试）",    
    "cmobd.com": "中移行车卫士（*密码填写token）",    
    "hellobike.com": "哈啰智能芯（*密码填写token）",
    "auto.amap.com": "高德车机版（*密码填写 Key||sessionid||paramdata）",
    "macless_haystack": "macless_haystack（*用户名填写 服务器Url，密码填写Key Json）",
    "gps_mqtt": "gps_mqtt（*用户名填写 mqtt服务器 server||user||password，密码填写mqtt主题）"
}

API_HOST_TUQIANG123 = "https://www.tuqiang123.com"   # https://www.tuqiangol.com 或者 https://www.tuqiang123.com
API_HOST_TUQIANGNET = "https://www.tuqiang.net"
API_HOST_TOKEN_GOODDRIVER = "https://ssl.gooddriver.cn"  # "https://ssl.gooddriver.cn" 或者 "http://121.41.101.95:8080"
API_URL_GOODDRIVER = "http://restcore.gooddriver.cn/API/Values/HudDeviceDetail/"
API_HOST_TOKEN_NIU = "https://account.niu.com"
API_URL_NIU = "https://app-api.niu.com"
API_URL_CMOBD = "https://lsapp.cmobd.com/v360/iovsaas"
API_URL_HELLOBIKE = "https://a.hellobike.com/evehicle/api"
API_URL_AUTOAMAP = "http://ts.amap.com/ws/tservice/internal/link/mobile/get?ent=2&in="

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
        verurl = API_HOST_TUQIANG123 + '/api/regdc?ver=1&method=getAuthWay&account=' + username
        resver = self.session.get(verurl)
        _LOGGER.debug(resver.json())
        if not resver.json().get("data") == "":
            msg = "账号开户了" + resver.json().get("data") + "登录二次认证，请关闭二次验证后再尝试！"
            return {"msg":msg}
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
        url = API_URL_HELLOBIKE + "?rent.user.getUseBikePagePrimeInfoV3"
        p_data = {
            "token" : token,
            "action" : "rent.user.getUseBikePagePrimeInfoV3"
        }
        resp = self.session.post(url, data=json.dumps(p_data)).json()
        return resp
        
    def _get_hellobike_tracker(self, token, bikeNo):
        url = API_URL_HELLOBIKE + "?rent.order.getRentBikeStatus"
        p_data = {
            "bikeNo" : bikeNo,
            "token" : token,
            "action" : "rent.order.getRentBikeStatus"
        }
        resp = self.session.post(url, data=json.dumps(p_data)).json()
        return resp
        
    def _devicelist_autoamap(self, token):
        url = str.format(API_URL_AUTOAMAP + token.split("||")[0])
        p_data = token.split("||")[2]
        resp = self.session.post(url, data=p_data).json()
        return resp
        
    def _test_macless_haystack(self, url, username, password):
        auth_header = self.basic_auth(username, password)
        headers = {"authorization": auth_header}
        _LOGGER.debug("url: %s, headers:%s", url, headers)
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()

        except requests.exceptions.ConnectionError as e:
            _LOGGER.error(f"连接错误：{e}")
            return {"success": False, "error": f"连接错误：{e}"}  # 返回字典
        except requests.exceptions.Timeout as e:
            _LOGGER.error(f"请求超时：{e}")
            return {"success": False, "error": f"请求超时：{e}"}
        except requests.exceptions.HTTPError as e:
            _LOGGER.error(f"HTTP 错误：状态码 {e.response.status_code}, 原因：{e}")
            return {"success": False, "error": f"HTTP 错误：状态码 {e.response.status_code}, 原因：{e}"}
        except requests.exceptions.RequestException as e:  # 处理其他所有请求异常
            _LOGGER.error(f"请求失败：{e}")
            return {"success": False, "error": f"请求失败：{e}"}

        _LOGGER.debug(resp)
        return {"success": True, "response": resp}
        
    def basic_auth(self, username, password):
        userpass = f"{username}:{password}"
        encoded_credentials = base64.b64encode(userpass.encode('utf-8')).decode('utf-8')
        return "Basic " + encoded_credentials
        
    def test_mqtt_connection(self, server, port, username, password):
        """测试 MQTT 服务器连接"""
        result = {"success": False, "error": ""}
        connected = False
        
        def on_connect(client, userdata, flags, rc):
            nonlocal connected
            if rc == 0:
                connected = True
            else:
                result["error"] = f"Connection failed with code {rc}"
        
        client = mqtt.Client()
        client.on_connect = on_connect
        
        if username and password:
            client.username_pw_set(username, password)
        
        try:
            client.connect(server, port, 5)  # 5秒连接超时
            client.loop_start()  # 启动网络循环（非阻塞）
            
            # 等待连接结果，最多5秒
            for _ in range(10):
                if connected:
                    result["success"] = True
                    break
                time.sleep(0.5)
            else:
                if not result["error"]:
                    result["error"] = "Connection timed out"
        except Exception as e:
            result["error"] = str(e)
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except:
                pass
        
        return result
        
    def parse_mqtt_server(self, server_str):
        """解析 MQTT 服务器地址，支持 host:port 格式"""
        if ":" in server_str:
            parts = server_str.split(":")
            host = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                port = 1883
            return host, port
        return server_str, 1883
            
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
                    

            elif webhost=="niu.com":
                headers = {
                    'User-Agent': USER_AGENT_NIU,
                    'Accept-Language': 'en-US'
                }
                self.session.headers = headers

                self.session.verify = True
                tokendata = await self.hass.async_add_executor_job(self._get_niu_token, username, password)
                if tokendata.get("status") != 0:
                    self._errors["base"] = tokendata.get("desc")
                    return await self._show_config_form(user_input)                    
                token = tokendata["data"]["token"]["access_token"]
                if token:
                    devicelistinfo = await self.hass.async_add_executor_job(self._get_niu_vehicles_info, token)     
                    deviceslist_data = devicelistinfo["data"]["items"]
                    _LOGGER.debug(deviceslist_data)
                    for deviceslist in deviceslist_data:
                        devices.append(str(deviceslist["sn_id"]))
                
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
                    
            elif webhost=="hellobike.com":
                headers = {
                    'content_type': 'text/plain;charset=utf-8',
                    'Accept': 'application/json, text/plain, */*'
                }                
                self.session.headers = headers
                self.session.verify = True
                
                status = await self.hass.async_add_executor_job(self._devicelist_hellobike, password)
                _LOGGER.debug(status)
                
                if status.get("code") != 0:
                    self._errors["base"] = status.get("msg")
                    
                    
                if status["data"].get("userBikeList"):
                    deviceslist_data = status["data"]["userBikeList"]
                    _LOGGER.debug(deviceslist_data)
                    for deviceslist in deviceslist_data:
                        resp = await self.hass.async_add_executor_job(self._get_hellobike_tracker, password, str(deviceslist["bikeNo"]))
                        if resp['code'] == 0:
                            devices.append(str(deviceslist["bikeNo"]))
                
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
                    
            elif webhost=="auto.amap.com":
                headers = {
                    'Host': 'ts.amap.com',
                    'Accept': 'application/json',
                    'sessionid': password.split("||")[1],
                    'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
                    'Cookie': 'sessionid=' + password.split("||")[1],
                    }
                self.session.headers = headers
                self.session.verify = True
                
                status = await self.hass.async_add_executor_job(self._devicelist_autoamap, password)
                _LOGGER.debug(status)
                
                if status.get("result") != "true":
                    self._errors["base"] = status.get("msg")
                    return await self._show_config_form(user_input)
                    
                if status["data"].get("carLinkInfoList"):
                    deviceslist_data = status["data"]["carLinkInfoList"]
                    _LOGGER.debug(deviceslist_data)
                    for deviceslist in deviceslist_data:
                        devices.append(str(deviceslist["tid"]))
                
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
                    
            elif webhost=="macless_haystack":
                if username.find("||") == -1:
                    username = username + "||0||0"
                status = await self.hass.async_add_executor_job(self._test_macless_haystack, username.split("||")[0], username.split("||")[1], username.split("||")[2])
                _LOGGER.debug(status)
                
                if status.get("success") == False:
                    self._errors["base"] = status.get("error")
                    return await self._show_config_form(user_input)
                
                if status.get("response").text != "Nothing to see here.":
                    _LOGGER.error("服务器参数不正确，返回的值提示服务器不正确。 %s", status.get("error",""))
                    self._errors["base"] = "服务器参数不正确，返回的值提示服务器不正确。" + status.get("error","")
                    return await self._show_config_form(user_input)
                
                try:
                    json_data = json.loads(password)
                except:
                    _LOGGER.error("密码中填写的json数据解析错误")
                    self._errors["base"] = "密码中填写的json数据解析错误。"
                    return await self._show_config_form(user_input)
                if not isinstance(json_data, list):
                    self._errors["base"] = "密码中填写的json数据格式不正确。"
                    return await self._show_config_form(user_input)
                
                if isinstance(json_data, list):
                    for deviceslist in json_data:
                        devices.append(str(deviceslist["id"]))

                    await self.async_set_unique_id(f"cloudpgs-{user_input[CONF_NAME]}-{user_input[CONF_WEB_HOST]}".replace(".","_"))
                    self._abort_if_unique_id_configured()
                    
                    config_data[CONF_USERNAME] = username
                    config_data[CONF_PASSWORD] = password
                    config_data[CONF_DEVICES] = devices
                    config_data[CONF_WEB_HOST] = webhost
                    
                    _LOGGER.debug(devices)
                    
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=config_data
                    )
            elif webhost=="gps_mqtt":
                # 确保用户名格式正确
                if "||" not in username:
                    _LOGGER.error("mqtt服务器格式不正确，请按 server||username||password 格式")
                    self._errors["base"] = "mqtt服务器格式不正确，请按 server||username||password 格式"
                    return await self._show_config_form(user_input)
                
                parts = username.split("||")
                if len(parts) < 3:
                    _LOGGER.error("mqtt服务器格式不正确，请按 server||username||password 格式")
                    self._errors["base"] = "mqtt服务器格式不正确，请按 server||username||password 格式"
                    return await self._show_config_form(user_input)
                
                server_str = parts[0]
                mqtt_username = parts[1]
                mqtt_password = parts[2]
                
                # 解析服务器地址和端口
                server, port = await self.hass.async_add_executor_job(
                    self.parse_mqtt_server, server_str
                )
                
                # 测试 MQTT 连接
                connection_test = await self.hass.async_add_executor_job(
                    self.test_mqtt_connection,
                    server,
                    port,
                    mqtt_username,
                    mqtt_password
                )
                
                if not connection_test["success"]:
                    _LOGGER.error("MQTT 连接失败: %s", connection_test["error"])
                    self._errors["base"] = f"MQTT 连接失败: {connection_test['error']}"
                    return await self._show_config_form(user_input)
                
                # 验证主题格式
                topic = user_input[CONF_PASSWORD]
                if not topic or not topic.strip():
                    self._errors["base"] = "主题不能为空"
                    return await self._show_config_form(user_input)
                    
                devices.append(topic)
                
                await self.async_set_unique_id(f"cloudpgs-{server}-{user_input[CONF_PASSWORD]}".replace(".","_").replace("/","_").replace(" ",""))
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

    async def _check_existing(self, host):
        for entry in self._async_current_entries():
            if host == entry.data.get(CONF_NAME):
                return True

class OptionsFlow(config_entries.OptionsFlow):
    """Config flow options for cloud."""

    def __init__(self, config_entry):
        """Initialize cloud options flow."""
        #self._config_entry = config_entry
        self._conf_app_id: str | None = None
        self._config = dict(config_entry.data)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_user()
        
    def update_password_from_user_input(self, entry_password: str | None, user_input: dict[str, any]) -> dict[str, any]:
        """Update the password if the entry has been updated.

        As we want to avoid reflecting the stored password in the UI,
        we replace the suggested value in the UI with a sentinel,
        and we change it back here if it was changed.
        """
        substituted_used_data = dict(user_input)
        # Take out the password submitted
        user_password: str | None = substituted_used_data.pop(CONF_PASSWORD, None)
        # Only add the password if it has changed.
        # If the sentinel password is submitted, we replace that with our current
        # password from the config entry data.
        password_changed = user_password is not None and user_password != PWD_NOT_CHANGED
        password = user_password if password_changed else entry_password
        substituted_used_data[CONF_PASSWORD] = password
        return substituted_used_data

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            updated_user_input = self.update_password_from_user_input(self._config.get("password"), user_input)
            self._config.update(updated_user_input)
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=self._config
            )
            await self.hass.config_entries.async_reload(self._config_entry_id)
            return self.async_create_entry(title="", data=self._config)
            
        listoptions = []  
        for deviceconfig in self.config_entry.data.get(CONF_DEVICES,[]):
            listoptions.append({"value": deviceconfig, "label": deviceconfig})
        
        if self.config_entry.data.get(CONF_WEB_HOST) == "tuqiang123.com":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_TOTALKM, "label": "totalkm"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_ACC, "label": "acc"},
                {"value": KEY_BATTERY, "label": "powbattery"}
            ]
            SWITCHSLIST = []
            BUTTONSLIST = [
                {"value": "nowtrack", "label": "nowtrack"}
            ]
        elif self.config_entry.data.get(CONF_WEB_HOST) == "hellobike.com":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_ACC, "label": "acc"},
                {"value": KEY_BATTERY, "label": "powbattery"}
            ]
            
            SWITCHSLIST = [
                {"value": "defence", "label": "defence"},
                {"value": "open_lock", "label": "open_lock"},
            ]
            
            BUTTONSLIST = [
                {"value": "bell", "label": "bell"}
            ]
        elif self.config_entry.data.get(CONF_WEB_HOST) == "gooddriver.cn":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_TOTALKM, "label": "totalkm"},
                {"value": KEY_ACC, "label": "acc"}
            ]
            
            SWITCHSLIST = []            
            BUTTONSLIST = []
        elif self.config_entry.data.get(CONF_WEB_HOST) == "cmobd.com":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_ACC, "label": "acc"}
            ]
            
            SWITCHSLIST = []            
            BUTTONSLIST = []
        elif self.config_entry.data.get(CONF_WEB_HOST) == "auto.amap.com":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_LASTRUNTIME, "label": "lastruntime"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_STATUS, "label": "status"},
            ]
            
            SWITCHSLIST = []            
            BUTTONSLIST = []
        elif self.config_entry.data.get(CONF_WEB_HOST) == "macless_haystack":
            SENSORSLIST = [
                {"value": KEY_LASTSEEN, "label": "lastseen"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_BATTERY_STATUS, "label": "battery_status"},
            ]
            
            SWITCHSLIST = []            
            BUTTONSLIST = []
        elif self.config_entry.data.get(CONF_WEB_HOST) == "gps_mqtt":
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_LASTRUNTIME, "label": "lastruntime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_TOTALKM, "label": "totalkm"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_ACC, "label": "acc"},
                {"value": KEY_RUNORSTOP, "label": "runorstop"},
                {"value": KEY_SHAKE, "label": "shake"},
                {"value": KEY_BATTERY, "label": "powbattery"}
            ]
            
            SWITCHSLIST = [
                {"value": "open_lock", "label": "open_lock"},
            ]            
            BUTTONSLIST = [
                {"value": "nowtrack", "label": "nowtrack"},
                {"value": "reboot", "label": "reboot"}
            ]
        else:
            SENSORSLIST = [
                {"value": KEY_PARKING_TIME, "label": "parkingtime"},
                {"value": KEY_LASTSTOPTIME, "label": "laststoptime"},
                {"value": KEY_ADDRESS, "label": "address"},
                {"value": KEY_SPEED, "label": "speed"},
                {"value": KEY_TOTALKM, "label": "totalkm"},
                {"value": KEY_STATUS, "label": "status"},
                {"value": KEY_ACC, "label": "acc"},
                {"value": KEY_BATTERY, "label": "powbattery"}
            ]
            SWITCHSLIST = []
            BUTTONSLIST = []
                
        return self.async_show_form(
            step_id="user",            
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD, default=PWD_NOT_CHANGED): cv.string,
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
                        CONF_WITH_MAP_CARD, 
                        default=self.config_entry.options.get(CONF_WITH_MAP_CARD,"none")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": "none", "label": "none"},
                                {"value": "baidu-map", "label": "baidu-map"},
                                {"value": "gaode-map", "label": "gaode-map"},
                            ], 
                            multiple=False,translation_key=CONF_WITH_MAP_CARD
                        )
                    ),
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
                        CONF_UPDATE_ADDRESSDISTANCE,
                        default=self.config_entry.options.get(CONF_UPDATE_ADDRESSDISTANCE, 50),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=10000)),
                    vol.Optional(
                        CONF_ADDRESSAPI, 
                        default=self.config_entry.options.get(CONF_ADDRESSAPI,"none")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": "none", "label": "none"},
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

