# 架构说明

本文档说明项目的整体架构、模块职责与数据流。

## 总体架构

项目采用 **分层 + 模块化** 架构,所有可调参数集中在 `config.py`,业务逻辑拆分到 6 个独立模块,`main.py` 仅负责组装与主循环。

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
│   action_    │     angle_    │     serial_sender     │
│  recognizer  │   calculator  │   (串口发送,可选)    │
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
集中配置文件。所有阈值、摄像头参数、串口端口、动作冷却等都集中在此,业务代码通过 `import config` 引用。修改参数无需改动业务代码。

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
基于关键点坐标判断 6 种动作:
- `recognize(pose_result)`:返回触发的 `ActionEvent` 或 None
- 维护每动作的 **冷却时间**(避免重复触发)
- 维护最近 3 个动作事件列表(用于屏幕底部显示)
- `reset()`:清空冷却与历史

### modules/angle_calculator.py — 角度计算工具
纯函数模块,计算三点夹角(余弦定理)。主要用于膝关节(髋-膝-踝)角度。

### modules/visualizer.py — `Visualizer` + `FPSCounter` 类
负责所有画面绘制:
- 用 MediaPipe `drawing_utils` 画骨骼连线
- 用不同颜色画 33 个关键点(躯干/四肢/手/脚)
- 左上角 FPS(平滑滤波)
- 右上角人数
- 底部最近 3 个动作 + 当前高亮动作
- `toggle_skeleton()`:按 f 切换骨骼

### modules/serial_sender.py — `SerialSender` 类
异步串口发送:
- `send_action(action_name)`:按协议 `POSE,动作名\n` 发送
- 线程安全(`threading.Lock`)
- 串口打不开时 **不崩溃**,仅警告继续运行

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
[ActionRecognizer.recognize] ──> ActionEvent? (含冷却判断)
   │                          │
   │                          └──> [SerialSender.send_action] (可选)
   ▼
[Visualizer.draw] ──> 带骨骼 + FPS + 动作文字的输出帧
   │
   ▼
[cv2.imshow] 显示
   │
   ▼
[cv2.waitKey] 处理键盘(q/s/f/c/r)
```

## 类图

```
┌─────────────────────┐
│      Camera         │
├─────────────────────┤
│ - camera_id: int    │
│ - width: int        │
│ - height: int       │
│ - cap: VideoCapture│
├─────────────────────┤
│ + open() -> bool    │
│ + read() -> frame   │
│ + switch(id)        │
│ + release()         │
└─────────────────────┘

┌─────────────────────┐         ┌────────────────────┐
│   PoseDetector      │         │   LandmarkPoint   │
├─────────────────────┤         ├────────────────────┤
│ - pose: mp.Pose     │ 1──33 * │ + x: float        │
│ - _buffer: deque    │────────>│ + y: float        │
├─────────────────────┤         │ + z: float        │
│ + process(frame)    │         │ + visibility      │
│ + reset_smoothing() │         └────────────────────┘
│ + close()           │
└─────────────────────┘
        │
        │ 输出
        ▼
┌─────────────────────┐
│     PoseResult      │
├─────────────────────┤
│ + landmarks: List   │  (平滑)
│ + raw_landmarks     │  (原始)
│ + person_detected   │
└─────────────────────┘

┌─────────────────────┐         ┌────────────────────┐
│ ActionRecognizer   │ 1──1 *  │   ActionEvent      │
├─────────────────────┤────────>├────────────────────┤
│ - cooldown: float   │         │ + name: str        │
│ - _last_trigger     │         │ + timestamp: float │
│ - recent_actions    │         │ + display_name     │
├─────────────────────┤         └────────────────────┘
│ + recognize(pose)   │
│ + reset()           │
└─────────────────────┘

┌─────────────────────┐         ┌────────────────────┐
│    Visualizer       │         │    FPSCounter      │
├─────────────────────┤         ├────────────────────┤
│ + show_skeleton     │         │ - _frame_times     │
│ - fps_counter       │ 1──1 *  ├────────────────────┤
│ - _active_event     │────────>│ + tick() -> float  │
├─────────────────────┤         └────────────────────┘
│ + draw(frame,...)   │
│ + toggle_skeleton() │
│ + set_active_action│
└─────────────────────┘

┌─────────────────────┐
│   SerialSender      │
├─────────────────────┤
│ - port: str         │
│ - baudrate: int     │
│ - serial_conn       │
│ - _lock: Lock       │
├─────────────────────┤
│ + connect() -> bool │
│ + send(msg)         │
│ + send_action(name) │
│ + close()           │
└─────────────────────┘
```

## 关键设计决策

### 1. 参数集中化(config.py)
所有阈值、端口、开关集中到 `config.py`,避免"魔法数字"散落业务代码,便于调参与部署。

### 2. 模块单一职责
每个模块只做一件事:摄像头管采集、Pose 管推理、动作识别管判定、可视化管绘制。模块间通过 `PoseResult` / `ActionEvent` 数据类耦合,无强依赖。

### 3. 时序平滑(deque)
关键点坐标逐帧抖动明显,用 `deque(maxlen=5)` 缓存最近 5 帧取平均,显著降低抖动,代价是 1-2 帧延迟(约 30-60ms,人眼无感)。

### 4. 动作冷却
同一动作 1 秒内只触发一次,避免举手时持续输出 30 次/秒的事件。冷却时间可在 `config.py` 调整。

### 5. 动作优先级
单帧可能同时满足多个动作(如站立+举手)。按优先级 `跌倒 > 双手 > 单手 > 站坐` 只触发最高优先级动作,避免一次输出多个事件干扰下游(Arduino)。

### 6. 串口容错
串口是可选外设,失败不应影响主流程。`SerialSender` 内部捕获所有异常,失败仅 `logger.warning`,主循环不感知。

### 7. 异常分级处理
- **致命错误**(摄像头打不开、MediaPipe 初始化失败):退出码 1
- **可恢复错误**(推理异常、串口断开):警告并跳过,继续运行
- **用户中断**(Ctrl+C、q):优雅退出,释放所有资源

## 扩展点

如需新增动作:
1. 在 `action_recognizer.py` 的 `ACTION_DISPLAY_NAMES` 加中文名
2. 在 `_detect_candidates()` 中加判定逻辑,append 到 candidates
3. 注意优先级顺序(位置靠前优先级高)

如需新增可视化元素:
- 在 `Visualizer.draw()` 中调用新的 `_draw_xxx()` 方法
