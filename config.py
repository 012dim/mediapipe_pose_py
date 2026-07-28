"""配置文件:集中管理所有可调参数。

修改本文件即可调整摄像头、MediaPipe、动作识别、串口等所有参数,
无需改动业务代码。
"""
import platform


# ============ 摄像头配置 ============
CAMERA_ID: int = 0                  # 默认摄像头 ID
CAMERA_WIDTH: int = 640             # 采集分辨率宽
CAMERA_HEIGHT: int = 480            # 采集分辨率高
CAMERA_FPS: int = 30                # 目标帧率
AVAILABLE_CAMERA_IDS: list = [0, 1, 2]  # 可循环切换的摄像头 ID 列表


# ============ MediaPipe Pose 配置 ============
STATIC_IMAGE_MODE: bool = False     # False=视频流模式(更高效)
MODEL_COMPLEXITY: int = 1           # 0 轻量 / 1 中等 / 2 最准
SMOOTH_LANDMARKS: bool = True       # 减少关键点抖动
MIN_DETECTION_CONFIDENCE: float = 0.5
MIN_TRACKING_CONFIDENCE: float = 0.5


# ============ 关键点平滑滤波 ============
# 用 deque 缓存最近 N 帧坐标取平均,减少抖动
SMOOTH_BUFFER_SIZE: int = 5


# ============ 动作识别阈值 ============
HAND_UP_THRESHOLD: float = 0.05     # 手腕高于鼻子的偏移量(归一化)
KNEE_ANGLE_STAND: float = 160.0     # 膝关节角度 > 此值判定为站立
KNEE_ANGLE_SIT: float = 130.0       # 膝关节角度 < 此值判定为坐下
FALL_RATIO_THRESHOLD: float = 0.3   # 髋肩高度差 / 肩宽 < 此值判定为跌倒


# ============ 动作显示 / 冷却 ============
ACTION_DISPLAY_DURATION: float = 2.0  # 动作在屏幕显示时长(秒)
ACTION_COOLDOWN: float = 1.0           # 同一动作冷却时间(秒),避免重复触发
MAX_RECENT_ACTIONS: int = 3            # 屏幕底部最多显示的动作数量


# ============ 串口配置(可选,默认关闭) ============
SERIAL_ENABLED: bool = False
SERIAL_BAUDRATE: int = 9600
SERIAL_TIMEOUT: float = 1.0
SERIAL_INTERVAL: float = 0.2    # 串口定时发送间隔(秒)
SERIAL_SEND_NONE: bool = True   # 无动作时是否发送 NONE

# 各平台默认串口名
DEFAULT_SERIAL_PORT_WIN: str = "COM3"
DEFAULT_SERIAL_PORT_LINUX: str = "/dev/ttyUSB0"
DEFAULT_SERIAL_PORT_MAC: str = "/dev/tty.usbserial*"


def get_default_serial_port() -> str:
    """根据当前操作系统返回默认串口端口名。

    Returns:
        str: 串口路径,例如 Windows 返回 'COM3'。
    """
    system = platform.system()
    if system == "Windows":
        return DEFAULT_SERIAL_PORT_WIN
    elif system == "Linux":
        return DEFAULT_SERIAL_PORT_LINUX
    else:
        return DEFAULT_SERIAL_PORT_MAC


# ============ 路径配置 ============
SCREENSHOT_DIR: str = "screenshots"


# ============ 显示配置 ============
SHOW_SKELETON_DEFAULT: bool = True  # 启动时是否显示骨骼


# ============ 日志配置 ============
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
