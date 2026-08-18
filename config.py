"""配置文件:集中管理所有可调参数。

修改本文件即可调整摄像头、MediaPipe、动作识别、串口等所有参数,
无需改动业务代码。
"""

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


# ============ 串口配置 ============
# SERIAL_ENABLED=True:严格门禁,3块泵控UNO必须全部连接才进入运行态
# SERIAL_ENABLED=False:跳过串口(测试用),状态机仍流转,发送静默失败
SERIAL_ENABLED: bool = False
SERIAL_TIMEOUT: float = 1.0           # 读超时(秒)
SERIAL_WRITE_TIMEOUT: float = 0.5    # 写超时(秒,防卡死)


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
GAS_MAX: int = 15                 # INFLATING 累计充气次数上限,达到则锁定充气

# ============ 4 板 Arduino 串口配置 ============
# 3 块泵控 UNO(PUMP_A/B/C),每块控制 3 个气泵 + 3 个电磁阀 = 6 路
# 第 4 块为灯箱 UNO(LIGHT),控制 3 个灯泡
# ★ 端口号必须以实机设备管理器为准 ★
PUMP_BOARDS: list = [
    {'id': 'PUMP_A', 'port': 'COM3'},   # 板A:气泵 A1/A2/A3 + 阀 A1/A2/A3
    {'id': 'PUMP_B', 'port': 'COM5'},   # 板B:气泵 B1/B2/B3 + 阀 B1/B2/B3
    {'id': 'PUMP_C', 'port': 'COM7'},   # 板C:气泵 C1/C2/C3 + 阀 C1/C2/C3
]
LIGHT_SERIAL_PORT: str = "COM4"    # 灯箱 UNO(第 4 块,独立)
ARDUINO_BAUDRATE: int = 9600

# ============ INFLATE_M 每泵充气时长(毫秒)============
# 9 泵同步动作,但每泵时长不同(≤1000ms),Python 每秒广播一次 INFLATE_M
# 每块 UNO 本地硬编码自己的 3 个时长;此处正式模式下作为 READY 启动门禁
# (connect(expected_ready_params=...) 会与 Arduino 上电 READY 中的三路时长严格比对,
#  不一致即拒绝进入运行态,拦截"PUMP_B 烧了 PUMP_A 参数"或"改配置未重烧"错误)
# ★ 实物标定前为占位值,通电前必须重新确认且与 Arduino 固件完全一致 ★
INFLATE_M_MS_PER_BOARD: dict = {
    'PUMP_A': [300, 500, 800],   # A1, A2, A3
    'PUMP_B': [400, 600, 700],   # B1, B2, B3
    'PUMP_C': [500, 700, 900],   # C1, C2, C3
}

# ============ SAFE_STOP 错误态配置 ============
# 任一泵控板发送失败时,全组进入 SAFE_STOP:广播 STOP_ALL,等待放气后退出
SAFE_STOP_DEFLATE_TIME: float = 5.0   # SAFE_STOP 时强制放气秒数


# ============ 路径配置 ============
SCREENSHOT_DIR: str = "screenshots"


# ============ 显示配置 ============
SHOW_SKELETON_DEFAULT: bool = True  # 启动时是否显示骨骼


# ============ 日志配置 ============
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
