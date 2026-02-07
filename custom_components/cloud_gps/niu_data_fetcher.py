import logging
import requests
import json
import time
import datetime
import hashlib
from time import gmtime, strftime

# 保持与其他模块一致的引用
from .const import (
    KEY_ADDRESS,
    KEY_PARKING_TIME,
    KEY_LASTSTOPTIME,
    KEY_LASTRUNTIME,
    KEY_LASTONLINETIME,
    KEY_LASTOFFLINETIME,
    KEY_LASTSEEN,
    KEY_SPEED,
    KEY_TOTALKM,
    KEY_STATUS,
    KEY_ACC,
    KEY_BATTERY,
    KEY_BATTERY_STATUS,
    KEY_RUNORSTOP,
    KEY_SHAKE,
    KEY_TODAY_DIS,
    KEY_YESTERDAY_DIS,
    KEY_MONTH_DIS,
    KEY_YEAR_DIS,
)

_LOGGER = logging.getLogger(__name__)

# API 常量定义
NIU_USER_AGENT = 'manager/4.6.48 (android; IN2020 11);lang=zh-CN;clientIdentifier=Domestic;timezone=Asia/Shanghai;model=IN2020;deviceName=IN2020;ostype=android'
NIU_ACCOUNT_BASE_URL = "https://account.niu.com"
NIU_API_BASE_URL = "https://app-api.niu.com"

# API Endpoints
URL_LOGIN = "/app/v3/passport/login"  # 使用 v3 登录接口
URL_VEHICLE_LIST = "/v3/motoinfo/list"
URL_MOTOR_INDEX = "/v3/motor_data/index_info" # 车辆核心状态(GPS, 锁, 开关)
URL_BATTERY_INFO = "/v3/motor_data/battery_info" # 电池信息
URL_OVERALL_TALLY = "/v3/motoinfo/overallTally" # 总里程等统计

class DataFetcher:
    """Fetch the cloud gps data for NIU."""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.username = username
        self.password = password
        self.device_imei = device_imei  # 在小牛这里，配置的 IMEI 实际对应 SN
        self.location_key = location_key
        self.token = None
        self.token_expire_time = 0
        
        # 缓存数据
        self.trackerdata = {}
        
        # Session 设置
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': NIU_USER_AGENT,
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive'
        })

    def _get_token(self):
        """获取小牛 API Token"""
        # 如果 Token 还在有效期内（假设有效期 1 小时，这里设置 3000 秒缓冲），直接复用
        if self.token and time.time() < self.token_expire_time:
            return self.token

        url = NIU_ACCOUNT_BASE_URL + URL_LOGIN
        md5_password = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        data = {
            "account": self.username,
            "password": md5_password,
            "grant_type": "password",
            "scope": "base",
            "app_id": "niu_ktdrr960",
        }
        
        try:
            r = self.session.post(url, data=data, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("status") == 0 and "data" in resp:
                    self.token = resp["data"]["token"]["access_token"]
                    # 简单设定过期时间为当前时间 + 3600秒
                    self.token_expire_time = time.time() + resp["data"]["token"].get("refresh_expires_in", 3600)
                    _LOGGER.debug("NIU Token refreshed successfully.")
                    return self.token
                else:
                    _LOGGER.error(f"NIU Login failed: {resp.get('desc')}")
        except Exception as e:
            _LOGGER.error(f"NIU Login request error: {e}")
        
        return None

    def _api_request(self, method, endpoint, params=None, data=None):
        """通用的 API 请求封装"""
        if not self.token:
            if not self._get_token():
                return None

        url = NIU_API_BASE_URL + endpoint
        headers = {"token": self.token}
        
        try:
            if method == "GET":
                r = self.session.get(url, headers=headers, params=params, timeout=10)
            else:
                r = self.session.post(url, headers=headers, params=params, data=data, timeout=10)
            
            if r.status_code == 200:
                json_data = r.json()
                if json_data.get("status") == 0:
                    return json_data.get("data")
                else:
                    _LOGGER.warning(f"NIU API Error [{endpoint}]: {json_data.get('desc')}")
                    # 如果 token 失效 (通常 status 可能是特定的 code，这里简单处理重试逻辑)
                    if json_data.get("status") in [1021, 1022]: # 假设的 token 失效码，需根据实际情况调整
                        self.token = None 
            return None
        except Exception as e:
            _LOGGER.error(f"NIU API Connection Error [{endpoint}]: {e}")
            return None

    def _get_vehicle_list(self):
        """获取车辆列表"""
        return self._api_request("GET", URL_VEHICLE_LIST)

    def _get_motor_info(self, sn):
        """获取车辆主要状态 (GPS, 锁, ACC)"""
        return self._api_request("GET", URL_MOTOR_INDEX, params={"sn": sn})

    def _get_battery_info(self, sn):
        """获取电池信息"""
        return self._api_request("GET", URL_BATTERY_INFO, params={"sn": sn})

    def _get_overall_tally(self, sn):
        """获取统计信息 (总里程)"""
        # 注意：这个接口在原代码中是 POST，且参数不同
        return self._api_request("POST", URL_OVERALL_TALLY, data={"sn": sn})

    def _parse_time(self, timestamp_ms):
        """解析毫秒级时间戳"""
        if not timestamp_ms:
            return ""
        try:
            return datetime.datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    def _calculate_parking_time(self, last_stop_time_str):
        """计算停车时长"""
        if not last_stop_time_str:
            return "未知"
        try:
            last_stop = datetime.datetime.strptime(last_stop_time_str, "%Y-%m-%d %H:%M:%S")
            diff = datetime.datetime.now() - last_stop
            days = diff.days
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if days > 0:
                return f"{days}天{hours}小时{minutes}分钟"
            elif hours > 0:
                return f"{hours}小时{minutes}分钟"
            else:
                return f"{minutes}分钟"
        except Exception:
            return "计算错误"

    async def get_data(self):
        """
        主入口：被 Coordinator 调用。
        """
        # 在 executor 中运行同步的 requests 代码，防止阻塞 HA 主循环
        return await self.hass.async_add_executor_job(self._get_data_sync)

    def _get_data_sync(self):
        """同步获取数据的逻辑"""
        
        # 1. 确保 Token
        if not self._get_token():
            return self.trackerdata

        # 2. 获取账户下所有车辆，用于验证配置的 SN 是否存在以及获取基础信息
        vehicles_data = self._get_vehicle_list()
        if not vehicles_data or "items" not in vehicles_data:
            return self.trackerdata

        vehicle_map = {v["sn_id"]: v for v in vehicles_data["items"]}

        # 3. 遍历配置的设备 (device_imei 在这里当作 SN 使用)
        for sn in self.device_imei:
            _LOGGER.debug(f"Fetching NIU data for SN: {sn}")
            
            if sn not in vehicle_map:
                _LOGGER.warning(f"Device SN {sn} not found in NIU account.")
                continue
            
            # 基础设备信息
            base_info = vehicle_map[sn]
            device_model = base_info.get("scooter_name", "小牛电动车")
            
            # --- API 1: 核心状态 (GPS, Speed, Lock) ---
            motor_data = self._get_motor_info(sn)
            if not motor_data:
                continue

            # --- API 2: 电池信息 ---
            battery_data = self._get_battery_info(sn)
            
            # --- API 3: 总里程 ---
            tally_data = self._get_overall_tally(sn)

            # --- 数据解析与组装 ---
            
            # GPS 坐标 (NIU API 返回的通常是 GCJ02，CloudGPS 的 coordinator 会处理转换，这里只管传原始值)
            # 注意: NIU API 返回的 postion 字段可能拼写错误为 "postion" 或 "position"，视版本而定
            pos_data = motor_data.get("postion", {}) 
            lat = float(pos_data.get("lat", 0))
            lon = float(pos_data.get("lng", 0))
            gps_precision = motor_data.get("hdop", 0)

            # 状态判断
            is_connected = motor_data.get("isConnected", 0) == 1
            lock_status = motor_data.get("lockStatus", 0) # 1: Locked, 0: Unlocked
            is_charging = motor_data.get("isCharging", 0)
            now_speed = float(motor_data.get("nowSpeed", 0))

            # 在线状态
            online_status = "在线" if is_connected else "离线"
            
            # 运行状态 & ACC
            acc_status = "未知"
            if lock_status == 1:
                acc_status = "已锁车"
                status = "停车"
            else:
                acc_status = "已开锁" # 对应 ACC ON
                status = "行驶" if now_speed > 0 else "钥匙开启"

            if not is_connected:
                status = "离线"

            run_or_stop = "运动" if now_speed > 0 else "静止"

            # 电池数据处理
            battery_level = 0
            battery_status_str = "未充电"
            if battery_data and "batteries" in battery_data:
                # 通常取 compartmentA
                comp_a = battery_data["batteries"].get("compartmentA", {})
                battery_level = comp_a.get("batteryCharging", 0)
                if comp_a.get("isConnected"):
                    battery_status_str = "充电中" if is_charging else "放电中"
            
            # 辅助信息
            estimated_mileage = motor_data.get("estimatedMileage", 0) # 预估剩余里程
            left_time = motor_data.get("leftTime", "") # 剩余时间/停车时间信息?
            # 注意：leftTime 含义在小牛API中经常变化，有时是预估剩余骑行时间，有时是最后更新时间
            # 我们尽量从 lastTrack 获取时间
            
            last_track = motor_data.get("lastTrack", {})
            last_update_time_ms = last_track.get("time", 0)
            last_update_str = self._parse_time(last_update_time_ms)
            
            # 停车时长计算 (依赖于最后更新时间)
            parking_time = "未知"
            if now_speed == 0:
                parking_time = self._calculate_parking_time(last_update_str)

            # 总里程
            total_km = 0
            if tally_data:
                total_km = tally_data.get("totalMileage", 0)

            # 组装 Attributes (Key 必须与 const.py 对应)
            attrs = {
                KEY_SPEED: now_speed,
                KEY_STATUS: status,
                KEY_ACC: acc_status,
                KEY_RUNORSTOP: run_or_stop,
                "onlinestatus": online_status,
                KEY_LASTSEEN: last_update_str,
                KEY_QUERYTIME: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                KEY_PARKING_TIME: parking_time,
                KEY_BATTERY: battery_level, # 实际上是百分比
                KEY_BATTERY_STATUS: battery_status_str,
                KEY_TOTALKM: total_km,
                "estimated_range": estimated_mileage, # 额外属性
                "gps_accuracy": gps_precision
            }
            
            # 兼容 CloudGPS 的 deviceinfo 结构
            device_info = {
                "device_model": device_model,
                "sw_version": "Cloud API",
                "expiration": "永久"
            }

            # 写入结果字典
            self.trackerdata[sn] = {
                "location_key": self.location_key + str(sn),
                "deviceinfo": device_info,
                "thislat": lat,
                "thislon": lon,
                "status": status,
                "attrs": attrs
            }
            
            _LOGGER.debug(f"NIU Data for {sn} processed: {status}, Bat: {battery_level}%")

        return self.trackerdata

class DataButton:
    def __init__(self, hass, username, password, imei, mqtt_manager=None):
        self.hass = hass
        # 复用 DataFetcher 来处理 Token 和 API 请求
        self.fetcher = DataFetcher(hass, username, password, [imei], "")

    async def _action(self, command_type):
        """发送控制指令"""
        # 在 executor 中运行，防止阻塞
        return await self.hass.async_add_executor_job(self._send_command_sync, command_type)

    def _send_command_sync(self, command_type):
        # 确保获取到 Token
        if not self.fetcher._get_token():
            return "Token获取失败"

        # 这里使用验证过的发送指令 API
        # 注意：小牛发指令通常需要 SN，而 DataButton 初始化传入的 imei 即为 SN
        sn = self.fetcher.device_imei[0] 
        url = NIU_API_BASE_URL + "/v5/cmd/creat"

        headers = {
            "token": self.fetcher.token,
            "Content-Type": "application/json; charset=utf-8",
            # 发送指令最好模拟 iOS 客户端，成功率较高
            "User-Agent": "manager/5.12.4 (iPhone; iOS 18.5; Scale/3.00);deviceName=iPhone;timezone=Asia/Shanghai;model=iPhone13,4;lang=zh-CN;ostype=iOS;clientIdentifier=Domestic"
        }
        payload = json.dumps({"sn": sn, "type": command_type})

        try:
            r = requests.post(url, headers=headers, data=payload, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("status") == 0:
                    _LOGGER.info(f"NIU Command {command_type} success.")
                    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    _LOGGER.error(f"NIU Command failed: {resp}")
                    return "指令发送失败"
        except Exception as e:
            _LOGGER.error(f"NIU Command Error: {e}")
            return "网络错误"
        return "未知错误"
        
class DataSwitch:
    def __init__(self, hass, username, password, imei, mqtt_manager=None):
        self.hass = hass
        # 复用 DataButton 已经写好的指令发送逻辑，因为 DataButton 也有 fetcher
        self.button_logic = DataButton(hass, username, password, imei, mqtt_manager)

    async def _turn_on(self, key):
        """打开开关"""
        if key == "open_lock":
            # 发送 ACC ON 指令
            await self.button_logic._action("acc_on")

    async def _turn_off(self, key):
        """关闭开关"""
        if key == "open_lock":
            # 发送 ACC OFF 指令
            await self.button_logic._action("acc_off")
