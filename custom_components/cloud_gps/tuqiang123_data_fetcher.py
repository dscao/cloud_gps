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
import homeassistant.util.dt as dt_util
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
        self.address = {}
        self.totalkm = {}
        self.dis = {}

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
            'equipment.userId': self.userid,
            'equipment.imei': str(imei_sn).strip()
        }
        resp = self.session_tuqiang123.post(url, data=p_data)
        
        results = resp.json().get('data', {}).get('result', [])
        for item in results:
            if str(item.get('imei', '')).strip() == str(imei_sn).strip():
                return item
                
        if results:
            return results[0]
        return {}

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

    def _get_device_mileage(self, imei_sn, start_time, end_time):
        url = TUQIANG123_API_HOST + '/mileageReportController/getList'
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
        resp = self.session_tuqiang123.post(url, data=p_data)
        respjson = resp.json()
        if respjson.get("code") != 0:
            raise ValueError(respjson.get("msg") or "获取里程报表失败")
        respdata = respjson.get("data")
        if not isinstance(respdata, dict):
            respdata = {}
        result = respdata.get("result") or []
        # 平台返回的 totalDistiance 即为区间总里程，缺失或异常时累加明细
        total = None
        raw_total = respdata.get("totalDistiance")
        if raw_total is not None and str(raw_total).strip() != "":
            try:
                total = float(raw_total)
            except (TypeError, ValueError):
                total = None
        if total is None:
            total = 0.0
            for item in result:
                try:
                    total += float(item.get("dis") or 0)
                except (TypeError, ValueError):
                    pass
        return round(total, 2)

    def _mileage_query(self, period):
        """按周期返回 (缓存标识, 起, 止)，时间格式为平台要求的 YYYY-MM-DD HH:MM:SS"""
        now = dt_util.now()
        if period == "today":
            start_day = end_day = now
            cache_key = now.strftime('%Y-%m-%d')
        elif period == "yesterday":
            start_day = end_day = now - datetime.timedelta(days=1)
            cache_key = start_day.strftime('%Y-%m-%d')
        elif period == "month":
            start_day = now.replace(day=1)
            end_day = now
            cache_key = now.strftime('%Y-%m')
        else:
            start_day = now.replace(month=1, day=1)
            end_day = now
            cache_key = now.strftime('%Y')
        return (cache_key,
                start_day.strftime('%Y-%m-%d 00:00:00'),
                end_day.strftime('%Y-%m-%d 23:59:59'))

    async def _update_mileage(self, imei, period):
        """按周期刷新今日/昨日/本月/今年里程，获取失败时保留上一次的值"""
        cache = self.dis.setdefault(imei, {})
        cache_key, start_time, end_time = self._mileage_query(period)
        interval = 600 if period == "today" else 3600
        if (cache.get(f"{period}_dis_key") == cache_key
                and int(datetime.datetime.now().timestamp()) - int(cache.get(f"{period}_dis_time") or 0) < interval):
            return
        try:
            async with timeout(10):
                mileage = await self.hass.async_add_executor_job(self._get_device_mileage, imei, start_time, end_time)
        except Exception as e:
            _LOGGER.debug("途强在线 %s 获取%s里程失败: %s", imei, period, e)
            return
        cache[f"{period}_dis"] = mileage
        cache[f"{period}_dis_time"] = int(datetime.datetime.now().timestamp())
        cache[f"{period}_dis_key"] = cache_key

    def _get_device_address(self, lat, lng):
        url = TUQIANG123_API_HOST + '/getAddress?lat='+str(lat)+'&lng='+str(lng)+'&mapType=baiduMap&poiList='
        resp = self.session_tuqiang123.get(url)
        return resp.json()['msg']

    def _get_instruction_and_params(self, imei, mc_type, type_id):
        url = TUQIANG123_API_HOST + '/device/getInstructionAndParams'
        p_data = {
            'imei': str(imei),
            'mcType': str(mc_type),
            'typeId': str(type_id)
        }
        try:
            resp = self.session_tuqiang123.post(url, data=p_data)
            return type_id, resp.json()
        except Exception:
            return type_id, None

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

        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self.username, self.password)

        for imei in self.device_imei:
            self.dis[imei] = self.dis.get(imei, {})
            self.dis[imei]["today_dis"]  = self.dis[imei].get("today_dis", 0)
            self.dis[imei]["today_dis_time"]  = self.dis[imei].get("today_dis_time", 0)
            self.dis[imei]["yesterday_dis"]  = self.dis[imei].get("yesterday_dis", 0)
            self.dis[imei]["yesterday_dis_time"]  = self.dis[imei].get("yesterday_dis_time", 0)
            self.dis[imei]["month_dis"]  = self.dis[imei].get("month_dis", 0)
            self.dis[imei]["month_dis_time"]  = self.dis[imei].get("month_dis_time", 0)
            self.dis[imei]["year_dis"]  = self.dis[imei].get("year_dis", 0)
            self.dis[imei]["year_dis_time"]  = self.dis[imei].get("year_dis_time", 0)

            if not self.deviceinfo.get(imei):

                try:
                    async with timeout(10):
                        infodata =  await self.hass.async_add_executor_job(self._get_device_info, imei)
                except (
                    ClientConnectorError
                ) as error:
                    raise

                if infodata:
                    self.deviceinfo[imei] =infodata
                    self.deviceinfo[imei]["device_model"] = "途强在线GPS"
                    self.deviceinfo[imei]["sw_version"] = infodata.get("mcTypeAlias", infodata.get("mcType", "未知"))
                    self.deviceinfo[imei]["expiration"] = infodata.get("expiration", "")
            data = None
            try:
                async with timeout(10):
                    data =  await self.hass.async_add_executor_job(self._get_device_tracker, imei)
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

                mc_type = self.deviceinfo[imei].get("mcType", "EV49-DM")
                param_tasks = []
                for t_id in ["791", "793", "795", "809", "991", "999"]:
                    param_tasks.append(
                        self.hass.async_add_executor_job(self._get_instruction_and_params, imei, mc_type, t_id)
                    )
                
                defence_params = {}
                defence_mode = "未知"
                defence_state = "未知"

                if str(data.get("fortify", "")) == "1":
                    defence_state = "设防"
                elif str(data.get("fortify", "")) == "0":
                    defence_state = "撤防"

                if param_tasks:
                    try:
                        async with timeout(15):
                            params_results = await asyncio.gather(*param_tasks)
                            for t_id, res in params_results:
                                if res and res.get("code") == 0:
                                    data_obj = res.get("data", {})
                                    defence_params[f"type_{t_id}"] = data_obj

                                    inst_list = data_obj.get("instructionAndParams", [])
                                    rec_list = data_obj.get("commandRecordList", [])

                                    for inst in inst_list:
                                        inst_name = inst.get("orderName", inst.get("instructionName", ""))
                                        params = inst.get("paramList", [])
                                        
                                        if "设防模式" in inst_name or "DEFMODE" in str(inst.get("orderContent", "")):
                                            if params:
                                                target_id = str(params[0].get("id", ""))
                                                for rec in rec_list:
                                                    if str(rec.get("paramId", "")) == target_id:
                                                        val = str(rec.get("paramRecord", ""))
                                                        if val == "0" or val.endswith(",0"):
                                                            defence_mode = "自动"
                                                        elif val == "1" or val.endswith(",1"):
                                                            defence_mode = "手动"
                                                        break
                    except Exception as e:
                        _LOGGER.debug("途强在线 %s 获取设防状态警告: %s", imei, e)

                try:
                    totalKm = float(data.get("totalKm", self.totalkm.get(imei, 0)))
                except (ValueError, TypeError):
                    totalKm = self.totalkm.get(imei, 0)

                await self._update_mileage(imei, "today")
                await self._update_mileage(imei, "yesterday")
                await self._update_mileage(imei, "month")
                await self._update_mileage(imei, "year")


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
                    "today_dis": self.dis[imei].get("today_dis", 0),
                    "yesterday_dis": self.dis[imei].get("yesterday_dis", 0),
                    "month_dis": self.dis[imei].get("month_dis", 0),
                    "year_dis": self.dis[imei].get("year_dis", 0),
                    "positionType":positionType,
                    "statustime": statustime,
                    "defence_mode": defence_mode,
                    "defence_state": defence_state,
                    "defence_params": defence_params 
                }

                self.trackerdata[imei] = {"location_key":self.location_key+imei,"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"imei":imei,"status":status,"attrs":attrs}

        return self.trackerdata

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
        # 注意：这里如果需要动态适配，逻辑参考 DataSwitch 的 _build_payload
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
        return resp.json() # 直接返回 JSON 对象

    async def _action(self, action):
        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        resp = await self.hass.async_add_executor_job(self._do_action, action)
        _LOGGER.debug("Button action response: %s", resp)
        return resp # 返回原始 JSON，让 Button 实体去处理


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
            'User-Agent': TUQIANG_USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest'
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
        if response.json().get('code') == 0:
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
        resp = self.session_tuqiang123.post(url, data=body)
        try:
            return resp.json()
        except Exception:
            _LOGGER.error("解析 JSON 失败，接口返回: %s", resp.text[:200])
            return {"code": 500, "msg": "接口返回非 JSON 内容"}
        
    def _build_payload(self, action, state, attrs):
        payload = {
            'imei': self.device_imei,
            'instructionPwd': '',
            'isUsePwd': '0',
            'isOffLine': '1'
        }
        
        target_name = ""
        if action == "defence":
            target_name = "设防" if state else "撤防"
            fallback_id = "97" if state else "118"
            fallback_content = "111#" if state else "000#"
        elif action == "defencemode":
            target_name = "设防模式"
            fallback_id = "98"
            fallback_content = "DEFMODE,{0}#"
        elif action == "open_lock":
            target_name = "远程控制"
            fallback_id = "111676"
            fallback_content = "RELAY,{1}#" if state else "RELAY,{0}#"
            
        matched_item = None
        if attrs and "defence_params" in attrs:
            for t_id, data_obj in attrs["defence_params"].items():
                if isinstance(data_obj, dict):
                    inst_list = data_obj.get("instructionAndParams", [])
                    for inst in inst_list:
                        name = inst.get("orderName", inst.get("instructionName", ""))
                        if name == target_name:
                            matched_item = inst
                            break
                if matched_item: break
                        
        if matched_item:
            payload['instructionId'] = str(matched_item.get('id', fallback_id))
            payload['instructionName'] = str(matched_item.get('orderName', target_name))
            payload['orderContent'] = str(matched_item.get('orderContent', fallback_content))
            payload['isUsePwd'] = str(matched_item.get('isUsePwd', '0'))
            payload['isOffLine'] = str(matched_item.get('isOffLine', '1'))
            
            param_list = matched_item.get("paramList", [])
            if action == "defencemode":
                param_id = "22342"
                if param_list:
                    param_id = str(param_list[0].get("id", "22342"))
                payload['param'] = f"{param_id},0" if state else f"{param_id},1"
            elif action == "open_lock":
                param_id = "22352"
                if param_list:
                    param_id = str(param_list[0].get("id", "22352"))
                payload['param'] = f"{param_id},0" if state else f"{param_id},1"
            else:
                payload['param'] = ""
        else:
            payload['instructionId'] = fallback_id
            payload['instructionName'] = target_name
            payload['orderContent'] = fallback_content
            if action == "defencemode":
                payload['param'] = "22342,0" if state else "22342,1"
            elif action == "open_lock":
                payload['param'] = "22352,0" if state else "22352,1"
            else:
                payload['param'] = ""
                
        # 针对 open_lock 特殊处理密码参数（覆盖默认配置）
        if action == "open_lock":
            payload['instructionPwd'] = self._password  # 传入真实的集成用户密码
            payload['isUsePwd'] = '1'                   # 平台要求校验密码标识
                
        return payload

    async def _turn_on(self, action, attrs=None):

        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        url = TUQIANG123_API_HOST + '/device/sendIns'
        json_body = await self.hass.async_add_executor_job(self._build_payload, action, True, attrs)
        resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
        _LOGGER.debug("Turn ON %s response: %s", action, resp)
        return resp

    async def _turn_off(self, action, attrs=None):

        if self.userid is None or self.usertype is None:
            await self.hass.async_add_executor_job(self._login, self._username, self._password)

        url = TUQIANG123_API_HOST + '/device/sendIns'
        json_body = await self.hass.async_add_executor_job(self._build_payload, action, False, attrs)
        resp = await self.hass.async_add_executor_job(self._do_action, url, json_body)
        _LOGGER.debug("Turn OFF %s response: %s", action, resp)
        return resp