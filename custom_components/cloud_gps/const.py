
"""Constants for cloud gps."""
DOMAIN = "cloud_gps"

REQUIRED_FILES = [
    "const.py",
    "manifest.json",
    "device_tracker.py",
    "config_flow.py",
    "translations/en.json",
    "translations/zh-Hans.json",
]
VERSION = "2025.6.27"
ISSUE_URL = "https://github.com/dscao/cloud_gps/issues"

STARTUP = """
-------------------------------------------------------------------
{name}
Version: {version}
This is a custom component
If you have any issues with this you need to open an issue here:
{issueurl}
-------------------------------------------------------------------
"""

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
)

ATTR_ICON = "icon"
ATTR_LABEL = "label"
MANUFACTURER = "云平台"
NAME = "云平台GPS"

CONF_WEB_HOST = "webhost"

CONF_DEVICES = "devices"
CONF_DEVICE_IMEI = "device_imei"
CONF_GPS_CONVER = "gps_conver"
CONF_ATTR_SHOW = "attr_show"
CONF_UPDATE_INTERVAL = "update_interval_seconds"
CONF_SENSORS = "sensors"
CONF_SWITCHS = "switchs"
CONF_BUTTONS = "buttons"
CONF_MAP_GCJ_LAT = "map_gcj_lat"
CONF_MAP_GCJ_LNG = "map_gcj_lng"
CONF_MAP_BD_LAT = "map_bd_lat"
CONF_MAP_BD_LNG = "map_bd_lng"
CONF_UPDATE_ADDRESSDISTANCE = "address_distance"
CONF_ADDRESSAPI = "addressapi"
CONF_ADDRESSAPI_KEY = "api_key"
CONF_PRIVATE_KEY = "private_key"
CONF_WITH_MAP_CARD = "with_map_card"

COORDINATOR = "coordinator"
UNDO_UPDATE_LISTENER = "undo_update_listener"

MQTT_MANAGER = "mqtt_manager"

PWD_NOT_CHANGED = "__**password_not_changed**__"

KEY_ADDRESS = "address"
KEY_QUERYTIME = "querytime"
KEY_PARKING_TIME = "parkingtime"
KEY_LASTSTOPTIME = "laststoptime"
KEY_LASTRUNTIME = "lastruntime"
KEY_LASTSEEN = "lastseen"
KEY_SPEED = "speed"
KEY_TOTALKM = "totalkm"
KEY_STATUS = "status"
KEY_ACC = "acc"
KEY_RUNORSTOP = "runorstop"
KEY_SHAKE = "shake"
KEY_BATTERY = "powbattery"
KEY_BATTERY_STATUS = "battery_status"

