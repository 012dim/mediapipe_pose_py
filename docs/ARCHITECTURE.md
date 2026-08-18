# 架构说明

本文档说明项目的整体架构、模块职责与数据流。

## 总体架构

项目采用 **分层 + 模块化** 架构,所有可调参数集中在 `config.py`,业务逻辑拆分到独立模块,`main.py` 仅负责组装与主循环。

**v4.2 架构**:3 块泵控 UNO(PUMP_A/B/C,各控 3 泵 + 3 阀 = 6 设备)+ 1 块灯箱 UNO(LIGHT),共 9 泵 9 阀 3 灯。泵控采用 RC 脉冲 + 继电器供电隔离模型。

```
┌─────────────────────────────────────────────────────┐
│                    main.py                          │
│              (Application 主循环 + 键盘交互)         │
└───────┬─────────────────────────────────────────────┘
        │
        │ 调用
        ▼
┌───────────────────────────────────────────────────────┐
│                     modules/                          │
├──────────────┬───────────────┬───────────────────────┤
│   camera     │ pose_detector │     visualizer        │
│ (摄像头采集) │  (Pose 推理)  │ (骨骼+FPS+文字绘制)   │
├──────────────┼───────────────┼───────────────────────┤
│   action_    │   state_      │     serial_sender     │
│  recognizer  │   machine     │ (PumpGroup+Light发送) │
└──────────────┴───────────────┴───────────────────────┘
        │
        │ 读取参数
        ▼
┌───────────────────────────────────────────────────────┐
│                   config.py                          │
│         (所有阈值 / 端口 / 开关集中配置)              │
└───────────────────────────────────────────────────────┘
```

## 模块职责

### config.py
集中配置文件。所有阈值、摄像头参数、串口端口(`PUMP_BOARDS` / `LIGHT_SERIAL_PORT`)、动作冷却、状态机参数等都集中在此,业务代码通过 `import config` 引用。修改参数无需改动业务代码。

### modules/camera.py — `Camera` 类
封装 `cv2.VideoCapture`:
- `open()`:打开摄像头并设置分辨率、帧率
- `read()`:读取一帧
- `switch(new_id)`:切换到另一个摄像头(用于按 c 键)
- `release()`:释放资源

### modules/pose_detector.py — `PoseDetector` 类
封装 MediaPipe Pose 推理:
- `process(frame_bgr)`:输入 BGR 帧,返回 `PoseResult`
- 内部用 `deque` 缓存最近 N 帧关键点,取平均实现 **时序平滑**
- 输出 `LandmarkPoint(x, y, z, visibility)` 列表(33 个)

### modules/action_recognizer.py — `ActionRecognizer` 类
基于关键点坐标判断 3 种手部动作 + `HAND_NONE`:
- `LEFT_HAND_UP`:用户左手举起(镜像后 = MediaPipe RIGHT_WRIST 高于鼻子)
- `RIGHT_HAND_UP`:用户右手举起(镜像后 = MediaPipe LEFT_WRIST 高于鼻子)
- `BOTH_HANDS_UP`:双手举起
- `HAND_NONE`:无动作

接口:
- `recognize(pose_result)`:返回触发的 `ActionEvent` 或 None(受冷却限制)
- `recognize_current(pose_result)`:返回 `HandActionState`(每帧实时,不受冷却限制,供状态机使用)
- `check_match(target, hand)`:返回 `MATCH_CORRECT` / `MATCH_WRONG`(供状态机 COUNTING 判定)
- `reset()`:清空冷却与历史

> **镜像约定**:主循环执行 `cv2.flip(frame, 1)` 水平翻转画面,因此 MediaPipe 的 LEFT_WRIST 实际对应用户右手,RIGHT_WRIST 对应用户左手。判定时交换两者,使识别结果与用户直觉一致。

### modules/state_machine.py — `StateMachine` 类
9 状态有限状态机(含 SAFE_STOP 安全停止态),控制 Arduino 气泵 + 灯箱交互流程:

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

- 使用 `time.monotonic()` 计时(避免系统时间被校准影响倒计时)
- `update(pose_result, hand_action)`:每帧调用,返回 `StateSnapshot`
- `reset()`:重置到 INIT(注意:SAFE_STOP 态下不应通过按键 r 调用,需在 main.py 按键处理中阻止)
- 人离开 ≥ n4 秒触发安全放气(STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL)
- gass 达 GAS_MAX 后锁定充气,状态机继续流转,DEFLATING 后恢复
- 任一泵控板发送失败 → 进入 SAFE_STOP(best-effort STOP_ALL + 放气后等待退出)

### modules/serial_sender.py — 串口发送模块
包含 3 个类:

**`PumpSender`(继承 SerialSender)**:单块泵控 UNO 串口
- `connect(expected_board_id, ready_timeout)`:打开串口并读取 READY 校验板号
- `send_and_wait(message, expected_board_id, accepted_commands, response_timeout)`:发送命令并等待 ACK/ERR
- `send_inflate_all(seconds)` / `send_deflate_all(seconds)` / `send_inflate_m()` / `send_stop_all()`

**`LightSender`(继承 SerialSender)**:灯箱 UNO 串口
- `send_light_on(id)` / `send_light_off(id)` / `send_all_off()` / `send_flash(times)`
- `light_id_for_action(action_name)`:动作 → 灯号映射

**`PumpGroupSender`**:泵组发送器,管理 3 块泵控 UNO
- `connect_all()`:连接 3 板并校验板号(任一失败返回 False)
- `send_inflate_all(seconds)` / `send_deflate_all(seconds)` / `send_inflate_m()`:三板广播,任一板失败 → `stop_all_best_effort()` + 返回 False(状态机进 SAFE_STOP)
- `send_stop_all()`:best-effort 广播 STOP_ALL(不进 SAFE_STOP,避免递归)
- `send_deflate_all_best_effort(seconds)`:仅供 SAFE_STOP 使用,部分失败不再 STOP_ALL(避免取消正常板放气)
- `test_mode=True`(SERIAL_ENABLED=False):跳过所有串口发送并返回成功,状态机可正常流转

### modules/visualizer.py — `Visualizer` + `FPSCounter` 类
负责所有画面绘制:
- 用 MediaPipe `drawing_utils` 画骨骼连线
- 用不同颜色画 33 个关键点(躯干/四肢/手/脚)
- 左上角 FPS(平滑滤波)
- 右上角人数
- 状态机面板(状态名 / 计时进度条 / 充气量 / 灯泡亮灭 / 充气锁定提示)
- `toggle_skeleton()`:按 f 切换骨骼

## 数据流

主循环每帧的数据流:

```
[摄像头] BGR 帧
   │
   ▼ cv2.flip 水平镜像
[Camera.read] ──> frame
   │
   ▼
[PoseDetector.process] ──> PoseResult(33 landmarks, 平滑)
   │
   ▼
[ActionRecognizer.recognize_current] ──> HandActionState(每帧实时)
   │
   ▼
[StateMachine.update] ──> StateSnapshot
   │        │
   │        └──> [PumpGroupSender.send_xxx] (3 板广播)
   │        └──> [LightSender.send_xxx]    (灯箱)
   ▼
[Visualizer.draw] ──> 带骨骼 + FPS + 状态面板的输出帧
   │
   ▼
[cv2.imshow] 显示
   │
   ▼
[cv2.waitKey] 处理键盘(q/s/f/c/r)
```

## 串口协议

### 泵控板(3 板:PUMP_A / PUMP_B / PUMP_C)

| 指令 | 含义 | 参数约束 |
|------|------|---------|
| `INFLATE_ALL,a` | 全部 3 泵充气 a 秒 | 0 < a ≤ 30,否则 `ERR,<板号>,BAD_DURATION` |
| `DEFLATE_ALL,b` | 全部 3 阀打开放气 b 秒 | 0 < b ≤ 30,否则 `ERR,<板号>,BAD_DURATION` |
| `INFLATE_M` | 9 泵同步点充,每泵独立时长 | — |
| `STOP_ALL` | 立即停止全部 6 设备(模式互斥优先级最高) | — |
| `STATUS` | 查询当前板状态 | 返回 `STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx` |
| `TEST_PUMP,i,t` | 测试第 i 号泵(0..2),持续 t 秒 | 0 < t ≤ 5 |

### 响应格式

| 响应 | 含义 |
|------|------|
| `READY,<板号>,<点充时长1>,<点充时长2>,<点充时长3>` | 上电就绪(含本板 INFLATE_M_MS_PER_PUMP 数值,用于核对烧录参数) |
| `ACK,<板号>,<命令>` | 指令执行成功 |
| `ERR,<板号>,<原因>` | 指令拒绝/失败 |
| `STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx` | 状态查询响应 |

### 灯箱(LIGHT, COM4)

| 指令 | 含义 |
|------|------|
| `LIGHT_ON,id` | 点亮灯 id(1/2/3) |
| `LIGHT_OFF,id` | 熄灭灯 id |
| `LIGHT_ALL_OFF` | 全部熄灭 |
| `LIGHT_FLASH,3` | 三灯闪烁 3 次 |

响应:`ACK,LIGHT,<命令>` / `ERR,LIGHT,<原因>`

## 关键设计决策

### 1. 参数集中化(config.py)
所有阈值、端口、开关集中到 `config.py`,避免"魔法数字"散落业务代码,便于调参与部署。

### 2. 模块单一职责
每个模块只做一件事:摄像头管采集、Pose 管推理、动作识别管判定、状态机管流程、串口管发送。模块间通过 `PoseResult` / `HandActionState` / `StateSnapshot` 数据类耦合,无强依赖。

### 3. 时序平滑(deque)
关键点坐标逐帧抖动明显,用 `deque(maxlen=5)` 缓存最近 5 帧取平均,显著降低抖动,代价是 1-2 帧延迟(约 30-60ms,人眼无感)。

### 4. 动作冷却
同一动作 1 秒内只触发一次,避免举手时持续输出 30 次/秒的事件。冷却时间可在 `config.py` 调整。

### 5. 镜像交换(画面翻转)
主循环执行 `cv2.flip(frame, 1)` 水平翻转画面,使画面与用户直觉一致(镜子效果)。代码中 LEFT_WRIST / RIGHT_WRIST 交换判定:MediaPipe 的 LEFT_WRIST 实际对应用户右手。

### 6. 单调时钟计时
状态机使用 `time.monotonic()` 而非 `time.time()`,避免 Windows 系统时间被 NTP 校准或手动修改时影响充放气倒计时。

### 7. 串口容错与板身份校验
- `SERIAL_ENABLED=False`:测试模式,跳过所有串口发送,状态机正常流转
- `SERIAL_ENABLED=True`:严格门禁,3 板泵控必须全部连接且 READY 板号匹配才进入运行态
- 任一泵控板发送失败 → 全组进入 SAFE_STOP(广播 STOP_ALL + 放气后等待退出)

### 8. SAFE_STOP 安全机制
- 任一泵控板 `INFLATE_ALL` / `DEFLATE_ALL` / `INFLATE_M` 失败 → 进入 SAFE_STOP
- SAFE_STOP 中使用 `send_deflate_all_best_effort()`(部分失败不再 STOP_ALL,确保正常板持续放气)
- SAFE_STOP 不自动恢复,等待用户按 q 退出
- SAFE_STOP 态下按 r 不应重置(在 main.py 按键处理中阻止)

### 9. 异常分级处理
- **致命错误**(摄像头打不开、MediaPipe 初始化失败、泵控板未全连):退出码非 0
- **可恢复错误**(推理异常、串口断开):警告并跳过,继续运行
- **用户中断**(Ctrl+C、q):优雅退出,先 STOP_ALL 再关闭串口

## 扩展点

如需修改动作识别(当前仅 3 种手部动作):
1. 在 `action_recognizer.py` 的 `ACTION_DISPLAY_NAMES` 加中文名
2. 在 `_detect_hand_action()` 和 `_detect_hand_candidates()` 中加判定逻辑
3. 注意镜像交换约定(LEFT_WRIST = 用户右手)

如需新增可视化元素:
- 在 `Visualizer.draw()` 中调用新的 `_draw_xxx()` 方法
