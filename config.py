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
# 置信度阈值:提高到 0.7/0.6 以降低对椅子/衣架/海报等杂物的误检
MIN_DETECTION_CONFIDENCE: float = 0.7
MIN_TRACKING_CONFIDENCE: float = 0.6


# ============ 关键点平滑滤波 ============
# 用 deque 缓存最近 N 帧坐标取平均,减少抖动
SMOOTH_BUFFER_SIZE: int = 5


# ============ 动作识别阈值 ============
HAND_UP_THRESHOLD: float = 0.05     # 手腕高于鼻子的偏移量(归一化)


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


# ============ Arduino 交互流程配置 ============
# 充气/放气时间(秒)
INFLATE_TIME_A: float = 5.0       # INIT 阶段充气时长 a
DEFLATE_TIME_B: float = 5.0       # DEFLATING 阶段放气时长 b

# 状态机时间阈值(秒)
PERSON_CONFIRM_N1: float = 3.0    # WAITING 状态确认人在线时长 n1
COUNT_MIN_N2: float = 5.0         # COUNTING 最短计时 n2
COUNT_MAX_N3: float = 10.0        # COUNTING 最长计时 n3
ABSENCE_TIMEOUT_N4: float = 3.0   # 人离开超时阈值 n4(触发安全放气)
ENDING_TIMEOUT: float = 30.0      # ENDING 状态最长停留秒数(兜底,防止误检导致死锁)

# 循环节奏
LOOP_INTERVAL: float = 1.0        # INTERVAL 状态间隔秒数
LOOP_COUNT_MAX: int = 3           # 一轮最多抽取的动作次数

# 充气安全阈值
GAS_MAX: int = 15                 # INFLATING 累计充气次数上限,达到则强制放气

# 双 Arduino 串口配置
ARDUINO_BAUDRATE: int = 9600
PUMP_SERIAL_PORT: str = "COM3"    # Uno-A:控制 3 气泵(6 路继电器)
LIGHT_SERIAL_PORT: str = "COM4"   # Uno-B:控制灯箱 3 灯泡(3 路继电器)


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
