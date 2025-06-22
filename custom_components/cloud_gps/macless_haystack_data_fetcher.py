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
import hashlib
import base64
from async_timeout import timeout
from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import math
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import struct
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify
from zoneinfo import ZoneInfo


# 预定义时区
SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')

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

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)
        
class DataFetcher:
    """fetch the cloud gps data"""

    def __init__(self, hass, username, password, device_imei, location_key):
        self.hass = hass
        self.location_key = location_key
        self.username = username
        self.password = password
        self.device_imei = device_imei        
        self.userid = None
        self.usertype = None
        self.deviceinfo = {}
        self.trackerdata = {}
        self.lastseentime = 0
        self._refresh_time = 0
        self.all_device_configs = []
        try:
            jsontext = json.loads(self.password)
        except json.JSONDecodeError as e:
            _LOGGER.error("Failed to parse self.password JSON: %s", e)
            
        # 处理设备配置
        device = jsontext[0]
        for device in jsontext:
            if "hashedAdvKey" not in device or "additionalHashedAdvKeys" not in device:
                # 计算主私钥的 hashedAdvKey
                main_private_key = device["privateKey"]
                device["hashedAdvKey"] = self.calculate_hashed_adv_key(main_private_key)

                # 计算 additionalHashedAdvKeys
                additional_hashed_keys = []
                for priv_key in device["additionalKeys"]:
                    hashed_key = self.calculate_hashed_adv_key(priv_key)
                    if hashed_key:
                        additional_hashed_keys.append(hashed_key)
                device["additionalHashedAdvKeys"] = additional_hashed_keys

            self.all_device_configs.append(device)
        _LOGGER.debug("all_device_configs: %s", self.all_device_configs)

        # 使用自定义编码器的存储
        self._store = Store(
            hass, 
            version=1, 
            key=f"cloud_gps_{slugify(location_key)}",
            private=False,
            encoder=DateTimeEncoder  # 使用自定义编码器
        )

        # 使用简单标志而不是立即加载
        self._persisted_data_loaded = False

        

    def _get_devices_info(self):        
        url = str.format(self.username.split("||")[0])
        headers = {}
        if self.username.split("||")[1] != "0":
            auth_header = self.basic_auth(self.username.split('||')[1], self.username.split('||')[2])
            headers = {"authorization": auth_header}
        
        p_data = self.json_format_data(self.all_device_configs[0])
        #_LOGGER.debug("payload: %s", p_data)
        resp = requests.post(url, headers=headers, json=p_data).json()
        _LOGGER.debug("resp_json: %s", resp)
        return resp
        
    def basic_auth(self, username, password):
        userpass = f"{username}:{password}"
        encoded_credentials = base64.b64encode(userpass.encode('utf-8')).decode('utf-8')
        return "Basic " + encoded_credentials
    
    def json_format_data(self, data):
        accessory_id = data.get("id")
        ids = data.get("additionalHashedAdvKeys")
        formatted_data = {
            "accessoryId": accessory_id,
            "ids": ids,
            "days": 1
        }
        return formatted_data
    
    def sha256(self, data):
        """SHA256实现，与Dart代码中的_kdf匹配"""
        digest = hashlib.sha256()
        digest.update(data)
        return digest.digest()
        
    def calculate_hashed_adv_key(self, private_key_b64):
        """计算私钥对应的 hashedAdvKey"""
        try:
            priv_bytes = base64.b64decode(private_key_b64)
            priv_int = int.from_bytes(priv_bytes, 'big')
            private_key = ec.derive_private_key(
                priv_int, 
                ec.SECP224R1(),
                default_backend()
            )
            public_key = private_key.public_key()
            x_coord = public_key.public_numbers().x
            x_bytes = x_coord.to_bytes(28, 'big')
            hashed = hashlib.sha256(x_bytes).digest()
            return base64.b64encode(hashed).decode('ascii')
        except Exception as e:
            print(f"Error processing key: {str(e)}")
            return None

    def decode_tag(self, decrypted_data):
        """解码标签数据，与Dart代码中的_decodePayload匹配"""
        # 使用struct.unpack获取32位无符号整数
        latitude = struct.unpack(">I", decrypted_data[0:4])[0] / 10000000.0
        longitude = struct.unpack(">I", decrypted_data[4:8])[0] / 10000000.0
        
        # 准确度和状态直接从字节中提取
        accuracy = decrypted_data[8]
        status = decrypted_data[9]
        
        # 电池状态解析
        battery_status = "unknown"
        # 检查是否支持电池状态更新
        # if status & 0x20 != 0:
        battery_bits = (status >> 6) & 0x3
        if battery_bits == 0:
            battery_status = "ok"
        elif battery_bits == 1:
            battery_status = "medium"
        elif battery_bits == 2:
            battery_status = "low"
        elif battery_bits == 3:
            battery_status = "criticalLow"
        
        return {
            'lat': latitude,
            'lon': longitude,
            'accuracy': accuracy,
            'status': status,
            'battery_status': battery_status
        }

    def decrypt_payload(self, encrypted_payload, private_key_b64):
        try:

            key_bytes = base64.b64decode(private_key_b64)
            priv_int = int.from_bytes(key_bytes, byteorder='big', signed=False)
            data = base64.b64decode(encrypted_payload)
            
            # Dart逻辑：如果长度 > 88，则移除第4个字节
            if len(data) > 88:
                _LOGGER.debug("Payload > 88 bytes, removing byte at index 4")
                modified_data = bytearray()
                modified_data.extend(data[0:4])
                modified_data.extend(data[5:])
                data = bytes(modified_data)
            
            # 提取时间戳和置信度
            timestamp_seconds = int.from_bytes(data[0:4], 'big')
            confidence = data[4]
            timestamp = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc) + \
                        datetime.timedelta(seconds=timestamp_seconds)
            _LOGGER.debug("Timestamp: %s, Confidence: %d", timestamp, confidence)

            # 提取密钥材料
            ephemeral_key_bytes = data[5:62]  # 临时公钥 (57字节)
            enc_data = data[62:72]  # 加密数据 (10字节)
            auth_tag = data[72:88]  # 认证标签 (16字节)
            
            # 创建密钥对象
            curve = ec.SECP224R1()
            private_key = ec.derive_private_key(priv_int, curve, default_backend())
            public_key = ec.EllipticCurvePublicKey.from_encoded_point(curve, ephemeral_key_bytes)
            
            # 使用 cryptography 的 ECDH 交换
            shared_key = private_key.exchange(ec.ECDH(), public_key)
            
            # 确保共享密钥为28字节 (secp224r1要求)
            if len(shared_key) < 28:
                # 前面补零
                shared_key = b'\x00' * (28 - len(shared_key)) + shared_key
            elif len(shared_key) > 28:
                # 截断到28字节
                shared_key = shared_key[:28]
            
            # KDF (密钥派生) - 与Dart的_kdf函数匹配
            counter = 1
            counter_bytes = counter.to_bytes(4, 'big')
            kdf_input = shared_key + counter_bytes + ephemeral_key_bytes
            symmetric_key = self.sha256(kdf_input)

            
            # 分离解密密钥和IV
            decryption_key = symmetric_key[:16]  # 16字节的解密密钥
            iv = symmetric_key[16:32]  # 16字节的初始化向量

            # 使用AES-GCM解密 - 匹配Dart的_decryptPayload函数
            # 注意：Dart代码中GCM模式使用tag作为附加认证数据
            cipher = Cipher(
                algorithms.AES(decryption_key),
                modes.GCM(iv, auth_tag, 16),  # 明确指定标签长度为16字节
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            # 解密数据
            decrypted = decryptor.update(enc_data) + decryptor.finalize()
            _LOGGER.debug("Decrypted data: %s", decrypted.hex())
            
            # 解析标签数据
            tag = self.decode_tag(decrypted)
            tag['timestamp'] = timestamp
            tag['confidence'] = confidence
            tag['isodatetime'] = timestamp.isoformat()

            return tag
            
        except Exception as e:
            _LOGGER.error("Decryption failed: %s", str(e), exc_info=True)
            return None

    def _process_reports_for_device(self, imei, reports, key_map):
        """处理设备报告的解密操作"""
        all_decrypted_data = []
        
        for report in reports:
            report_hashed_adv_key = report["id"]
            report_payload_b64 = report.get("payload")
            
            if not report_payload_b64:
                continue
                
            correct_private_key_b64 = key_map.get(report_hashed_adv_key)
            if not correct_private_key_b64:
                continue
                
            try:
                # 添加性能监控点
                start_time = time.time()
                
                # 跳过时间戳检查
                decrypted_data = self.decrypt_payload(
                    report_payload_b64, 
                    correct_private_key_b64
                )
                
                if decrypted_data:
                    # 添加报告时间用于后续处理
                    decrypted_data['report_time'] = report.get("datePublished")
                    all_decrypted_data.append(decrypted_data)
                    
                    # 记录解密耗时
                    duration = time.time() - start_time
                    _LOGGER.debug("Device %s: Decrypted report in %.3fs", imei, duration)
                    
                    # 如果找到足够新的数据，提前停止
                    if decrypted_data['timestamp'].timestamp() > self.lastseentime + 3600:  # 1小时内的新数据
                        _LOGGER.debug("Device %s: Found sufficiently new data, skipping further reports", imei)
                        break
                        
                else:
                    _LOGGER.debug("Device %s: Decryption failed for report", imei)
                    
            except Exception as e:
                _LOGGER.error("Device %s: Decryption error: %s", imei, repr(e))
        
        return all_decrypted_data
    
    async def _load_persisted_data(self):
        """异步加载持久化数据"""
        try:
            self._persisted_data = await self._store.async_load() or {}
            _LOGGER.debug("%s Loaded persisted data: %s", self.device_imei, self._persisted_data)
            
            # 清理无效设备数据
            self._clean_invalid_devices()
        except Exception as e:
            _LOGGER.error("%s Error loading persisted data: %s", self.device_imei, e)
            self._persisted_data = {}
    
    async def _persist_data(self):
        """异步保存数据到持久化存储"""
        try:
            # 准备要保存的数据，确保所有 datetime 对象都被转换为字符串
            data_to_save = {
                "trackerdata": self._clean_data_for_storage(self.trackerdata),
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            await self._store.async_save(data_to_save)
            _LOGGER.debug("%s Persisted data saved", self.device_imei)
        except Exception as e:
            _LOGGER.error("%s Error saving persisted data: %s", self.device_imei, e)
    
    def _clean_data_for_storage(self, data):
        """递归清理数据，确保所有 datetime 对象被转换为字符串"""
        if isinstance(data, dict):
            return {k: self._clean_data_for_storage(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._clean_data_for_storage(item) for item in data]
        elif hasattr(data, 'isoformat') and callable(data.isoformat):
            # 鸭子类型检查：任何有 isoformat 方法的对象都视为日期时间
            return data.isoformat()
        return data
    
    def _clean_invalid_devices(self):
        """清理无效设备数据"""
        if "data" not in self._persisted_data:
            return
            
        # 创建有效设备列表
        valid_devices = {}
        
        for imei, device_data in self._persisted_data["trackerdata"].items():
            # 检查设备数据是否有效
            if self._is_device_data_valid(device_data):
                valid_devices[imei] = device_data
            else:
                _LOGGER.warning("Removing invalid device data for IMEI: %s", imei)
        
        self._persisted_data["trackerdata"] = valid_devices
    
    def _is_device_data_valid(self, device_data):
        """检查设备数据是否有效"""
        # 基本检查
        if not isinstance(device_data, dict):
            return False
        
        # 必须有位置键
        if "location_key" not in device_data:
            return False
            
        # 必须有坐标
        if "thislon" not in device_data or "thislat" not in device_data:
            return False
            
        # 坐标必须是数字
        try:
            float(device_data["thislon"])
            float(device_data["thislat"])
        except (TypeError, ValueError):
            return False
            
        return True
        
    async def get_data(self): 
        
        # 延迟加载持久化数据（仅在第一次更新时）
        if not self._persisted_data_loaded:
            await self._load_persisted_data()
            self._persisted_data_loaded = True
            
        if (int(datetime.datetime.now().timestamp()) - int(self._refresh_time)) >= 60: #限制最快1分钟才请求一次
            devicesinfodata = None
            try:
                async with timeout(60): 
                    devicesinfodata = await self.hass.async_add_executor_job(self._get_devices_info)
                    
            except Exception as e:
                #_LOGGER.error("%s Failed to get data from macless_haystack: %s", self.device_imei, repr(e))
                for imei in self.device_imei:
                    # 如果没有数据，尝试使用持久化数据
                    _LOGGER.warning("%s No new data available, using persisted data", imei)
                    self.trackerdata[imei] = self._persisted_data.get("trackerdata", {}).get(imei, {})
                    if not self.trackerdata[imei]:
                        _LOGGER.warning("%s No new data available and no persisted data", imei)
                        _LOGGER.warning("请将此%s_devices.json在web端或app端导入测试成功后再加入", imei)
                    continue

                
            self._refresh_time = int(datetime.datetime.now().timestamp())
            
            if devicesinfodata:
                querytime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                all_device_configs = self.all_device_configs
 
                for imei in self.device_imei:
                    _LOGGER.debug("Processing device with ID (imei): %s", imei)

                    if self.trackerdata.get(imei):
                        self.trackerdata[imei]["attrs"]["querytime"] = querytime
                    
                    # 找到当前 imei 对应的设备配置
                    target_device_config = next((d_config for d_config in all_device_configs if str(d_config.get("id")) == str(imei)), None)

                    if not target_device_config:
                        _LOGGER.debug("No device configuration found in JSON for ID (imei): %s", imei)
                        continue

                    # 为该设备构建一个 HashedAdvKey -> PrivateKey 的完整映射
                    key_map = {}
                    
                    # 添加主密钥对
                    if "hashedAdvKey" in target_device_config and "privateKey" in target_device_config:
                        main_hashed_key = target_device_config["hashedAdvKey"]
                        main_private_key = target_device_config["privateKey"]
                        
                    if main_hashed_key and main_private_key:  # 确保值不为空
                        key_map[main_hashed_key] = main_private_key

                    # 处理辅助密钥
                    additional_private_keys = target_device_config.get("additionalKeys", [])
                    additional_hashed_keys = target_device_config["additionalHashedAdvKeys"]

                    if len(additional_hashed_keys) == len(additional_private_keys):
                        for i in range(len(additional_hashed_keys)):
                            h_key = additional_hashed_keys[i]
                            p_key = additional_private_keys[i]
                            if h_key and p_key: #确保值不为空
                                key_map[h_key] = p_key
                                #_LOGGER.debug("Device %s: Added additional key mapping for HashedAdvKey: %s", imei, h_key)
                    else:
                        _LOGGER.warning("Device %s: Mismatch in lengths of 'additionalHashedAdvKeys' (%d) and 'additionalKeys' (%d). Skipping additional keys mapping.", 
                                        imei, len(additional_hashed_keys), len(additional_private_keys))

                    if not key_map:
                        _LOGGER.debug("Device %s: No usable keys (main or additional) found after building map.", imei)
                        continue
                    
                    # 从 devicesinfodata["results"] 中筛选出与当前设备任何已知 HashedAdvKey 匹配的报告
                    if devicesinfodata is None or "results" not in devicesinfodata or not isinstance(devicesinfodata["results"], list):
                        _LOGGER.error("devicesinfodata is missing or invalid")
                        continue 

                    matched_reports_for_this_device = []
                    for report in devicesinfodata["results"]:
                        if isinstance(report, dict) and report.get("id") in key_map:
                            matched_reports_for_this_device.append(report)

                    if not matched_reports_for_this_device:
                        _LOGGER.debug("Device %s: No matching reports", imei)
                        # 如果没有新数据，尝试使用持久化数据
                        _LOGGER.warning("%s No new data available, using persisted data", imei)
                        self.trackerdata[imei] = self._persisted_data.get("trackerdata", {}).get(imei, {})
                        if not self.trackerdata[imei]:
                            _LOGGER.warning("%s No new data available and no persisted data", imei)
                            _LOGGER.warning("请将此%s_devices.json在web端或app端导入测试成功后再加入", imei)
                        continue

                    # 按报告时间降序排序，优先处理最新报告
                    matched_reports_for_this_device.sort(key=lambda x: x.get("datePublished", 0), reverse=True)
                    
                    # 限制最大解密报告数量
                    MAX_REPORTS_PER_DEVICE = 5  # 每台设备最多解密5份报告
                    if len(matched_reports_for_this_device) > MAX_REPORTS_PER_DEVICE:
                        _LOGGER.debug("Device %s: Limiting reports from %d to %d", 
                                     imei, len(matched_reports_for_this_device), MAX_REPORTS_PER_DEVICE)
                        matched_reports_for_this_device = matched_reports_for_this_device[:MAX_REPORTS_PER_DEVICE]
                    
                    # 添加超时保护
                    try:
                        # 使用异步执行避免阻塞主线程
                        all_decrypted_data = await self.hass.async_add_executor_job(
                            self._process_reports_for_device, 
                            imei, 
                            matched_reports_for_this_device, 
                            key_map
                        )
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Device %s: Report processing timed out", imei)
                        continue
                    except Exception as e:
                        _LOGGER.error("Device %s: Error processing reports: %s", imei, repr(e))
                        continue
                    
                    # 如果没有解密出任何数据，继续下一个设备
                    if not all_decrypted_data:
                        _LOGGER.debug("Device %s: No reports successfully decrypted.", imei)
                        continue
                        
                    # 找出时间戳最新的解密数据
                    # 注意：decrypted_data['timestamp'] 是 datetime 对象
                    latest_decrypted = max(all_decrypted_data, key=lambda x: x['timestamp'])
                    
                    # 检查这个最新时间戳是否比上次记录的时间更新
                    if int(latest_decrypted['timestamp'].timestamp()) > self.lastseentime or self.trackerdata.get(imei)==None:
                        _LOGGER.debug("Device %s: Using latest decrypted data with isodatetime: %s", imei, latest_decrypted['isodatetime'])
                        
                        # 更新 lastseentime
                        self.lastseentime = int(latest_decrypted['timestamp'].timestamp())

                        # 处理解密数据
                        lastseen = latest_decrypted.get('isodatetime')            
                        thislat = latest_decrypted.get('lat')
                        thislon = latest_decrypted.get('lon')
                        accuracy = latest_decrypted.get('accuracy', 0)
                        battery_status = latest_decrypted.get('battery_status')
                        status = latest_decrypted.get('status')
                        
                        self.trackerdata[imei] = {}
                        self.deviceinfo[imei] = latest_decrypted
                        self.deviceinfo[imei]["device_model"] = "macless_haystack"
                        self.deviceinfo[imei]["sw_version"] = "1.0"

                        attrs ={
                            "querytime": querytime,
                            "lastseen": datetime.datetime.fromisoformat(lastseen).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),
                            "latest_report_time": datetime.datetime.fromtimestamp(int(latest_decrypted['report_time'])/1000).strftime("%Y-%m-%d %H:%M:%S"),
                            "battery_status": battery_status,
                        }
                                            
                        self.trackerdata[imei] = {"location_key":self.location_key+imei,"deviceinfo":self.deviceinfo[imei],"thislat":thislat,"thislon":thislon,"accuracy":accuracy,"source_type":"bluetooth_le","imei":imei,"status":status,"attrs":attrs}
                        await self._persist_data()
                    else:
                        _LOGGER.debug("Device %s: Latest decrypted data is not newer than existing data. Latest timestamp: %s, Last seen: %s", 
                                      imei, latest_decrypted['isodatetime'], datetime.datetime.fromtimestamp(self.lastseentime).isoformat())
                
        else:
            _LOGGER.debug("时间小于1分钟，不请求数据")
            
        return self.trackerdata

        
class GetDataError(Exception):
    """request error or response data is unexpected"""                
            
            
            
