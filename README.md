# MediaPipe Pose 动作识别 + Arduino 交互系统

基于 Google MediaPipe Pose 的实时手部动作识别项目,并扩展为 4 板 Arduino Uno 控制的气泵 + 灯箱交互系统。摄像头实时识别手部动作,驱动 9 状态有限状态机,通过串口指令控制 9 泵同步充放气与灯箱亮灭,实现"抽题 → 计时 → 惩罚充气 → 结束放气"的完整交互流程。

> **架构版本**:v4.2 — 3 块泵控 UNO(PUMP_A/B/C,各控 3 泵 + 3 阀 = 6 设备)+ 1 块灯箱 UNO(LIGHT),共 9 泵 9 阀 3 灯。泵控采用 RC 脉冲 + 继电器供电隔离模型。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10.14-green) ![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-orange) ![Arduino](https://img.shields.io/badge/Arduino-Uno-00979D) ![License](https://img.shields.io/badge/License-MIT-yellow)

## 特性

- **实时 33 关键点检测**:MediaPipe Pose 全身关键点,平滑滤波减少抖动
- **3 种手部动作识别**:左手举起 / 右手举起 / 双手举起(画面镜像,与用户直觉一致)
- **9 状态有限状态机**:INIT → WAITING → EXTRACTING → COUNTING ⇄ INFLATING → INTERVAL → ENDING → DEFLATING → 循环;新增 SAFE_STOP 安全停止态
- **4 板 Arduino 控制**:3 块泵控 UNO(PUMP_A/B/C,各控 3 泵 + 3 阀 = 6 设备,RC 脉冲 + 继电器供电隔离)+ 1 块灯箱 UNO(3 灯泡),共 9 泵 9 阀同步动作
- **智能计时**:只有动作正确时才推进计时,错误/无动作时暂停(不重置)
- **安全机制**:人离开超时自动放气;充气达上限锁定后续充气,放气后恢复;任一泵控板发送失败 → 全组进入 SAFE_STOP(广播 STOP_ALL + 放气后等待退出)
- **误检防护**:核心关键点可见度校验 + 高置信度阈值,挡掉椅子/衣架等误检
- **可视化面板**:状态名 / 计时进度条 / 充气量 / 灯泡亮灭 / 充气锁定提示
- **跨平台**:Windows 10/11 优先,兼容 macOS、Linux

## 系统要求

- **操作系统**:Windows 10 / 11(优先),macOS,Linux
- **Python**:3.10、3.11 或 3.12
- **摄像头**:USB 摄像头(默认设备 ID 0)
- **硬件**(可选):4 块 Arduino Uno — 3 块泵控(各 6 路继电器 + 6 路 Servo S 线,控制 3 泵 + 3 阀)+ 1 块灯箱(3 路继电器)
- **CPU**:2018 年后 i5 笔记本即可,CPU 推理 ≥ 25 FPS

## 快速开始

```bash
# 1. 创建虚拟环境(Python 3.10 / 3.11 / 3.12)
py -3.12 -m venv venv
venv\Scripts\activate

# 2. 安装依赖(使用清华镜像加速)
pip install --upgrade pip
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 运行
python main.py
```

> macOS / Linux 激活虚拟环境:`source venv/bin/activate`

### 运行模式(由 `config.py` 的 `SERIAL_ENABLED` 控制)

- **`SERIAL_ENABLED = False`(默认,测试模式)**:只测试摄像头、动作识别和状态机界面,不连接 Arduino。串口发送静默失败(返回成功),状态机正常流转。适用于无硬件的开发/测试环境。
- **`SERIAL_ENABLED = True(正式运行)**:连接 4 块 UNO(3 块泵控 + 1 块灯箱)并控制真实硬件。3 板泵控必须全部连接且 READY 板号匹配才进入运行态,否则拒绝启动。

> **注意**:默认 `SERIAL_ENABLED=False`,不会控制气泵。正式展览/联机测试时必须改为 `True` 并正确配置 `PUMP_BOARDS` 与 `LIGHT_SERIAL_PORT`。

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
                                │ 4 路 USB 转串口
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼                   ▼
   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
   │  PUMP_A (泵A)    │  │  PUMP_B (泵B)    │  │  PUMP_C (泵C)    │  │  LIGHT (灯箱)    │
   │  COM3 @ 9600    │  │  COM5 @ 9600    │  │  COM7 @ 9600    │  │  COM4 @ 9600    │
   │                 │  │                 │  │                 │  │                 │
   │  D2 → RELAY 泵1 │  │  D2 → RELAY 泵1 │  │  D2 → RELAY 泵1 │  │  D2 → RELAY 灯1 │
   │  D3 → RELAY 阀1 │  │  D3 → RELAY 阀1 │  │  D3 → RELAY 阀1 │  │  D3 → RELAY 灯2 │
   │  D4 → RELAY 泵2 │  │  D4 → RELAY 泵2 │  │  D4 → RELAY 泵2 │  │  D4 → RELAY 灯3 │
   │  D5 → RELAY 阀2 │  │  D5 → RELAY 阀2 │  │  D5 → RELAY 阀2 │  └─────────────────┘
   │  D6 → RELAY 泵3 │  │  D6 → RELAY 泵3 │  │  D6 → RELAY 泵3 │
   │  D7 → RELAY 阀3 │  │  D7 → RELAY 阀3 │  │  D7 → RELAY 阀3 │
   │  D8  → S 线 泵1 │  │  D8  → S 线 泵1 │  │  D8  → S 线 泵1 │
   │  D9  → S 线 阀1 │  │  D9  → S 线 阀1 │  │  D9  → S 线 阀1 │
   │  D10 → S 线 泵2 │  │  D10 → S 线 泵2 │  │  D10 → S 线 泵2 │
   │  D11 → S 线 阀2 │  │  D11 → S 线 阀2 │  │  D11 → S 线 阀2 │
   │  D12 → S 线 泵3 │  │  D12 → S 线 泵3 │  │  D12 → S 线 泵3 │
   │  D13 → S 线 阀3 │  │  D13 → S 线 阀3 │  │  D13 → S 线 阀3 │
   └─────────────────┘  └─────────────────┘  └─────────────────┘
```

> **泵控板硬件模型(v4.2)**:每台设备(泵/阀)占用 1 路继电器(供电通断,D2-D7)+ 1 路 S 线(RC 脉冲,D8-D13,由 Servo 库生成)。继电器闭合 → 设备得电;Servo 输出 RC 脉冲 → 控制设备启停。3 块泵控板烧同一份 [pump_uno_v4_2.ino](arduino_commands/pump_uno_v4_2.ino),仅修改顶部 `BOARD_ID` 即可区分。

### 9 状态机流程

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

           任一泵控板发送失败 → SAFE_STOP(广播STOP_ALL + 放气后等待用户退出)
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
| SAFE_STOP | 任一泵控板发送失败时进入:best-effort 广播 STOP_ALL + 放气 `SAFE_STOP_DEFLATE_TIME` 秒,保持等待用户按 q 退出(不自动恢复) |

**安全机制**:EXTRACTING/COUNTING/INFLATING/INTERVAL/ENDING 中人消失 ≥ n4 秒 → 立即 `STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL`。

**充气锁定**:gass 达 `GAS_MAX`(15)后停止充气,锁定后续充气指令(窗口显示红色提示),状态机继续流转,直到 ENDING → DEFLATING 放气后自动解锁。

**SAFE_STOP 触发**:`PumpGroupSender` 对 `INFLATE_ALL`/`DEFLATE_ALL`/`INFLATE_M` 任一板失败 → best-effort `stop_all_best_effort()` + 进入 SAFE_STOP 态;`STOP_ALL` 自身采用 best-effort(不进 SAFE_STOP,避免递归)。

### 串口协议

**泵控板 (3 板: PUMP_A / PUMP_B / PUMP_C,各自独立 COM 口)**:

Python 端 `PumpGroupSender` 把每条逻辑命令广播给 3 块板;每块板独立执行并回送 ACK/ERR。

| 指令 | 含义 | 参数约束 |
|------|------|---------|
| `INFLATE_ALL,a` | 全部 3 泵充气 a 秒 | 0 < a ≤ 30,否则 `ERR,<板号>,BAD_DURATION` |
| `DEFLATE_ALL,b` | 全部 3 阀打开放气 b 秒 | 0 < b ≤ 30,否则 `ERR,<板号>,BAD_DURATION` |
| `INFLATE_M` | 9 泵(本板 3 泵)同步点充,每泵独立时长(见 `INFLATE_M_MS_PER_PUMP`);Python 每秒广播一次刷新 | — |
| `STOP_ALL` | 立即停止全部 6 设备(模式互斥优先级最高) | — |
| `STATUS` | 查询当前板状态 | 返回 `STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx` |
| `TEST_PUMP,i,t` | 测试第 i 号泵(0..2),持续 t 秒 | 0 < t ≤ 5,否则 `ERR,<板号>,BAD_TEST_DURATION` |

**泵控板响应**:

| 响应 | 含义 |
|------|------|
| `READY,<板号>` | 上电就绪 |
| `ACK,<板号>,<命令>` | 指令执行成功(如 `ACK,PUMP_A,INFLATE_ALL`) |
| `ERR,<板号>,<原因>` | 指令拒绝/失败(原因:`BAD_DURATION` / `BAD_PUMP_INDEX` / `BAD_TEST_DURATION` / `BAD_ARGS` / `UNKNOWN_CMD`) |
| `STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx` | 状态查询响应(mode ∈ IDLE/INFLATE_ALL/DEFLATE_ALL/INFLATE_M/TEST;relay/servo 为 6 位 0/1 位图,设备 0..5) |

**灯箱 (LIGHT, COM4)**:

| 指令 | 含义 |
|------|------|
| `LIGHT_ON,id` | 点亮灯 id(1/2/3) |
| `LIGHT_OFF,id` | 熄灭灯 id |
| `LIGHT_ALL_OFF` | 全部熄灭 |
| `LIGHT_FLASH,3` | 三灯闪烁 3 次 |

> **模式互斥(泵控板)**:同一时刻只能处于一个模式(IDLE / INFLATE_ALL / DEFLATE_ALL / INFLATE_M / TEST)。进入新指令前自动 `allOff()`(停止所有 RC 脉冲 + 断开所有继电器),再启动新模式。
>
> **INFLATE_M 时序**:Python 每秒广播一次 `INFLATE_M`,每次广播重启所有 3 泵周期。每泵按 `INFLATE_M_MS_PER_PUMP[i]` 独立计时(均 ≤ 1000ms),到时自动停泵。本地看门狗 `INFLATE_M_LOCAL_TIMEOUT_MS=1500ms`:若 1500ms 内未收到新 `INFLATE_M`,强制停止所有泵(防 Python 卡死)。

### Arduino 参考代码

完整的 Arduino 代码(含详细注释)见 [arduino_commands/](arduino_commands/) 目录:

- [pump_uno_v4_2.ino](arduino_commands/pump_uno_v4_2.ino) — **★ 当前版本(v4.2)** 3 块泵控 UNO 共用源码。基于 RC 脉冲 + 继电器供电隔离模型,扩展至 3 泵 + 3 阀 = 6 设备。烧录前修改顶部 `BOARD_ID` 为 `PUMP_A` / `PUMP_B` / `PUMP_C`,以及 `INFLATE_M_MS_PER_PUMP[3]`(每泵吸气时长,实物标定后修改)
- [pump_uno_commands.txt](arduino_commands/pump_uno_commands.txt) — **(旧版 v4.1,已废弃)** 单板单泵控继电器模型参考代码。仅供历史对照,新项目请使用 `pump_uno_v4_2.ino`
- [lightbox_uno_commands.txt](arduino_commands/lightbox_uno_commands.txt) — 灯箱 UNO 代码,顶部可调引脚、触发电平、闪烁时长

烧录步骤:

1. 用 Arduino IDE 打开 `pump_uno_v4_2.ino`,修改顶部 `BOARD_ID` 为 `PUMP_A`
2. 修改 `INFLATE_M_MS_PER_PUMP[3]` 为本板 3 个泵的实测吸气时长(毫秒,≤ 1000)
3. 上传至第 1 块 UNO;重复步骤 1-3,改 `BOARD_ID` 为 `PUMP_B` / `PUMP_C` 后分别上传至第 2、3 块 UNO
4. 打开 `lightbox_uno_commands.txt` 中的代码,上传至第 4 块 UNO(灯箱)
5. 在 Windows 设备管理器中确认 4 个 COM 口编号,更新 [config.py](config.py) 的 `PUMP_BOARDS` 与 `LIGHT_SERIAL_PORT`

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

### 4 板 Arduino 串口

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SERIAL_ENABLED` | `False` | 串口总开关。`True`:严格门禁,3 板泵控必须全部连接才进入运行态;`False`:测试模式,状态机流转但发送静默失败 |
| `ARDUINO_BAUDRATE` | `9600` | Arduino 串口波特率(4 板统一) |
| `SERIAL_WRITE_TIMEOUT` | `0.5` | 写超时(秒,防卡死) |
| `PUMP_BOARDS` | (见下表) | 3 块泵控 UNO 配置(板 ID + COM 口) |
| `LIGHT_SERIAL_PORT` | `"COM4"` | 灯箱 UNO 串口 |
| `INFLATE_M_MS_PER_BOARD` | (见下表) | 9 泵 INFLATE_M 时长(毫秒,仅供 Python 端记录;每板 UNO 本地硬编码) |
| `SAFE_STOP_DEFLATE_TIME` | `5.0` | SAFE_STOP 态强制放气秒数 |

`PUMP_BOARDS` 默认配置(★ COM 口为占位,实机以设备管理器为准):

| 板 ID | COM 口 | 控制 |
|------|--------|------|
| `PUMP_A` | `COM3` | 泵 A1/A2/A3 + 阀 A1/A2/A3 |
| `PUMP_B` | `COM5` | 泵 B1/B2/B3 + 阀 B1/B2/B3 |
| `PUMP_C` | `COM7` | 泵 C1/C2/C3 + 阀 C1/C2/C3 |

`INFLATE_M_MS_PER_BOARD` 默认值(占位,实物标定后必须修改):

```python
INFLATE_M_MS_PER_BOARD = {
    'PUMP_A': [300, 500, 800],   # A1, A2, A3
    'PUMP_B': [400, 600, 700],   # B1, B2, B3
    'PUMP_C': [500, 700, 900],   # C1, C2, C3
}
```

> 串口发送设有 `write_timeout=0.5` 秒,对端不响应时不会卡死主循环。
> `SERIAL_ENABLED=False` 时跳过所有串口连接,状态机正常流转(发送静默失败),用于无硬件的开发/测试环境。

## 项目结构

```
mediapipe_pose_py/
├── main.py                     # 程序入口,主循环
├── config.py                   # 配置文件(阈值、串口、状态机参数、PUMP_BOARDS)
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
│   ├── state_machine.py        # 9 状态有限状态机(含 SAFE_STOP,核心)
│   └── serial_sender.py        # PumpSender / PumpGroupSender / LightSender
├── arduino_commands/           # Arduino 参考代码(不被 Python 执行)
│   ├── pump_uno_v4_2.ino       # ★ v4.2 泵控 UNO 源码(3 板共用,改 BOARD_ID)
│   ├── pump_uno_commands.txt   # (旧版 v4.1,已废弃)单板泵控参考
│   └── lightbox_uno_commands.txt # 灯箱 UNO 代码(含详细注释)
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
A:串口 `write` 阻塞。已通过 `write_timeout=0.5` + 去掉 `flush()` 修复。若仍卡死,检查 COM3/4/5/7 是否被其他程序占用,或将 `config.py` 的 `SERIAL_ENABLED` 改为 `False`(状态机正常流转,串口发送静默失败)。

**Q5:左右手识别反了?**
A:画面已做水平翻转(镜像),代码中 LEFT_WRIST/RIGHT_WRIST 已交换判定。若仍反,检查是否修改过 `action_recognizer.py` 的 `_detect_hand_action`。

**Q6:充气达上限后不充气了?**
A:这是设计行为。gass 达 `GAS_MAX`(15)后锁定充气,窗口显示红色"充气已锁定!"提示。状态机继续流转,3 轮结束 → ENDING → DEFLATING 放气后自动解锁。

**Q7:串口 COM3/5/7 打不开(OSError 121)?**
A:Windows 错误 121 通常是端口被占用或 CH340 驱动异常。① 拔插 USB 重新枚举;② 关闭其他占用该 COM 的程序;③ 重装 CH340 驱动;④ 在设备管理器中确认 4 块 UNO 的实际 COM 编号,更新 [config.py](config.py) 的 `PUMP_BOARDS` 与 `LIGHT_SERIAL_PORT`。代码侧已容错,不会因此崩溃。

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
