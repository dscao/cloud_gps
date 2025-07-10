import logging
import json
import time
import datetime
import math
import asyncio
import threading
import paho.mqtt.client as mqtt
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util # 导入 Home Assistant 的时间工具

_LOGGER = logging.getLogger(__name__)

EARTH_RADIUS = 6378.137  # 地球半径（公里）
MIN_DISTANCE_FOR_MOVEMENT =50   # 移动的最小距离阈值（米）
MIN_SPEED_FOR_MOVEMENT = 1.0     # 移动的最小速度阈值（km/h）

class DateTimeEncoder(json.JSONEncoder):
    """用于将 datetime 对象序列化为 ISO 格式字符串的 JSON 编码器。"""
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)

class SimpleMQTTManager:
    """简洁稳定的 MQTT 连接管理器"""
    
    def __init__(self, hass_loop, connection_str, topic=None):
        """
        初始化 MQTT 连接管理器
        :param hass_loop: Home Assistant 的事件循环，用于调度异步任务。
        :param connection_str: MQTT 连接字符串，格式为 "server||username||password"
        :param topic: MQTT 主题（可选），如果用于订阅，通常是带通配符的主题。
        """
        self.hass_loop = hass_loop # 存储 Home Assistant 的事件循环
        self.connection_str = connection_str
        topic_parts = topic.split("||")
        self.base_topic = topic if len(topic_parts) < 2 else topic_parts[0]
        self.command_topic = self.get_command_topic(self.base_topic) if len(topic_parts) < 2 else topic_parts[1]
            
        self.mqtt_client = None
        self.mqtt_clientid = None
        self._is_connected = False
        self._should_run = True
        self._reconnect_task = None
        self._message_callback = None # 用于存储外部的消息处理回调
        
        # 用于在连接成功并订阅后通知等待的异步任务
        self._connected_event = asyncio.Event() 
        
        self._parse_mqtt_info() # 解析连接信息
        
    def _parse_connection(self, server_str):
        """解析 MQTT 服务器地址和端口"""
        if ":" in server_str:
            host, port = server_str.split(":", 1)
            return host, int(port)
        return server_str, 1883

    def _parse_mqtt_info(self):
        """解析 MQTT 连接信息"""
        mqtt_parts = self.connection_str.split("||")
        if len(mqtt_parts) < 3:
            raise ValueError("Invalid MQTT connection format. Expected: 'server||username||password||clientID'")
            
        server_str = mqtt_parts[0]
        self.mqtt_server, self.mqtt_port = self._parse_connection(server_str) 
        self.mqtt_username = mqtt_parts[1]
        self.mqtt_password = mqtt_parts[2]
        if len(mqtt_parts) == 4:
            self.mqtt_clientid = mqtt_parts[3]
            
    def get_command_topic(self, topic):
        """获取命令主题，格式为 <base_topic>/command"""
        if topic and topic.endswith("/#"):
            return f"{topic.rstrip('/#')}/command"
        elif self.base_topic:
            return f"{topic}/command"
        return "command" # fallback

    def set_message_callback(self, callback):
        """设置接收到 MQTT 消息时的回调函数"""
        self._message_callback = callback
        if self.mqtt_client:
            # 确保在回调设置后立即应用到 Paho 客户端
            self.mqtt_client.on_message = self._on_message_wrapper 

    async def connect(self):
        """连接 MQTT 服务器"""
        # 如果已经连接且事件已设置，直接返回 True
        if self._is_connected and self._connected_event.is_set(): 
            _LOGGER.debug("MQTT client already connected and ready.")
            return True
            
        # 清理旧连接
        if self.mqtt_client:
            _LOGGER.debug("Closing existing MQTT client before new connection attempt.")
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception as e:
                _LOGGER.warning(f"Error disconnecting old MQTT client: {e}")
            finally:
                self.mqtt_client = None
                self._is_connected = False
                self._connected_event.clear() # 清除事件，表示未连接就绪
                
        # 创建新客户端
        client_id_prefix = slugify(self.mqtt_username) if self.mqtt_username else "ha_mqtt"
        client_id = f"{client_id_prefix}_{int(time.time())}" 
        if self.mqtt_clientid:
            client_id = self.mqtt_clientid
        self.mqtt_client = mqtt.Client(client_id=client_id)
        if self.mqtt_username != None and self.mqtt_username != "None":
            self.mqtt_client.username_pw_set(self.mqtt_username, self.mqtt_password)
        
        # 设置 MQTT 客户端的内部回调
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message_wrapper 
        
        _LOGGER.info(f"Attempting to connect to MQTT broker at {self.mqtt_server}:{self.mqtt_port}")
        try:
            # 使用 loop.run_in_executor 将阻塞的 connect 调用放到线程池中执行
            # connect() 在连接成功或失败后会返回
            await self.hass_loop.run_in_executor(
                None, self.mqtt_client.connect, self.mqtt_server, self.mqtt_port, 60
            )
            
            # 启动 MQTT 客户端的内部循环（在它自己的线程中）
            await self.hass_loop.run_in_executor(None, self.mqtt_client.loop_start)
            _LOGGER.debug("MQTT client loop started.")
            
            # 等待 _on_connect 回调来设置 _connected_event
            # 设置一个超时，防止无限等待
            await asyncio.wait_for(self._connected_event.wait(), timeout=10) 
            
            _LOGGER.info("MQTT client connected and ready for operations.")
            return True
        except asyncio.TimeoutError:
            _LOGGER.error("MQTT connection timed out after establishing socket. _on_connect did not fire in time or subscription failed.")
            self._is_connected = False
            self._connected_event.clear()
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            )
            return False
        except Exception as e:
            _LOGGER.error(f"Failed to connect to MQTT broker: {e}", exc_info=True)
            self._is_connected = False
            self._connected_event.clear()
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            )
            return False
            
    def _on_connect(self, client, userdata, flags, rc):
        """连接成功回调，在 MQTT 线程中执行。"""
        if rc == 0:
            _LOGGER.info("Successfully connected to MQTT broker (rc=0).")
            # 订阅主题
            topic = self.base_topic
            if topic:
                _LOGGER.debug(f"Attempting to subscribe to topic: {topic}")
                result, mid = client.subscribe(topic)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    _LOGGER.info(f"Subscribed to topic: {topic} (mid={mid})")
                    # 连接成功且订阅已发送，现在可以设置事件
                    self.hass_loop.call_soon_threadsafe(self._connected_event.set)
                    self.hass_loop.call_soon_threadsafe(lambda: setattr(self, '_is_connected', True))
                else:
                    _LOGGER.error(f"Failed to subscribe to topic {topic}: {mqtt.error_string(result)}")
                    # 订阅失败，仍认为连接不稳定，触发重连
                    self.hass_loop.call_soon_threadsafe(self._connected_event.clear)
                    self.hass_loop.call_soon_threadsafe(lambda: setattr(self, '_is_connected', False))
                    self.hass_loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._schedule_reconnect())
                    )
            else:
                _LOGGER.warning("No base_topic configured for subscription. Connection considered ready.")
                # 没有订阅主题，直接认为连接就绪
                self.hass_loop.call_soon_threadsafe(self._connected_event.set)
                self.hass_loop.call_soon_threadsafe(lambda: setattr(self, '_is_connected', True))
        else:
            _LOGGER.error(f"MQTT connection failed with result code {rc}: {mqtt.connack_string(rc)}")
            self.hass_loop.call_soon_threadsafe(self._connected_event.clear)
            self.hass_loop.call_soon_threadsafe(lambda: setattr(self, '_is_connected', False))
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            )
            
    def _on_disconnect(self, client, userdata, rc):
        """断开连接回调，在 MQTT 线程中执行。"""
        _LOGGER.warning(f"MQTT client disconnected with result code {rc}: {mqtt.error_string(rc)}")
        
        self.hass_loop.call_soon_threadsafe(self._connected_event.clear)
        self.hass_loop.call_soon_threadsafe(lambda: setattr(self, '_is_connected', False))
        
        if self._should_run:
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            )
            
    def _on_message_wrapper(self, client, userdata, msg):
        """MQTT 消息回调包装器，将消息调度到主事件循环处理。"""
        if self._message_callback:
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._message_callback(msg.topic, msg.payload))
            )
        else:
            _LOGGER.warning("MQTT message received but no callback is set.")
            
    async def _schedule_reconnect(self):
        """安排重连任务（指数退避）"""
        if not self._should_run:
            return
            
        if self._reconnect_task and not self._reconnect_task.done():
            # 取消正在进行的重连任务，避免重复
            _LOGGER.debug("Cancelling existing reconnect task.")
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task 
            except asyncio.CancelledError:
                pass
                
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        
    async def _reconnect_loop(self):
        """重连循环"""
        attempts = 0
        base_delay = 5
        max_delay = 300
        
        while self._should_run and not self._is_connected: 
            delay = min(base_delay * (2 ** attempts), max_delay)
            _LOGGER.info(f"Attempting reconnect in {delay:.1f} seconds (attempt {attempts+1})")
            await asyncio.sleep(delay)
            
            try:
                # 再次尝试连接
                if await self.connect():
                    _LOGGER.info("Reconnected successfully.")
                    return # 连接成功，退出重连循环
            except Exception as e:
                _LOGGER.error(f"Reconnect attempt failed: {e}")
                
            attempts += 1
            # 可以考虑重置 attempts 或增加一个最大重试次数，但通常无限重试更符合 Home Assistant 长期运行的特性
            # 如果达到一定次数后仍然失败，可以记录更严重的错误日志
            
    async def publish(self, message, topic=None, qos=1):
        """发布 MQTT 消息"""
        publish_topic = topic or self.base_topic
        
        # 等待连接就绪
        if not self._is_connected or not self._connected_event.is_set():
            _LOGGER.warning(f"MQTT not connected for publish to {publish_topic}. Waiting for connection readiness...")
            try:
                # 尝试连接，并等待连接事件被设置（超时10秒）
                if not await self.connect(): # connect() 内部会处理重连和事件设置
                    _LOGGER.error(f"Failed to establish MQTT connection for publish to {publish_topic}.")
                    return False
            except asyncio.TimeoutError:
                _LOGGER.error(f"MQTT connection not ready within timeout for publish to {publish_topic}. Check broker status.")
                return False
            except Exception as e:
                _LOGGER.error(f"Unexpected error while waiting for MQTT connection for publish: {e}")
                return False
                
        # 再次检查连接状态，确保在等待后连接确实就绪
        if not self._is_connected or not self._connected_event.is_set():
             _LOGGER.error(f"MQTT connection is not ready after waiting for publish to {publish_topic}. Aborting publish.")
             return False

        try:
            # Paho-MQTT 的 publish 方法是线程安全的
            await self.hass_loop.run_in_executor(
                None, self.mqtt_client.publish, publish_topic, json.dumps(message), qos
            )
            _LOGGER.debug(f"Published to {publish_topic}: {message}")
            return True
        except Exception as e:
            _LOGGER.error(f"Error publishing message to {publish_topic}: {e}", exc_info=True)
            # 发布失败通常也意味着连接有问题，触发重连
            self._is_connected = False
            self._connected_event.clear()
            self.hass_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._schedule_reconnect())
            ) 
            return False
            
    def is_connected(self):
        """检查是否已连接"""
        # 更严谨的连接状态检查：同时检查 _is_connected 标志和 _connected_event
        return self._is_connected and self._connected_event.is_set()
        
    async def stop(self):
        """停止 MQTT 连接"""
        _LOGGER.info("Stopping MQTT Manager...")
        self._should_run = False
        
        # 取消重连任务
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                _LOGGER.debug("Reconnect task cancelled during stop.")
                pass
        
        # 清除事件和状态
        self._connected_event.clear()
        self._is_connected = False
                
        if self.mqtt_client:
            _LOGGER.debug("Disconnecting MQTT client.")
            try:
                # 尝试停止 Paho 客户端的循环并断开连接
                await self.hass_loop.run_in_executor(None, self.mqtt_client.loop_stop)
                await self.hass_loop.run_in_executor(None, self.mqtt_client.disconnect)
                _LOGGER.info("MQTT client stopped successfully.")
            except Exception as e:
                _LOGGER.warning(f"Error while stopping MQTT client: {e}")
            finally:
                self.mqtt_client = None
        else:
            _LOGGER.debug("MQTT client not initialized, nothing to stop.")


class DataFetcher:
    """处理 MQTT 数据并维护设备状态"""
    def __init__(self, hass, mqtt_manager, device_imei, location_key): 
        self.hass = hass
        self.location_key = location_key
        self.device_imei = [device_imei] if isinstance(device_imei, str) else device_imei
        
        self.mqtt_manager = mqtt_manager 
        self.mqtt_manager.set_message_callback(self._handle_mqtt_message) # 设置回调
        
        self.state_history = {} 
        self.deviceinfo = {}    
        self.trackerdata = {}   
        self.server_distance = 0 
        self._stopped = False
        
        self._coordinator_update_callback = None
        self._last_received_data = {}
        
        self._store = Store(
            hass, 
            version=1, 
            key=f"mqtt_gps_{slugify(location_key)}",
            private=False,
            encoder=DateTimeEncoder
        )
        self._persisted_data_loaded = False
        
        self.hass.async_create_task(self._load_persisted_data())


    def set_coordinator_update_callback(self, callback):
        """设置协调器提供的回调方法，用于推送数据。"""
        self._coordinator_update_callback = callback
        _LOGGER.debug("DataFetcher registered coordinator update callback.")

    async def _handle_mqtt_message(self, topic, payload_bytes):
        """
        处理从 MQTT 接收到的原始消息。
        这个方法在 Home Assistant 主事件循环中执行。
        """
        try:
            payload = json.loads(payload_bytes.decode())
            _LOGGER.debug("Processing MQTT message on topic %s: %s", topic, payload)

            for imei in self.device_imei:
                await self._process_single_device_data(imei, payload)


        except json.JSONDecodeError:
            _LOGGER.error(f"MQTT message payload is not valid JSON: {payload_bytes.decode()}")
        except Exception as e:
            _LOGGER.error(f"Error handling MQTT message: {e}", exc_info=True)

    async def _process_single_device_data(self, imei: str, payload: dict):
        """
        内部方法：处理单个设备的最新 MQTT 数据，更新内部状态，并通知协调器。
        """
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
            
        if imei not in self.state_history:
            self.state_history[imei] = {
                "gps": {"lat": 0.0, "lng": 0.0, "speed": 0.0, "course": 0.0, "accuracy": 0},
                "lbs": {"lat": 0.0, "lng": 0.0},
                "s": 0, "acc": 1, "adc": 0, "csq": 0, "f": 0,
                "ol": 1, "m": 0, "In1": 0,
                "t": dt_util.utcnow(), 
                "lastupdate": dt_util.utcnow(),
                "laststoptime": dt_util.as_timestamp(dt_util.utcnow()), 
                "runorstop": "停止",
                "totalkm": 0.0,
                "latitude": 0.0, 
                "longitude": 0.0, 
                "old_ol": 1 
            }
            
        for key, value in payload.items():
            if key == "gps" and isinstance(value, dict):
                for gps_key, gps_value in value.items():
                    self.state_history[imei]["gps"][gps_key] = gps_value
            elif key == "lbs" and isinstance(value, dict): 
                for lbs_key, lbs_value in value.items():
                    self.state_history[imei]["lbs"][lbs_key] = lbs_value
            elif key in ["t"]: 
                if isinstance(value, (int, float)):
                    self.state_history[imei][key] = dt_util.utc_from_timestamp(value)
                elif isinstance(value, str):
                    try:
                        self.state_history[imei][key] = dt_util.parse_datetime(value) or dt_util.utcnow()
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"Failed to parse datetime for {imei}, key {key}: {value}. Using current time.")
                        self.state_history[imei][key] = dt_util.utcnow()
                else: 
                    self.state_history[imei][key] = value
            elif value is not None:
                self.state_history[imei][key] = value
        
        longitude = float(self.state_history[imei]["gps"].get("lng", 0.0))
        latitude = float(self.state_history[imei]["gps"].get("lat", 0.0))
        speed = float(self.state_history[imei]["gps"].get("speed", 0.0)) 
        course = float(self.state_history[imei]["gps"].get("course", 0.0))

        last_recorded_lat = float(self.state_history[imei]["latitude"])
        last_recorded_lng = float(self.state_history[imei]["longitude"])
        last_recorded_runorstop = self.state_history[imei].get("lastrunorstop", "运动")
        current_runorstop = self.state_history[imei].get("runorstop", "停止")
        
        current_distance_moved = 0.0
        if last_recorded_lat != 0.0 or last_recorded_lng != 0.0:
            current_distance_moved = self.get_distance(
                latitude, longitude,
                last_recorded_lat, last_recorded_lng
            )
        
        
        if speed < MIN_SPEED_FOR_MOVEMENT and self.state_history[imei].get("s", 0) == 0:  #兼容无s参数
            self.state_history[imei]["runorstop"] = "停止"
            self.state_history[imei]["speed"] = 0.0
        elif current_distance_moved > MIN_DISTANCE_FOR_MOVEMENT and (self.state_history[imei].get("s", 1) == 1 or self.state_history[imei].get("acc", 0) == 0):  #兼容无s参数
            if current_distance_moved > MIN_DISTANCE_FOR_MOVEMENT:
                self.state_history[imei]["latitude"] = latitude 
                self.state_history[imei]["longitude"] = longitude
                self.state_history[imei]["totalkm"] += current_distance_moved / 1000
                self.state_history[imei]["speed"] = speed
                self.state_history[imei]["runorstop"] = "运动"
                _LOGGER.debug(f"Device {imei} moved {current_distance_moved:.2f}m. Total KM: {self.state_history[imei]['totalkm']:.2f}")
        
        self.state_history[imei]["server_distance"] = current_distance_moved
        
        self.state_history[imei]["course"] = course

        if last_recorded_runorstop == "运动" and current_runorstop == "停止":
            self.state_history[imei]["lastrunorstop"] = current_runorstop
            self.state_history[imei]["laststoptime"] = dt_util.as_timestamp(dt_util.utcnow())
            _LOGGER.debug(f"Device {imei} transitioned to STOPPED at {dt_util.utcnow()}")
            
        if last_recorded_runorstop == "停止" and current_runorstop == "运动":
            self.state_history[imei]["lastrunorstop"] = current_runorstop
            self.state_history[imei]["lastruntime"] = dt_util.as_timestamp(dt_util.utcnow())
            _LOGGER.debug(f"Device {imei} transitioned to RUN at {dt_util.utcnow()}")
            
        current_ol = self.state_history[imei].get("ol", 1) 
        old_ol = self.state_history[imei].get("old_ol", current_ol) 
        
        if current_ol == 1 and old_ol == 0: 
            self.state_history[imei]["lastonlinetime"] = dt_util.utcnow()
            self.state_history[imei]["old_ol"] = 1
            _LOGGER.debug(f"Device {imei} came online at {self.state_history[imei]['lastonlinetime']}")
        elif current_ol == 0 and old_ol == 1: 
            self.state_history[imei]["lastofflinetime"] = dt_util.utcnow()
            self.state_history[imei]["old_ol"] = 0
            _LOGGER.debug(f"Device {imei} went offline at {self.state_history[imei]['lastofflinetime']}")

        
        self.state_history[imei]["lastupdate"] = dt_util.utcnow()
        _LOGGER.debug(f"[{imei}] state_history updated: {self.state_history[imei]}")

        parking_time_str = ""
        if "laststoptime" in self.state_history[imei] and self.state_history[imei]["laststoptime"] is not None:
            parking_time_str = self.time_diff(self.state_history[imei]["laststoptime"])


        status = "停车"

        if self.state_history[imei].get("ol", 1) == 1: 
            onlinestatus = "在线"
        else:
            onlinestatus = "离线"
            status = "离线"
        if self.state_history[imei].get("s", 0) == 1: 
            shake = "震动"
            status = "震动"
        else: 
            shake = "静止"
        if self.state_history[imei].get("acc", 1) == 0: 
            acc = "车辆启动"
            status = "车辆启动"
        else:
            acc = "车辆熄火"
        if self.state_history[imei].get("runorstop") == "运动":
            status = "行驶"
            parking_time_str = ""
        
        if self.state_history[imei].get("f", 0) == 1:
            gps_fix = "gps已定位"
        else:
            gps_fix = "gps未定位"

        last_update = self.to_date_time_string(self.state_history[imei].get("lastupdate"))
        query_time = self.to_date_time_string(dt_util.utcnow()) 
        last_stop_time = self.to_date_time_string(self.state_history[imei].get("laststoptime"))
        last_run_time = self.to_date_time_string(self.state_history[imei].get("lastruntime"))
        lastonlinetime = self.to_date_time_string(self.state_history[imei].get("lastonlinetime"))
        lastofflinetime = self.to_date_time_string(self.state_history[imei].get("lastofflinetime"))

        new_device_data = {
            "thislon": self.state_history[imei]["longitude"],
            "thislat": self.state_history[imei]["latitude"],
            "accuracy": self.state_history[imei]["gps"].get("accuracy", 0), 
            "speed": self.state_history[imei].get("speed",0), 
            "course": self.state_history[imei]["course"],
            "status": status, 
            "imei": imei,
            "location_key": self.location_key, 
            "deviceinfo": { 
                "device_model": "MQTT GPS Tracker",
                "sw_version": "1.0",
                "tid": imei,
            },
            "attrs": { 
                "latitude": self.state_history[imei]["latitude"], 
                "longitude": self.state_history[imei]["longitude"], 
                "speed": round(self.state_history[imei].get("speed",0),2), 
                "course": self.state_history[imei]["course"],
                "lbslat": float(self.state_history[imei]["lbs"].get("lat", 0.0)),
                "lbslng": float(self.state_history[imei]["lbs"].get("lng", 0.0)),
                "lbsmap": f"http://apis.map.qq.com/uri/v1/marker?coord_type=1&marker=title:+;coord:{self.state_history[imei]['lbs'].get('lat', 0.0)},{self.state_history[imei]['lbs'].get('lng', 0.0)}",
                "acc": acc, 
                "powbatteryvoltage": float(self.state_history[imei].get("adc", 0)) / 1000,
                "csq": int(self.state_history[imei].get("csq", 0)),
                "status": status,
                "shake": shake,
                "In1": self.state_history[imei].get("In1", 0),
                "gpsisfix": gps_fix,
                "onlinestatus": onlinestatus,
                "parkingtime": parking_time_str,
                "laststoptime": last_stop_time,
                "lastruntime": last_run_time,
                "lastonlinetime": lastonlinetime,
                "lastofflinetime": lastofflinetime,
                "last_update": last_update,
                "querytime": query_time,
                "distance": self.state_history[imei].get("m", 0), 
                "serverdistance": self.state_history[imei].get("server_distance", 0.0), 
                "totalKm": round(self.state_history[imei].get("totalkm", 0.0),2), 
                "runorstop": self.state_history[imei].get("runorstop")
            }
        }

        if self._coordinator_update_callback:
            _LOGGER.debug(f"DataFetcher pushing immediate update for {imei} to coordinator.")
            await self._coordinator_update_callback(imei, new_device_data)
        else:
            _LOGGER.warning(f"No coordinator update callback registered for DataFetcher when handling push for {imei}.")

        await self._persist_data()


    async def get_data(self):
        """
        """
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
            
        self.trackerdata = {} 
        for imei in self.device_imei:
            device_state = self.state_history.get(imei)
            if not device_state:
                _LOGGER.debug("No state data for device: %s in get_data, returning empty data for it.", imei)
                self.trackerdata[imei] = {
                    "location_key": f"{self.location_key}",
                    "imei": imei,
                    "thislat": 0.0,
                    "thislon": 0.0,
                    "accuracy": 0,
                    "source_type": "gps",
                    "status": "未知/离线",
                    "deviceinfo": {
                        "device_model": "MQTT GPS Tracker",
                        "sw_version": "1.0",
                        "tid": imei,
                    },
                    "attrs": { 
                        "latitude": 0.0,
                        "longitude": 0.0,
                        "speed": 0,
                        "course": 0,
                        "lbslat": 0.0,
                        "lbslng": 0.0,
                        "lbsmap": "",
                        "acc": "未知",
                        "powbatteryvoltage": 0.0,
                        "csq": 0,
                        "status": "未知/离线",
                        "shake": "未知",
                        "In1": 0,
                        "gpsisfix": "未知",
                        "onlinestatus": "在线",
                        "parkingtime": "未知",
                        "laststoptime": "未知",
                        "lastonlinetime": "未知",
                        "lastofflinetime": "未知",
                        "last_update": self.to_date_time_string(dt_util.utcnow()),
                        "querytime": self.to_date_time_string(dt_util.utcnow()),
                        "distance": 0,
                        "serverdistance": 0.0,
                        "totalKm": 0.0,
                        "runorstop": "未知"
                    }
                }
                continue
                
            gps_data = self.state_history.get(imei, {})
            thislat = float(gps_data.get("latitude", 0.0))
            thislon = float(gps_data.get("longitude", 0.0))
            speed = float(gps_data.get("speed", 0.0)) 
            course = float(gps_data.get("course", 0.0))
            runorstop = gps_data.get("runorstop")

            parking_time_str = ""
            if "laststoptime" in self.state_history[imei] and self.state_history[imei]["laststoptime"] is not None:
                parking_time_str = self.time_diff(self.state_history[imei]["laststoptime"])

            status = "停车"

            if self.state_history[imei].get("ol", 1) == 1: 
                onlinestatus = "在线"
            else:
                onlinestatus = "离线"
                status = "离线"
            if self.state_history[imei].get("s", 0) == 1: 
                shake = "震动"
                status = "震动"
            else: 
                shake = "静止"
            if self.state_history[imei].get("acc", 1) == 0: 
                acc = "车辆启动"
                status = "车辆启动"
            else:
                acc = "车辆熄火"
            if runorstop == "运动":
                status = "行驶"
                parking_time_str = ""
            
            if self.state_history[imei].get("f", 0) == 1:
                gps_fix = "gps已定位"
            else:
                gps_fix = "gps未定位"

            last_update = self.to_date_time_string(self.state_history[imei].get("lastupdate"))
            query_time = self.to_date_time_string(dt_util.utcnow()) 
            last_stop_time = self.to_date_time_string(self.state_history[imei].get("laststoptime"))
            last_run_time = self.to_date_time_string(self.state_history[imei].get("lastruntime"))
            lastonlinetime = self.to_date_time_string(self.state_history[imei].get("lastonlinetime"))
            lastofflinetime = self.to_date_time_string(self.state_history[imei].get("lastofflinetime"))

            self.trackerdata[imei] = {
                "location_key": f"{self.location_key}",
                "deviceinfo": {
                    "device_model": "MQTT GPS Tracker",
                    "sw_version": "1.0",
                    "tid": imei,
                },
                "thislat": self.state_history[imei]["latitude"], 
                "thislon": self.state_history[imei]["longitude"], 
                "accuracy": self.state_history[imei]["gps"].get("accuracy", 0), 
                "speed": speed, 
                "course": course,
                "status": status, 
                "source_type": "gps",
                "imei": imei,
                "attrs": { 
                    "latitude": self.state_history[imei]["latitude"], 
                    "longitude": self.state_history[imei]["longitude"], 
                    "speed": speed, 
                    "course": course,
                    "lbslat": float(self.state_history[imei]["lbs"].get("lat", 0.0)),
                    "lbslng": float(self.state_history[imei]["lbs"].get("lng", 0.0)),
                    "lbsmap": f"http://apis.map.qq.com/uri/v1/marker?coord_type=1&marker=title:+;coord:{self.state_history[imei]['lbs'].get('lat', 0.0)},{self.state_history[imei]['lbs'].get('lng', 0.0)}",
                    "acc": acc, 
                    "powbatteryvoltage": float(self.state_history[imei].get("adc", 0)) / 1000,
                    "csq": int(self.state_history[imei].get("csq", 0)),
                    "status": status,
                    "shake": shake,
                    "In1": self.state_history[imei].get("In1", 0),
                    "gpsisfix": gps_fix,
                    "onlinestatus": onlinestatus,
                    "parkingtime": parking_time_str,
                    "laststoptime": last_stop_time,
                    "lastruntime": last_run_time,
                    "lastonlinetime": lastonlinetime,
                    "lastofflinetime": lastofflinetime,
                    "last_update": last_update,
                    "querytime": query_time,
                    "distance": self.state_history[imei].get("m", 0), 
                    "serverdistance": self.state_history[imei].get("server_distance", 0.0), 
                    "totalKm": round(self.state_history[imei].get("totalkm", 0.0),2), 
                    "runorstop": runorstop
                }
            }
        return self.trackerdata

    async def _load_persisted_data(self):
        """异步加载持久化数据"""
        if self._persisted_data_loaded: 
            return
        try:
            persisted_data = await self._store.async_load() or {}
            self.state_history = persisted_data.get("state_history", {})
            
            for imei, state in self.state_history.items():
                for time_field in ["t", "lastupdate", "lastonlinetime", "lastofflinetime"]:
                    if time_field in state and isinstance(state[time_field], str):
                        try:
                            state[time_field] = datetime.datetime.fromisoformat(state[time_field])
                            if state[time_field].tzinfo is None: 
                                state[time_field] = state[time_field].replace(tzinfo=datetime.timezone.utc)
                        except (ValueError, TypeError):
                            _LOGGER.warning(f"Failed to parse persisted datetime for {imei}, field {time_field}: {state[time_field]}. Using current UTC time.")
                            state[time_field] = dt_util.utcnow()
                if "laststoptime" not in state or state["laststoptime"] is None:
                    state["laststoptime"] = dt_util.as_timestamp(dt_util.utcnow())
                state["latitude"] = float(state.get("latitude", 0.0))
                state["longitude"] = float(state.get("longitude", 0.0))
                state.setdefault("old_ol", state.get("ol", 1))

            _LOGGER.debug("Loaded and converted persisted data: %s", self.state_history)
            self._persisted_data_loaded = True
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
        """计算时间差 (Home Assistant 推荐使用 timedelta)"""
        if isinstance(timestamp, (int, float)):
            dt = dt_util.utc_from_timestamp(timestamp)
        elif isinstance(timestamp, datetime.datetime):
            dt = timestamp
        else:
            return "未知"
            
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
            
        now_utc = dt_util.utcnow()
        result = now_utc - dt
        
        if result.total_seconds() < 0:
            return "未来时间"

        total_seconds = int(result.total_seconds())
        days, remainder = divmod(total_seconds, 86400) 
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if seconds > 0 or not parts: 
            parts.append(f"{seconds}秒")
            
        return "".join(parts)

    def to_date_time_string(self, dt):
        """将 datetime 对象或时间戳转换为字符串格式：YYYY-MM-DD HH:MM:SS"""
        if dt is None:
            return "N/A"
        if isinstance(dt, datetime.datetime):
            return dt_util.as_local(dt).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(dt, (int, float)):
            return dt_util.as_local(dt_util.utc_from_timestamp(dt)).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(dt, str):
            try: 
                parsed_dt = dt_util.parse_datetime(dt)
                if parsed_dt:
                    return dt_util.as_local(parsed_dt).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass 
            return dt 
        return "未知时间"
           
class DataButton:
    """通过 MQTT 发布消息执行按钮动作"""

    # 接收 mqtt_manager 实例
    def __init__(self, hass, username, password, device_imei, mqtt_manager): 
        self.hass = hass
        self.device_imei = device_imei
        self.mqtt_manager = mqtt_manager # 直接使用传入的 manager
        
    async def _do_action(self, action):
        """通过 MQTT 发布动作消息"""
        message = action
        # 确保 mqtt_manager 已连接
        if not self.mqtt_manager.is_connected():
            _LOGGER.warning("MQTT Manager not connected for button action, attempting to reconnect...")
            if not await self.mqtt_manager.connect():
                _LOGGER.error("Failed to reconnect MQTT for button action.")
                return False
                
        command_topic = self.mqtt_manager.command_topic
        _LOGGER.debug("[%s] mqtt_manager.publish: %s ,topic: %s", self.device_imei, message, command_topic)
        return await self.mqtt_manager.publish(message, topic=command_topic)

    async def _action(self, action):
        """执行按钮动作"""
        success = await self._do_action(action)
        state = dt_util.now().strftime("%Y-%m-%d %H:%M:%S") 
        return state if success else "执行失败"

class DataSwitch:

    # 接收 mqtt_manager 实例
    def __init__(self, hass, username, password, device_imei, mqtt_manager):
        self.hass = hass
        self.device_imei = device_imei
        self.mqtt_manager = mqtt_manager # 直接使用传入的 manager
        
    async def _do_action(self, action):
        """通过 MQTT 发布动作消息"""
        message = action
        # 确保 mqtt_manager 已连接
        if not self.mqtt_manager.is_connected():
            _LOGGER.warning("MQTT Manager not connected for switch action, attempting to reconnect...")
            if not await self.mqtt_manager.connect():
                _LOGGER.error("Failed to reconnect MQTT for switch action.")
                return False
        
        command_topic = self.mqtt_manager.command_topic
        _LOGGER.debug("[%s] mqtt_manager.publish: %s ,topic: %s", self.device_imei, message, command_topic)
        return await self.mqtt_manager.publish(message, topic=command_topic)
        
    async def _turn_on(self, action): 
        if action == "open_lock": 
            success = await self._do_action({"cmd":"on1"}) 
            state = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
            return state if success else "执行失败"
            
    async def _turn_off(self, action): 
        if action == "open_lock": 
            success = await self._do_action({"cmd":"off1"}) 
            state = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
            return state if success else "执行失败"