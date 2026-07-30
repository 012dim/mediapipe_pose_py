# MediaPipe Pose 动作识别 + Arduino 交互系统

基于 Google MediaPipe Pose 的实时手部动作识别项目,并扩展为双 Arduino Uno 控制的气泵 + 灯箱交互系统。摄像头实时识别手部动作,驱动 8 状态有限状态机,通过串口指令控制气泵充放气与灯箱亮灭,实现"抽题 → 计时 → 惩罚充气 → 结束放气"的完整交互流程。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10.14-green) ![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-orange) ![Arduino](https://img.shields.io/badge/Arduino-Uno-00979D) ![License](https://img.shields.io/badge/License-MIT-yellow)

## 特性

- **实时 33 关键点检测**:MediaPipe Pose 全身关键点,平滑滤波减少抖动
- **3 种手部动作识别**:左手举起 / 右手举起 / 双手举起(画面镜像,与用户直觉一致)
- **8 状态有限状态机**:INIT → WAITING → EXTRACTING → COUNTING ⇄ INFLATING → INTERVAL → ENDING → DEFLATING → 循环
- **双 Arduino 控制**:Uno-A 控制 3 气泵(6 路继电器),Uno-B 控制灯箱 3 灯泡(3 路继电器)
- **智能计时**:只有动作正确时才推进计时,错误/无动作时暂停(不重置)
- **安全机制**:人离开超时自动放气;充气达上限锁定后续充气,放气后恢复
- **误检防护**:核心关键点可见度校验 + 高置信度阈值,挡掉椅子/衣架等误检
- **可视化面板**:状态名 / 计时进度条 / 充气量 / 灯泡亮灭 / 充气锁定提示
- **跨平台**:Windows 10/11 优先,兼容 macOS、Linux

## 系统要求

- **操作系统**:Windows 10 / 11(优先),macOS,Linux
- **Python**:3.10、3.11 或 3.12
- **摄像头**:USB 摄像头(默认设备 ID 0)
- **硬件**(可选):2 块 Arduino Uno + 6 路继电器模块(气泵)+ 3 路继电器模块(灯箱)
- **CPU**:2018 年后 i5 笔记本即可,CPU 推理 ≥ 25 FPS

## 快速开始

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

启动后进入 INIT 充气阶段(5 秒),随后等待人物出现。按 `q` 退出。

## 按键说明

| 按键 | 功能 |
|------|------|
| `q` 或 `Esc` | 退出程序 |
| `s` | 截图保存到 `screenshots/`,文件名 `pose_YYYYMMDD_HHMMSS.png` |
| `f` | 切换骨骼显示(纯视频流 ↔ 带骨骼) |
| `c` | 切换摄像头 ID(0 → 1 → 2 → 0) |
| `r` | 重置动作识别 + 状态机(回到 INIT) |
| `Ctrl + C` | 优雅退出,释放摄像头、串口与所有窗口 |

## 识别的动作

| 动作 | 触发条件 | 事件名 | 对应灯泡 |
|------|---------|--------|---------|
| 左手举起 | 左手腕 Y < 鼻子 Y − 0.05 | `LEFT_HAND_UP` | 灯1 |
| 右手举起 | 右手腕 Y < 鼻子 Y − 0.05 | `RIGHT_HAND_UP` | 灯2 |
| 双手举起 | 左右手腕同时满足上述条件 | `BOTH_HANDS_UP` | 灯3 |
| 无动作 | 以上均不满足 | `HAND_NONE` | — |

> 注:画面水平翻转(镜像),MediaPipe 的 LEFT_WRIST/RIGHT_WRIST 已交换判定,识别结果与用户直觉一致。

## Arduino 交互系统

### 硬件接线

```
┌─────────────┐   USB    ┌──────────────┐
│  摄像头      │──────────│  电脑(Python) │
└─────────────┘          └──────┬───────┘
                                │ COM3 / COM4
                   ┌────────────┴────────────┐
                   ▼                         ▼
          ┌─────────────────┐       ┌─────────────────┐
          │  Uno-A (气泵)    │       │  Uno-B (灯箱)    │
          │  COM3 @ 9600     │       │  COM4 @ 9600     │
          │                  │       │                  │
          │  D2 → RELAY1 泵1充│       │  D2 → RELAY1 灯1 │
          │  D3 → RELAY2 泵1放│       │  D3 → RELAY2 灯2 │
          │  D4 → RELAY3 泵2充│       │  D4 → RELAY3 灯3 │
          │  D5 → RELAY4 泵2放│       └─────────────────┘
          │  D6 → RELAY5 泵3充│
          │  D7 → RELAY6 泵3放│
          └─────────────────┘
```

### 8 状态机流程

```
INIT(充气a秒) → WAITING(等人≥n1秒) → EXTRACTING(抽动作,亮灯)
    ↓                                        ↓
    └─────────────────────────────── COUNTING(计时,判动作)
                          ↑↓错误           ↓ 计时完
                  INFLATING(充气)       INTERVAL(灭灯)
                      ↑正确               ↓
                      └─────← n<3 ──→ n=3 → ENDING(三灯闪3次)
                                               ↓ 人离开≥n4秒
                                          DEFLATING(放气b秒)
                                               ↓
                                             回 INIT
```

| 状态 | 说明 |
|------|------|
| INIT | 发 `INFLATE_ALL,a`,初始充气 a 秒,gass=0 |
| WAITING | 等待可靠人持续在线 ≥ n1 秒,进入抽题 |
| EXTRACTING | 随机抽取目标动作,点亮对应灯泡(瞬态) |
| COUNTING | 只有动作正确才推进计时;错误→INFLATING;无动作也判错误 |
| INFLATING | 每秒发 `INFLATE_M` 充气,gass+=1;动作正确回 COUNTING(计时从暂停处继续) |
| INTERVAL | 灭灯,间隔 1 秒;n<3 回 EXTRACTING,n=3 进 ENDING |
| ENDING | 三灯闪烁 3 次;人离开 ≥ n4 秒或超时 30 秒进 DEFLATING |
| DEFLATING | 发 `DEFLATE_ALL,b`,放气 b 秒,gass=0,回 INIT |

**安全机制**:EXTRACTING/COUNTING/INFLATING/INTERVAL/ENDING 中人消失 ≥ n4 秒 → 立即 `STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL`。

**充气锁定**:gass 达 `GAS_MAX`(15)后停止充气,锁定后续充气指令(窗口显示红色提示),状态机继续流转,直到 ENDING → DEFLATING 放气后自动解锁。

### 串口协议

**气泵 (Uno-A, COM3)**:

| 指令 | 含义 |
|------|------|
| `INFLATE_ALL,a` | 全部气泵充气 a 秒 |
| `DEFLATE_ALL,b` | 全部气泵放气 b 秒 |
| `INFLATE_M` | 主气泵点充一次(时长由 Arduino 端常量设定) |
| `STOP_ALL` | 断开全部继电器 |

**灯箱 (Uno-B, COM4)**:

| 指令 | 含义 |
|------|------|
| `LIGHT_ON,id` | 点亮灯 id(1/2/3) |
| `LIGHT_OFF,id` | 熄灭灯 id |
| `LIGHT_ALL_OFF` | 全部熄灭 |
| `LIGHT_FLASH,3` | 三灯闪烁 3 次 |

### Arduino 参考代码

完整的 Arduino 代码(含详细注释)见 [arduino_commands/](arduino_commands/) 目录:

- [pump_uno_commands.txt](arduino_commands/pump_uno_commands.txt) — 气泵 Uno-A 代码,顶部可调 `INFLATE_M_MS_PER_PUMP`(每个气泵吸气时长)和 `INFLATE_M_PUMP_INDEX`(主气泵选择)
- [lightbox_uno_commands.txt](arduino_commands/lightbox_uno_commands.txt) — 灯箱 Uno-B 代码,顶部可调引脚、触发电平、闪烁时长

将代码复制到 Arduino IDE,修改顶部【用户可调参数区】后分别上传至两块 Uno。

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
   15 左腕       16 右腕
         \  /
   23 左髋 ─── 24 右髋
    |             |
   25 左膝       26 右膝
    |             |
   27 左踝       28 右踝
```

详细编号表见 [docs/KEYPOINTS.md](docs/KEYPOINTS.md)。

## 配置说明

所有可调参数集中在 [config.py](config.py):

### 摄像头 / MediaPipe

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CAMERA_ID` | `0` | 默认摄像头 ID |
| `CAMERA_WIDTH` / `CAMERA_HEIGHT` | `640` / `480` | 采集分辨率 |
| `MODEL_COMPLEXITY` | `1` | 0 轻量 / 1 中等 / 2 最准 |
| `SMOOTH_BUFFER_SIZE` | `5` | 关键点平滑缓冲帧数 |
| `MIN_DETECTION_CONFIDENCE` | `0.7` | 检测置信度(提高以减少误检) |
| `MIN_TRACKING_CONFIDENCE` | `0.6` | 跟踪置信度 |
| `HAND_UP_THRESHOLD` | `0.05` | 举手判定阈值(归一化) |

### Arduino 交互流程

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `INFLATE_TIME_A` | `5.0` | INIT 充气时长 a(秒) |
| `DEFLATE_TIME_B` | `5.0` | DEFLATING 放气时长 b(秒) |
| `PERSON_CONFIRM_N1` | `3.0` | WAITING 确认人在线时长 n1(秒) |
| `COUNT_MIN_N2` | `5.0` | COUNTING 最短计时 n2(秒) |
| `COUNT_MAX_N3` | `10.0` | COUNTING 最长计时 n3(秒) |
| `ABSENCE_TIMEOUT_N4` | `3.0` | 人离开超时 n4(秒,触发安全放气) |
| `ENDING_TIMEOUT` | `30.0` | ENDING 最长停留(秒,兜底防死锁) |
| `LOOP_INTERVAL` | `1.0` | INTERVAL 间隔(秒) |
| `LOOP_COUNT_MAX` | `3` | 一轮最多抽题次数 |
| `GAS_MAX` | `15` | 充气次数上限,达到后锁定充气 |

### 双 Arduino 串口

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ARDUINO_BAUDRATE` | `9600` | Arduino 串口波特率 |
| `PUMP_SERIAL_PORT` | `"COM3"` | Uno-A 气泵串口 |
| `LIGHT_SERIAL_PORT` | `"COM4"` | Uno-B 灯箱串口 |

> 串口发送设有 `write_timeout=0.5` 秒,对端不响应时不会卡死主循环。

## 项目结构

```
mediapipe_pose_py/
├── main.py                     # 程序入口,主循环
├── config.py                   # 配置文件(阈值、串口、状态机参数)
├── requirements.txt            # Python 依赖列表
├── conftest.py                 # pytest 配置
├── README.md                   # 本文档
├── modules/
│   ├── __init__.py
│   ├── camera.py               # 摄像头封装
│   ├── pose_detector.py        # MediaPipe 姿态识别封装
│   ├── visualizer.py           # 骨骼绘制 + 状态机面板 + 灯泡显示
│   ├── action_recognizer.py    # 手部动作识别 + check_match 匹配
│   ├── angle_calculator.py     # 关节角度计算工具
│   ├── state_machine.py        # 8 状态有限状态机(核心)
│   └── serial_sender.py        # 双串口 PumpSender + LightSender
├── arduino_commands/           # Arduino 参考代码(不被 Python 执行)
│   ├── pump_uno_commands.txt   # Uno-A 气泵代码(含详细注释)
│   └── lightbox_uno_commands.txt # Uno-B 灯箱代码(含详细注释)
├── models/                     # 占位(MediaPipe 用内置模型)
├── screenshots/                # 截图保存目录
└── docs/
    ├── ARCHITECTURE.md         # 架构说明
    ├── KEYPOINTS.md            # 33 关键点编号对照表
    └── TROUBLESHOOTING.md      # 常见问题排查
```

## FAQ(常见问题)

**Q1:运行报错 `ModuleNotFoundError: No module named 'mediapipe'`?**
A:未安装依赖。先激活虚拟环境,再执行 `pip install -r requirements.txt`。如安装慢,加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

**Q2:启动后报 `无法打开摄像头 0`?**
A:① 摄像头被其他软件占用,关闭后重试;② 设备 ID 不是 0,改 `config.py` 的 `CAMERA_ID` 或按 `c` 切换;③ 检查 USB 连接。

**Q3:等人阶段进度条不动?**
A:MediaPipe 可能未检测到"可靠人"。确保上半身(鼻子+双肩)完整入镜且光照充足。半身坐姿也能识别,不强制要求髋部入镜。

**Q4:进入抽题后程序卡死/窗口未响应?**
A:串口 `write` 阻塞。已通过 `write_timeout=0.5` + 去掉 `flush()` 修复。若仍卡死,检查 COM3/COM4 是否被其他程序占用,或暂时不接 Arduino(状态机会继续流转,串口发送静默失败)。

**Q5:左右手识别反了?**
A:画面已做水平翻转(镜像),代码中 LEFT_WRIST/RIGHT_WRIST 已交换判定。若仍反,检查是否修改过 `action_recognizer.py` 的 `_detect_hand_action`。

**Q6:充气达上限后不充气了?**
A:这是设计行为。gass 达 `GAS_MAX`(15)后锁定充气,窗口显示红色"充气已锁定!"提示。状态机继续流转,3 轮结束 → ENDING → DEFLATING 放气后自动解锁。

**Q7:串口 COM3 打不开(OSError 121)?**
A:Windows 错误 121 通常是端口被占用或 CH340 驱动异常。① 拔插 USB 重新枚举;② 关闭其他占用 COM3 的程序;③ 重装 CH340 驱动。代码侧已容错,不会因此崩溃。

**Q8:举手没反应?**
A:① 确保上半身完整入镜;② 手腕要明显高于鼻子(超过 5% 画面高度);③ 站到画面中央;④ 按 `r` 重置状态机。

**Q9:骨骼闪烁严重?**
A:① 增大 `SMOOTH_BUFFER_SIZE`(如改为 8);② 确认 `SMOOTH_LANDMARKS = True`;③ 改善光照条件,避免逆光。

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
| 串口发送延迟 | < 1ms | < 1ms(正常)/ 0.5s 超时(异常) |

## License

MIT License - 详见 [LICENSE](LICENSE)。

Copyright (c) 2026
