'''  

'''
import logging
import json
import time
import datetime
import math
import asyncio
import paho.mqtt.client as mqtt
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)

EARTH_RADIUS = 6378.137  # 地球半径（公里）
MIN_DISTANCE_FOR_MOVEMENT = 25  # 移动的最小距离阈值（米）
MIN_SPEED_FOR_MOVEMENT = 1.0    # 移动的最小速度阈值（km/h）

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)

class MqttConnectionManager:
    """通用的 MQTT 连接管理器"""
    
    def __init__(self, connection_str, topic=None):
        """
        初始化 MQTT 连接管理器
        
        :param connection_str: MQTT 连接字符串，格式为 "server||username||password"
        :param topic: MQTT 主题（可选）
        """
        self.connection_str = connection_str
        self.base_topic = topic
        self.mqtt_client = None
        self._connected = False
        self._connecting = False
        self._parse_connection()
        
    def _parse_connection(self):
        """解析 MQTT 连接信息"""
        mqtt_parts = self.connection_str.split("||")
        if len(mqtt_parts) != 3:
            raise ValueError("Invalid MQTT connection format. Expected: 'server||username||password'")
        
        server_str = mqtt_parts[0]
        self.mqtt_server, self.mqtt_port = self._parse_mqtt_server(server_str)
        self.mqtt_username = mqtt_parts[1]
        self.mqtt_password = mqtt_parts[2]
        
        # 如果没有提供主题，使用密码作为主题
        if self.base_topic is None:
            self.base_topic = self.mqtt_password
            
                
    def get_command_topic(self):
        """获取命令主题，格式为 <base_topic>/command"""
        return f"{self.base_topic}/command"

    def _parse_mqtt_server(self, server_str):
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
        
    async def connect(self, client_id_prefix="ha_mqtt"):
        """连接 MQTT 服务器"""
        if self._connected or self._connecting:
            return True
            
        self._connecting = True
        _LOGGER.info(f"Connecting to MQTT server: {self.mqtt_server}")
        
        try:
            client_id = f"{client_id_prefix}_{int(time.time())}"
            self.mqtt_client = mqtt.Client(client_id=client_id)
            self.mqtt_client.username_pw_set(self.mqtt_username, self.mqtt_password)
            
            # 在事件循环中运行 MQTT 客户端
            await asyncio.get_event_loop().run_in_executor(
                None, 
                self.mqtt_client.connect, 
                self.mqtt_server, 
                self.mqtt_port, 
                60
            )
            
            self.mqtt_client.loop_start()
            _LOGGER.info("MQTT client started")
            self._connecting = False
            self._connected = True
            return True
        except Exception as e:
            self._connecting = False
            _LOGGER.error(f"Failed to connect to MQTT: {e}")
            return False
            
    async def disconnect(self):
        """断开 MQTT 连接"""
        if self.mqtt_client and self._connected:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self._connected = False
            _LOGGER.info("MQTT client stopped")

    
    async def publish(self, message, topic=None, qos=1):
        """发布 MQTT 消息"""
        if not self._connected:
            if not await self.connect():
                return False
                
        try:
            publish_topic = topic or self.topic
            json_message = json.dumps(message)
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                self.mqtt_client.publish,
                publish_topic,
                json_message,
                qos
            )
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.debug(f"Published message to {publish_topic}: {json_message}")
                return True
            else:
                _LOGGER.error(f"Failed to publish message: {mqtt.error_string(result.rc)}")
                return False
        except Exception as e:
            _LOGGER.error(f"Error publishing message: {e}")
            return False
            
    def is_connected(self):
        """检查是否已连接"""
        return self._connected


class DataFetcher:
    """处理 MQTT 数据并维护设备状态"""
    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        # 确保 device_imei 是列表
        self.device_imei = [device_imei] if isinstance(device_imei, str) else device_imei
        self.username = username
        self.password = password
        
        # 使用 MQTT 连接管理器
        self.mqtt_manager = MqttConnectionManager(username, password)
        
        # 设备状态数据
        self.state_history = {}
        self.deviceinfo = {}
        self.trackerdata = {}
        self.server_distance = 0
        self._stopped = False
        
        # 初始化存储
        self._store = Store(
            hass, 
            version=1, 
            key=f"mqtt_gps_{slugify(location_key)}",
            private=False,
            encoder=DateTimeEncoder
        )
        self._persisted_data_loaded = False
        
        # 启动MQTT连接
        asyncio.create_task(self.start())
        
    # ======================== MQTT 连接管理 ========================
    async def connect_mqtt(self):
        """连接 MQTT 服务器并订阅主题"""
        if self._stopped:
            return False
            
        if self.mqtt_manager.is_connected():
            return True
            
        if await self.mqtt_manager.connect(client_id_prefix="ha_gps"):
            # 设置回调函数
            self.mqtt_manager.mqtt_client.on_connect = self.on_connect
            self.mqtt_manager.mqtt_client.on_message = self.on_message
            self.mqtt_manager.mqtt_client.on_disconnect = self.on_disconnect
            return True
        return False

    def on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调"""
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            # 订阅设备主题
            topic = self.mqtt_manager.base_topic
            result = client.subscribe(topic)
            if result[0] == mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.info("Subscribed to topic: %s", topic)
            else:
                _LOGGER.error("Failed to subscribe to topic %s: %s", 
                              topic, mqtt.error_string(result[0]))
        else:
            error_msg = mqtt.connack_string(rc)
            _LOGGER.error("MQTT connection failed (rc=%s): %s", rc, error_msg)
            # 安全地安排重连
            if not self._stopped and self.hass.loop.is_running():
                self.hass.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._schedule_reconnect())
                )

    def on_disconnect(self, client, userdata, rc):
        """MQTT 断开连接回调"""
        self.mqtt_manager._connected = False
        if rc != 0:
            _LOGGER.warning("Unexpected disconnection from MQTT broker (rc=%s): %s", 
                            rc, mqtt.error_string(rc))
        else:
            _LOGGER.info("Disconnected normally from MQTT broker")
        
        # 安全地安排重连任务
        if not self._stopped and self.hass.loop.is_running():
            self.hass.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            )

    async def _schedule_reconnect(self):
        """安排重连任务"""
        if self._stopped:
            return
            
        await asyncio.sleep(10)  # 延迟10秒重连
        if not self._stopped:
            _LOGGER.info("Attempting to reconnect to MQTT broker")
            try:
                await self.connect_mqtt()
            except Exception as e:
                _LOGGER.error("Reconnect attempt failed: %s", e)

    async def start(self):
        """启动 MQTT 连接"""
        await self.connect_mqtt()
        
    async def stop(self):
        """停止 MQTT 连接"""
        self._stopped = True
        await self.mqtt_manager.disconnect()

    def on_message(self, client, userdata, msg):
        """MQTT 消息回调"""
        try:
            payload = json.loads(msg.payload.decode())
            _LOGGER.debug("Received MQTT message on topic %s: %s", msg.topic, payload)
            for imei in self.device_imei:
                asyncio.run_coroutine_threadsafe(
                    self.process_message(imei, payload),
                    self.hass.loop
                )
        except Exception as e:
            _LOGGER.error("Error processing MQTT message: %s", e)

    # ======================== 数据处理逻辑 ========================
    async def _load_persisted_data(self):
        """异步加载持久化数据"""
        try:
            persisted_data = await self._store.async_load() or {}
            self.state_history = persisted_data.get("state_history", {})
            
            # 转换时间字符串为 datetime 对象
            for imei, state in self.state_history.items():
                # 转换时间字段
                for time_field in ["t", "lastupdate"]:
                    if time_field in state and isinstance(state[time_field], str):
                        try:
                            state[time_field] = datetime.datetime.fromisoformat(state[time_field])
                        except (ValueError, TypeError):
                            state[time_field] = datetime.datetime.now()
                
                # 确保其他时间字段存在
                if "laststoptime" not in state:
                    state["laststoptime"] = time.time()
            
            _LOGGER.debug("Loaded and converted persisted data: %s", self.state_history)
        except Exception as e:
            _LOGGER.error("Error loading persisted data: %s", e)
            self.state_history = {}

    async def _persist_data(self):
        """异步保存数据到持久化存储"""
        try:
            data_to_save = {
                "state_history": self.state_history
            }
            await self._store.async_save(data_to_save)
            _LOGGER.debug("Persisted data saved")
        except Exception as e:
            _LOGGER.error("Error saving persisted data: %s", e)

    def get_distance(self, lat1, lng1, lat2, lng2):
        """计算两点间距离（米）"""
        rad_lat1 = lat1 * math.pi / 180.0
        rad_lat2 = lat2 * math.pi / 180.0
        a = rad_lat1 - rad_lat2
        b = lng1 * math.pi / 180.0 - lng2 * math.pi / 180.0
        s = 2 * math.asin(math.sqrt(math.pow(math.sin(a / 2), 2) + 
                             math.cos(rad_lat1) * math.cos(rad_lat2) * math.pow(math.sin(b / 2), 2)))
        return s * EARTH_RADIUS * 1000

    def time_diff(self, timestamp):
        """计算时间差"""
        if isinstance(timestamp, (int, float)):
            dt = datetime.datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, datetime.datetime):
            dt = timestamp
        else:
            return "未知"
            
        result = datetime.datetime.now() - dt
        hours = int(result.seconds / 3600)
        minutes = int(result.seconds % 3600 / 60)
        seconds = result.seconds % 3600 % 60
        
        if result.days > 0:
            return f"{result.days}天{hours}小时{minutes}分钟"
        elif hours > 0:
            return f"{hours}小时{minutes}分钟"
        elif minutes > 0:
            return f"{minutes}分钟{seconds}秒"
        else:
            return f"{seconds}秒"

    def to_date_time_string(self, dt):
        """将 datetime 对象或时间戳转换为字符串格式：YYYY-MM-DD HH:MM:SS"""
        if isinstance(dt, datetime.datetime):
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(dt, (int, float)):
            return datetime.datetime.fromtimestamp(dt).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(dt, str):
            # 尝试解析 ISO 格式字符串
            try:
                return datetime.datetime.fromisoformat(dt).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return dt  # 返回原始字符串
        return "未知时间"

    async def process_message(self, imei, payload):
        """处理 MQTT 消息并更新设备状态"""
        # 首次加载持久化数据
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
        
        # 初始化设备状态
        if imei not in self.state_history:
            # 如果没有数据，创建一个完整状态
            self.state_history[imei] = {
                "gps": {"lat": 0, "lng": 0, "speed": 0, "course": 0},
                "lbs": {"lat": 0, "lng": 0},
                "s": 0,
                "acc": 1,
                "adc": 0,
                "csq": 0,
                "f": 0,
                "ol": 1,
                "m": 0,
                "In1": 0,
                "t": datetime.datetime.now(),
                "lastupdate": datetime.datetime.now(),
                "laststoptime": time.time(),
                "runorstop": "停止",
                "totalkm": 0,
                "latitude": 0,
                "longitude": 0
            }
        
        for key in payload:
            if key == "gps" and payload.get("gps") is not None:
                # 只更新提供的 GPS 字段
                for gps_key in payload["gps"]:
                    self.state_history[imei]["gps"][gps_key] = payload["gps"][gps_key]
            elif payload[key] is not None:
                self.state_history[imei][key] = payload[key]
        
        
        
        # 获取当前 GPS 数据
        longitude = float(self.state_history[imei]["gps"]["lng"])
        latitude = float(self.state_history[imei]["gps"]["lat"])
        speed = float(self.state_history[imei]["gps"]["speed"])
        course = float(self.state_history[imei]["gps"]["course"])

        # 计算与上次位置的距离
        last_lat = self.state_history[imei].get("latitude")
        last_lng = self.state_history[imei].get("longitude")
        self.server_distance = self.get_distance(
            latitude, longitude,
            last_lat, last_lng
        ) if last_lat and last_lng else 0
        
        # 确定运动状态
        runorstop = "停止"
        if speed < MIN_SPEED_FOR_MOVEMENT and self.state_history[imei].get("s", 0) == 0:
            runorstop = "停止"

            speed = 0
        elif self.server_distance > MIN_DISTANCE_FOR_MOVEMENT and self.state_history[imei].get("s", 0) == 1:
            runorstop = "运动"
            # 更新经纬度
            self.state_history[imei]["latitude"] = self.state_history[imei]["gps"]["lat"]
            self.state_history[imei]["longitude"] = self.state_history[imei]["gps"]["lng"]
            # 更新总里程
            self.state_history[imei]["totalkm"] += self.server_distance / 1000
        
        # 处理状态变化
        if self.state_history[imei]["runorstop"] == "运动" and runorstop == "停止":
            self.state_history[imei]["laststoptime"] = time.time()
            
        if self.state_history[imei].get("ol", 0) == 1 and self.state_history[imei].get("old_ol",0) == 0:
            self.state_history[imei]["old_ol"] = 1
            self.state_history[imei]["lastonlinetime"] = datetime.datetime.now()
            
        if self.state_history[imei].get("ol", 1) == 0 and self.state_history[imei].get("old_ol",1) == 1:
            self.state_history[imei]["old_ol"] = 0
            self.state_history[imei]["lastofflinetime"] = datetime.datetime.now()
        
        # 更新当前运动状态
        self.state_history[imei]["runorstop"] = runorstop
            
        # 更新最后更新时间
        self.state_history[imei]["lastupdate"] = datetime.datetime.now()
        
        # 保存状态
        await self._persist_data()
        _LOGGER.debug("[%s] Updated state: %s", imei, self.state_history[imei])

    async def get_data(self):
        """获取设备格式化数据"""
        # 确保加载持久化数据
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
            
        self.trackerdata = {}
        for imei in self.device_imei:
            device_state = self.state_history.get(imei)
            if not device_state:
                _LOGGER.debug("No state data for device: %s", imei)
                continue
                
            thislat = device_state.get("latitude",0)
            thislon = device_state.get("longitude",0)
            accuracy = 0
            
            # 计算停车时间
            parking_time = ""
            if "laststoptime" in device_state:
                try:
                    parking_time = self.time_diff(device_state["laststoptime"])
                except Exception as e:
                    _LOGGER.error("Error calculating parking time: %s", e)
                    parking_time = "未知"
            
            # 确定设备状态
            status = "离线"

            if device_state.get("runorstop") == "停止":
                status = "停车"
            
            # 震动状态
            shake = "震动" if device_state.get("s", 0) == 1 else "静止"
            if shake == "震动":
                status = "震动"
            
            # ACC 状态
            acc = "车辆启动" if device_state.get("acc", 0) == 0 else "车辆熄火"
            if acc == "车辆启动":
                status = "车辆启动"
                
            if device_state.get("runorstop") == "运动":
                status = "行驶"
            
            # GPS 定位状态
            gps_fix = "gps已定位" if device_state.get("f", 0) == 1 else "gps未定位"
            if gps_fix == "gps已定位":
                device_state["lastgpstime"] = time.time()
            
            # 在线状态
            onlinestatus = "在线" if device_state.get("ol", 0) == 1 else "离线"
            if onlinestatus == "离线":
                status = "离线"
            
            lastonlinetime = device_state.get("lastonlinetime", "")
            lastofflinetime = device_state.get("lastofflinetime", "")
            
            # 格式化时间 - 使用安全转换函数
            last_update = self.to_date_time_string(device_state.get("lastupdate", datetime.datetime.now()))
            query_time = self.to_date_time_string(datetime.datetime.now())
            
            last_stop_time = ""
            if "laststoptime" in device_state:
                last_stop_time = self.to_date_time_string(device_state["laststoptime"])
            
            attrs = {
                "latitude": thislat,
                "longitude": thislon,
                "speed": device_state.get("speed", 0),
                "course": device_state.get("course", 0),
                "lbslat": float(device_state["lbs"]["lat"]),
                "lbslng": float(device_state["lbs"]["lng"]),
                "lbsmap": f"http://apis.map.qq.com/uri/v1/marker?coord_type=1&marker=title:+;coord:{device_state['lbs']['lat']},{device_state['lbs']['lng']}",
                "acc": acc,
                "powbatteryvoltage": float(device_state.get("adc", 0)) / 1000,
                "csq": int(device_state.get("csq", 0)),
                "status": status,
                "shake": shake,
                "In1": device_state.get("In1", 0),
                "gpsisfix": gps_fix,
                "onlinestatus": onlinestatus,
                "parkingtime": parking_time,
                "laststoptime": last_stop_time,
                "lastonlinetime": lastonlinetime,
                "lastofflinetime": lastofflinetime,
                "last_update": last_update,
                "querytime": query_time,
                "distance": device_state.get("m", 0),
                "serverdistance": self.server_distance,
                "totalKm": device_state.get("totalkm", 0),
                "runorstop": device_state.get("runorstop", "停止")
            }
            
            # 设备信息
            self.deviceinfo[imei] = {
                "device_model": "MQTT GPS Tracker",
                "sw_version": "1.0",
                "tid": imei,
            }
            
            self.trackerdata[imei] = {
                "location_key": f"{self.location_key}_{imei}",
                "deviceinfo": self.deviceinfo[imei],
                "thislat": thislat,
                "thislon": thislon,
                "accuracy": accuracy,
                "source_type": "gps",
                "imei": imei,
                "status": status,
                "attrs": attrs
            }
        return self.trackerdata

            
            
class DataButton:
    """通过 MQTT 发布消息执行按钮动作"""

    def __init__(self, hass, username, password, device_imei):
        self.hass = hass
        self._username = username
        self._password = password
        self.device_imei = device_imei
        self.mqtt_manager = MqttConnectionManager(username, password)
        
    async def _do_action(self, action):
        """通过 MQTT 发布动作消息"""
        message = action
        command_topic = self.mqtt_manager.get_command_topic()
        _LOGGER.debug("[%s] mqtt_manager.publish: %s ,topic: %s", self.device_imei, message, command_topic)
        return await self.mqtt_manager.publish(message, topic=command_topic)

    async def _action(self, action):
        """执行按钮动作"""
        success = await self._do_action(action)
        state = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        asyncio.create_task(self._delayed_disconnect())
        return state if success else "执行失败"
        
    async def _delayed_disconnect(self):
        """延迟断开 MQTT 连接"""
        await asyncio.sleep(5)  # 保持连接5秒，避免频繁操作时重复连接
        await self.mqtt_manager.disconnect()

    

class DataSwitch:

    def __init__(self, hass, username, password, device_imei):
        self.hass = hass
        self._username = username
        self._password = password
        self.device_imei = device_imei
        self.mqtt_manager = MqttConnectionManager(username, password)
        
    async def _do_action(self, action):
        """通过 MQTT 发布动作消息"""
        message = action
        command_topic = self.mqtt_manager.get_command_topic()
        _LOGGER.debug("[%s] mqtt_manager.publish: %s ,topic: %s", self.device_imei, message, command_topic)
        return await self.mqtt_manager.publish(message, topic=command_topic)
        
    async def _delayed_disconnect(self):
        """延迟断开 MQTT 连接"""
        await asyncio.sleep(5)  # 保持连接5秒，避免频繁操作时重复连接
        await self.mqtt_manager.disconnect()      
        
    async def _turn_on(self, action): 
        if action == "open_lock":
            success = await self._do_action({"cmd":"on1"})
            state = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            asyncio.create_task(self._delayed_disconnect())
            return state if success else "执行失败"

            
    async def _turn_off(self, action): 
        
        if action == "open_lock":
            success = await self._do_action({"cmd":"off1"})
            state = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            asyncio.create_task(self._delayed_disconnect())
            return state if success else "执行失败"
