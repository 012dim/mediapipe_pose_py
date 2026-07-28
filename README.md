# MediaPipe Pose 动作识别

基于 Google MediaPipe Pose 的实时人体姿态与动作识别项目。从 USB 摄像头实时读取视频流,识别 33 个人体关键点,绘制骨骼并显示 FPS,同时识别 6 种常见动作。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10.14-green) ![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-orange) ![License](https://img.shields.io/badge/License-MIT-yellow)

## 特性

- **实时 33 关键点检测**:鼻、肩、肘、腕、髋、膝、踝、手指、脚趾全覆盖
- **6 种动作识别**:左手举起、右手举起、双手举起、站立、坐下、跌倒
- **平滑滤波**:deque 缓存最近 5 帧坐标取平均,减少关键点抖动
- **FPS 实时显示**:带 30 帧平滑滤波,数字不跳
- **多色骨骼**:躯干/四肢/手/脚用不同颜色区分
- **键盘交互**:截图、切换骨骼、切换摄像头、重置状态
- **可选串口**:动作事件通过串口发送给 Arduino
- **跨平台**:Windows 10/11 优先,兼容 macOS、Linux
- **开箱即用**:`pip install` 后 `python main.py` 一键运行

## 效果截图

程序运行时按 `s` 键截图,自动保存到 `screenshots/` 目录,文件名格式 `pose_YYYYMMDD_HHMMSS.png`。

截图位置:`mediapipe_pose_py/screenshots/`

## 系统要求

- **操作系统**:Windows 10 / 11(优先),macOS,Linux 也可
- **Python**:3.10、3.11 或 3.12
- **摄像头**:USB 摄像头(默认设备 ID 0)
- **CPU**:2018 年后 i5 笔记本即可,CPU 推理 ≥ 25 FPS
- **内存**:< 300MB

## 快速开始(3 步)

```bash
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖(使用清华镜像加速)
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 运行
python main.py
```

> macOS / Linux 激活虚拟环境:`source venv/bin/activate`

启动后 5 秒内即可看到摄像头画面 + 骨骼。按 `q` 退出。

## 按键说明

| 按键 | 功能 |
|------|------|
| `q` 或 `Esc` | 退出程序 |
| `s` | 截图保存到 `screenshots/`,文件名 `pose_YYYYMMDD_HHMMSS.png` |
| `f` | 切换骨骼显示(纯视频流 ↔ 带骨骼) |
| `c` | 切换摄像头 ID(0 → 1 → 2 → 0) |
| `r` | 重置动作识别状态(清除冷却与最近动作) |
| `Ctrl + C` | 优雅退出,释放摄像头与所有窗口 |

## 识别的动作列表

| 动作 | 触发条件 | 事件名 | 中文显示 |
|------|---------|--------|---------|
| 左手举起 | 左手腕 Y < 鼻子 Y − 0.05 | `LEFT_HAND_UP` | 左手举起 |
| 右手举起 | 右手腕 Y < 鼻子 Y − 0.05 | `RIGHT_HAND_UP` | 右手举起 |
| 双手举起 | 左右手腕同时满足上述条件 | `BOTH_HANDS_UP` | 双手举起 |
| 站立 | 膝关节角度 > 160° | `STAND` | 站立 |
| 坐下 | 膝关节角度 < 130° | `SIT` | 坐下 |
| 跌倒 | (肩 Y − 髋 Y) / 肩宽 < 0.3 | `FALL_DETECTED` | 跌倒 |

- 动作触发后,屏幕底部高亮显示动作名,持续 **2 秒**
- 每个动作有 **1 秒冷却**,避免重复触发
- 触发时终端打印:`[14:32:15] 动作:BOTH_HANDS_UP`

### 动作优先级

同一帧中多个动作同时满足时,按优先级触发最高者:
`跌倒 > 双手举起 > 单手举起 > 站立 / 坐下`

## 33 个关键点编号对照表

MediaPipe Pose 输出 33 个关键点,坐标归一化到 [0, 1]:

```
            0 鼻子 nose
           /  \
          1-10 面部(眼、耳、嘴)
         /      \
   11 左肩 ─── 12 右肩
    |             |
   13 左肘       14 右肘
    |             |
   15 左腕  17-22 左手细节
   16 右腕  17-22 右手细节
         \  /
   23 左髋 ─── 24 右髋
    |             |
   25 左膝       26 右膝
    |             |
   27 左踝       28 右踝
    |             |
   29 左跟  31 左脚趾
   30 右跟  32 右脚趾
```

| 编号 | 名称 | 编号 | 名称 |
|------|------|------|------|
| 0 | nose 鼻子 | 17 | left_pinky 左小指 |
| 1 | left_eye_inner 左眼内 | 18 | right_pinky 右小指 |
| 2 | left_eye 左眼 | 19 | left_index 左食指 |
| 3 | left_eye_outer 左眼外 | 20 | right_index 右食指 |
| 4 | right_eye_inner 右眼内 | 21 | left_thumb 左拇指 |
| 5 | right_eye 右眼 | 22 | right_thumb 右拇指 |
| 6 | right_eye_outer 右眼外 | 23 | left_hip 左髋 |
| 7 | left_ear 左耳 | 24 | right_hip 右髋 |
| 8 | right_ear 右耳 | 25 | left_knee 左膝 |
| 9 | mouth_left 左嘴角 | 26 | right_knee 右膝 |
| 10 | mouth_right 右嘴角 | 27 | left_ankle 左踝 |
| 11 | left_shoulder 左肩 | 28 | right_ankle 右踝 |
| 12 | right_shoulder 右肩 | 29 | left_heel 左跟 |
| 13 | left_elbow 左肘 | 30 | right_heel 右跟 |
| 14 | right_elbow 右肘 | 31 | left_foot_index 左脚趾 |
| 15 | left_wrist 左腕 | 32 | right_foot_index 右脚趾 |
| 16 | right_wrist 右腕 |  |  |

详细编号表见 [docs/KEYPOINTS.md](docs/KEYPOINTS.md)。

## 配置说明

所有可调参数集中在 [config.py](config.py),无需改动业务代码:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CAMERA_ID` | `0` | 默认摄像头 ID |
| `CAMERA_WIDTH` / `CAMERA_HEIGHT` | `640` / `480` | 采集分辨率 |
| `CAMERA_FPS` | `30` | 目标帧率 |
| `AVAILABLE_CAMERA_IDS` | `[0, 1, 2]` | 按 c 可循环切换的 ID |
| `MODEL_COMPLEXITY` | `1` | 0 轻量 / 1 中等 / 2 最准 |
| `SMOOTH_BUFFER_SIZE` | `5` | 关键点平滑缓冲帧数 |
| `HAND_UP_THRESHOLD` | `0.05` | 举手判定阈值(归一化) |
| `KNEE_ANGLE_STAND` | `160.0` | 站立膝关节角度阈值 |
| `KNEE_ANGLE_SIT` | `130.0` | 坐下膝关节角度阈值 |
| `FALL_RATIO_THRESHOLD` | `0.3` | 跌倒比率阈值 |
| `ACTION_COOLDOWN` | `1.0` | 动作冷却时间(秒) |
| `ACTION_DISPLAY_DURATION` | `2.0` | 动作显示时长(秒) |
| `SERIAL_ENABLED` | `False` | 是否启用串口 |
| `SERIAL_BAUDRATE` | `9600` | 串口波特率 |

### 切换摄像头

修改 `config.py` 中的 `CAMERA_ID`,或运行时按 `c` 键循环切换。

### 启用串口

把 `config.py` 中 `SERIAL_ENABLED = True`,并根据系统修改默认端口:

```python
# Windows
DEFAULT_SERIAL_PORT_WIN = "COM3"
# Linux
DEFAULT_SERIAL_PORT_LINUX = "/dev/ttyUSB0"
# macOS
DEFAULT_SERIAL_PORT_MAC = "/dev/tty.usbserial*"
```

## Arduino 联动示例

### 接线图

```
Arduino Uno           USB 摄像头
┌─────────────┐       ┌──────────┐
│             │       │          │
│  RX (0) <───┼───────┼ USB      │
│  TX (1) ────┼──┐    │ (本机)   │
│             │  │    │          │
│  GND ───────┼──┼────┼ GND      │
│             │  │    └──────────┘
│  LED 13 ────┘  │
│                │
│  USB-TTL 串口模块
└─────────────┘
```

> 注意:USB 摄像头接电脑,Arduino 通过 USB 或 USB-TTL 模块接到电脑串口。Arduino 的 RX 接 USB-TTL 模块的 TX,反之亦然。

### Arduino 接收端代码

```cpp
// Arduino 接收端:解析 POSE,动作名 串口数据
String inputBuffer = "";

void setup() {
  Serial.begin(9600);
  pinMode(13, OUTPUT);
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine(inputBuffer);
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }
}

void handleLine(String line) {
  // 协议:POSE,ACTION_NAME
  if (!line.startsWith("POSE,")) return;
  String action = line.substring(5);
  if (action == "BOTH_HANDS_UP") {
    digitalWrite(13, HIGH);
    delay(500);
    digitalWrite(13, LOW);
  } else if (action == "FALL_DETECTED") {
    for (int i = 0; i < 5; i++) {
      digitalWrite(13, HIGH); delay(100);
      digitalWrite(13, LOW);  delay(100);
    }
  }
}
```

## FAQ(常见问题)

**Q1:运行报错 `ModuleNotFoundError: No module named 'mediapipe'`?**
A:未安装依赖。先激活虚拟环境,再执行 `pip install -r requirements.txt`。如安装慢,加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

**Q2:启动后报 `无法打开摄像头 0`?**
A:① 摄像头被其他软件(Zoom、微信)占用,关闭后重试;② 设备 ID 不是 0,改 `config.py` 的 `CAMERA_ID` 或按 `c` 切换;③ 检查 USB 连接。

**Q3:FPS 低于 25?**
A:① 降低 `MODEL_COMPLEXITY` 为 0(轻量);② 关闭其他占用 CPU 的程序;③ 降低分辨率到 480×360;④ 确认未启用 `model_complexity=2`。

**Q4:举手没反应?**
A:① 站到摄像头前,确保上半身完整入镜;② 手腕要明显高于鼻子(超过 5% 画面高度);③ 检查 `MIN_DETECTION_CONFIDENCE` 是否过低;④ 按 `r` 重置冷却再试。

**Q5:骨骼闪烁严重?**
A:① 增大 `SMOOTH_BUFFER_SIZE`(如改为 8);② 确认 `SMOOTH_LANDMARKS = True`;③ 改善光照条件,避免逆光。

**Q6:跌倒误报?**
A:① 调高 `FALL_RATIO_THRESHOLD`(如 0.4);② 确保肩部水平(头不歪);③ 站直时肩髋高度差应明显大于肩宽的 30%。

**Q7:串口打不开?**
A:① 确认 Arduino 已连接且驱动安装;② 检查端口名(COM3 / /dev/ttyUSB0);③ 关闭串口监视器等其他占用程序;④ 程序不会因串口失败崩溃,会继续运行。

**Q8:`cv2.imshow` 窗口无响应?**
A:① 确认 OpenCV 安装的是 `opencv-python` 而非 `opencv-python-headless`;② 不要在 SSH 远程无图形界面运行;③ Linux 需安装 GTK 系统库。

**Q9:macOS 上摄像头权限被拒?**
A:在 `系统设置 → 隐私与安全 → 摄像头` 中允许 Terminal / Python 访问摄像头。

**Q10:按 `c` 切换摄像头后画面黑屏?**
A:目标 ID 没有摄像头,程序会自动切回原摄像头。检查 `AVAILABLE_CAMERA_IDS` 配置。

## 项目结构

```
mediapipe_pose_py/
├── main.py                     # 程序入口,主循环
├── config.py                   # 配置文件(阈值、端口、开关)
├── requirements.txt            # Python 依赖列表
├── conftest.py                 # pytest 配置(sys.path)
├── .gitignore                  # Git 忽略文件
├── README.md                   # 本文档
├── modules/
│   ├── __init__.py
│   ├── camera.py               # 摄像头封装
│   ├── pose_detector.py        # MediaPipe 姿态识别封装
│   ├── visualizer.py           # 骨骼绘制 + FPS + 文字
│   ├── action_recognizer.py    # 动作识别(6 种 + 冷却)
│   ├── angle_calculator.py     # 关节角度计算工具
│   └── serial_sender.py        # 串口发送(异步,容错)
├── models/                     # 占位(MediaPipe 用内置模型)
├── screenshots/                # 截图保存目录
├── tests/                      # 单元测试(pytest)
│   ├── test_angle_calculator.py
│   ├── test_action_recognizer.py
│   └── README.md
└── docs/
    ├── ARCHITECTURE.md         # 架构说明 + 类图
    ├── KEYPOINTS.md            # 33 关键点编号对照表
    └── TROUBLESHOOTING.md      # 常见问题排查
```

## 运行测试

```bash
pytest tests/ -v
```

## 性能指标

| 指标 | 目标 | 实测参考(i5-8250U) |
|------|------|---------------------|
| 推理帧率 | ≥ 25 FPS | 28-32 FPS(complexity=1) |
| 内存占用 | < 300MB | ~180MB |
| 启动到首帧 | < 5 秒 | ~3 秒 |
| 关键点抖动 | 平滑 | deque-5 平均 |

## License

MIT License - 详见 [LICENSE](LICENSE)。

Copyright (c) 2026
