# MediaPipe Pose × Arduino 系统最新代码复查与泵阀实机问题报告

> 仓库：<https://github.com/012dim/mediapipe_pose_py>  
> 本次复查提交：`c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf`  
> 提交时间：2026-08-18 15:11:09（UTC+8）  
> 提交说明：`v4.2.1: 修复P1/P2/P3级问题 + 扩展测试覆盖率`  
> 复查日期：2026-08-18  
> 报告范围：Python 主程序、串口通信、状态机、灯箱固件、泵控固件、自动化测试、Trae CN 遗留事项，以及本轮单 UNO 实机异常  

---

## 1. 最终结论

最新提交的软件修改总体有效：

- 灯箱固件已经拆成独立可编译的 `.ino`。
- 三板 ACK 已改为公平轮询，A 板无响应不会再阻塞 B/C 的 ACK 收集。
- 摄像头切换失败能够显式恢复旧摄像头 ID。
- READY 五字段校验、STOP ACK 消费、SAFE_STOP 放气、实时动作判断等上一轮功能仍然保留。
- Python 3.12 隔离环境中 `64` 项自动化测试全部通过。
- MediaPipe 合成帧推理和可视化绘制通过。

但是，本轮真实硬件测试出现了两个直接影响安全和功能的现象：

1. 串口程序已经输出 `DONE,PUMP,1,300`，但实体气泵没有停止。
2. 气泵充气时电磁阀同时放气，气球不能有效变大。

这两项现象意味着：**当前不能继续进行三板带载、Python 主程序带载或 `GAS_MAX=15` 标定。**

目前系统状态应当定义为：

> **Python 软件复测通过；真实泵阀控制链未通过，属于实机联调阻断状态。**

在确认继电器接线、触发电平、电子开关信号协议、停止脉宽和电磁阀常开/常闭逻辑之前，不应继续给完整气路通电。

### 1.1 风险等级

| 等级 | 问题 | 当前结论 |
|---|---|---|
| P0 实机阻断 | `DONE`/`STOP` 后气泵仍运行 | 必须先解决，禁止继续扩大带载测试 |
| P0 实机阻断 | 充气时电磁阀同时放气 | 必须确认阀的物理逻辑并修改控制抽象 |
| P1 固件风险 | 当前泵控固件仍假设 RC 1500/2000 μs、低触发、阀断电关闭 | 最新提交没有修改该固件，实物已对这些假设提出反证 |
| P2 Python 可靠性 | `in_waiting`/`readline()` 异常没有逐板捕获 | USB 突然拔出时可能让广播方法直接抛异常 |
| P2 Arduino 协议 | Arduino 使用宽松 `toInt()`/`toFloat()` 解析 | 畸形指令可能被部分解析为有效值，应改为严格解析 |
| P3 文档 | 仓库内旧复查报告已过期，README 仍有一处旧说明 | 不影响运行，但会误导烧录和验收 |

---

## 2. 本次复查方法

本次复查没有直接沿用上一份报告，而是重新获取 GitHub 当前最新提交并进行以下检查：

1. 获取最新 commit、提交时间、提交说明和变更统计。
2. 对比上一轮复查提交 `7d784f2` 与最新提交 `c15a9b0`。
3. 在全新 Python 3.12 虚拟环境安装 `requirements.txt`。
4. 运行 `pip check` 检查依赖冲突。
5. 运行 `python -m compileall` 检查 Python 语法。
6. 导入 `main` 和核心依赖，确认入口可加载。
7. 运行全部 pytest。
8. 单独运行串口与摄像头测试。
9. 使用 coverage 检查各模块覆盖率。
10. 使用 Ruff 检查未定义名称、未使用导入、类型和规范问题。
11. 使用 640×480 黑色合成帧运行 MediaPipe Pose 和 Visualizer 冒烟测试。
12. 沿 `Python → 串口命令 → Arduino 解析 → 继电器/Servo → 泵/阀` 检查真实控制链。
13. 将本轮串口实机现象与正式仓库协议逐项比对。
14. 对照 Arduino 官方 Servo 和 UNO 资料判断 RC 脉宽与占空比 PWM 的区别。

---

## 3. 最新提交与变更范围

### 3.1 最新提交

```text
c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf
v4.2.1: 修复P1/P2/P3级问题 + 扩展测试覆盖率
```

相对上一轮提交 `7d784f2`：

```text
11 files changed, 1891 insertions(+), 49 deletions(-)
```

其中 `1252` 行来自加入仓库的上一版复查报告，因此不能把全部新增行都视为新功能代码。

### 3.2 本次提交主要修改

| 修改 | 文件 | 复查结论 |
|---|---|---|
| 灯箱独立 `.ino` | `arduino_commands/lightbox_uno_v4_2/lightbox_uno_v4_2.ino` | 已完成 |
| 三板公平 ACK 轮询 | `modules/serial_sender.py` | 基本正确，测试通过 |
| 摄像头失败回退 | `main.py`、`modules/camera.py` | 已修复，测试通过 |
| READY 注释与文档 | `config.py`、README、docs | 大部分已更新，仍有少量旧说明 |
| 摄像头测试 | `tests/test_camera.py` | 新增 9 项 |
| 串口公平性测试 | `tests/test_serial_sender.py` | 已覆盖 A 无响应、B/C 成功场景 |

### 3.3 关键事实：泵控固件没有在本次提交中更新

当前泵控文件：

```text
arduino_commands/pump_uno_v4_2.ino
```

该文件最近一次修改仍然来自：

```text
bf25875ce0e4f8c35c23af0454707b1f55a8f979
2026-08-18 11:55:21 +08:00
v4.2: 串口可靠性增强 + 测试体系重建 + Arduino READY/ACK 握手
```

因此，最新提交虽然修复了 Python、灯箱和文档问题，但**没有针对刚刚发现的气泵不停止、电磁阀同时放气问题修改泵控代码**。

---

## 4. 实际软件测试结果

### 4.1 环境

| 项目 | 实际结果 |
|---|---|
| Python | 3.12 |
| MediaPipe | 0.10.21 |
| OpenCV | 4.9.0 |
| NumPy | 1.26.4 |
| Pillow | 10.4.0 |
| PySerial | 3.5 |
| pytest | 8.4.2 |

### 4.2 安装与运行检查

| 检查 | 结果 |
|---|---|
| `pip install -r requirements.txt` | 通过 |
| `pip check` | `No broken requirements found.` |
| `python -m compileall -q .` | 通过 |
| `import main` | 通过 |
| 全部 pytest | `64 passed, 2 warnings in 4.57s` |
| 串口＋摄像头定向测试 | `42 passed, 2 warnings in 4.49s` |
| MediaPipe 黑帧推理 | 通过，`person_detected=False` |
| Visualizer 绘制 | 通过，输出 `(480, 640, 3) uint8` |
| Ruff 未定义名称 `F821` | 0 项 |
| Arduino CLI 编译 | 未执行；当前环境无 Arduino 工具链 |
| 实际摄像头设备 | 未测试；使用 mock 和合成帧验证 |
| 四块 UNO 实机 | 未测试；用户当前只完成单 UNO 初步测试 |

两条 warning 来自 protobuf 对未来 Python 3.14 的弃用提示，对当前 Python 3.10～3.12 运行没有直接影响。

### 4.3 测试分布

| 测试文件 | 数量 |
|---|---:|
| `test_action_recognizer.py` | 11 |
| `test_camera.py` | 9 |
| `test_serial_sender.py` | 33 |
| `test_state_machine.py` | 11 |
| 合计 | 64 |

### 4.4 覆盖率

| 模块 | 覆盖率 |
|---|---:|
| `action_recognizer.py` | 94% |
| `camera.py` | 75% |
| `pose_detector.py` | 41% |
| `serial_sender.py` | 75% |
| `state_machine.py` | 87% |
| `visualizer.py` | 25% |
| 总计 | 65% |

上一轮总覆盖率约为 55%，本次提升主要来自新增摄像头测试和串口公平轮询测试。

### 4.5 Ruff 结果

Ruff 共报告 89 项，主要是：

- 未使用导入或变量；
- 旧式 `typing.List/Optional/Set`；
- import 排序；
- 无效 `noqa`；
- 行过长和日志格式；
- 测试中的未使用变量。

没有发现 `F821` 未定义名称。89 项不代表 89 个运行错误，但建议后续分批清理，避免真实问题被噪声掩盖。

---

## 5. 已确认修复的代码问题

## 5.1 灯箱已经有独立 `.ino`

新增文件：

```text
arduino_commands/lightbox_uno_v4_2/
└── lightbox_uno_v4_2.ino
```

现在可以直接用 Arduino IDE 打开，不需要从 `.txt` 中复制代码。

灯箱使用 `millis()` 非阻塞闪烁：

```cpp
void startFlash(int times) {
  flashRemaining = times;
  flashActive = true;
  flashOnPhase = true;
  flashPhaseStart = millis();
  allOn();
}

void updateFlash() {
  if (!flashActive) return;
  // 根据 millis() 推进亮/灭阶段
}
```

收到闪烁命令时：

```cpp
startFlash(times);
sendACK("LIGHT_FLASH");
```

启动闪烁不再被多次 `delay()` 阻塞，ACK 能够立即返回；`LIGHT_ALL_OFF` 也可以中断闪烁。

结论：**代码问题已修复，仍必须把该 `.ino` 烧录到灯箱 UNO 后实测。**

---

## 5.2 三板 ACK 公平轮询已经实现

旧版按 A→B→C 调用阻塞读取。如果 A 没有响应，A 可能占满共享 deadline，导致已经回复的 B/C 也被判失败。

新版先写入三块板，再在共同 deadline 中检查每块串口的 `in_waiting`：

```python
pending = {bid for bid in self.board_ids if results[bid] is True}
deadline = time.monotonic() + response_timeout

while pending and time.monotonic() < deadline:
    for board_id in tuple(pending):
        conn = self.boards[board_id].serial_conn
        if not getattr(conn, "in_waiting", 0):
            continue
        line = conn.readline()
        parsed = sender._parse_response_line(...)
```

该方法达到以下效果：

- 三板命令仍然先连续写入；
- 无数据的板不会阻塞有数据的板；
- 旧 ACK、READY、STATUS 会被消费后继续等待；
- 三板共用同一个 0.8 秒上限。

测试已覆盖：A 无响应，B/C 正常 ACK，结果应为 A=False、B=True、C=True。

结论：**上一轮 ACK 饥饿问题已修复。**

---

## 5.3 摄像头失败回退已经修复

`Camera.open()` 在 DirectShow 打开失败后先释放旧实例：

```python
self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
if not self.cap.isOpened():
    self.cap.release()
    self.cap = cv2.VideoCapture(self.camera_id)
```

`main._switch_camera()` 在切换前保存旧 ID：

```python
old_id = self.camera.camera_id
if self.camera.switch(new_id):
    return
if not self.camera.switch(old_id):
    logger.error("恢复摄像头也失败")
```

相关 9 项摄像头测试通过。

结论：**上一轮摄像头回退问题已修复。**

---

## 5.4 READY 五字段严格门禁仍然有效

泵控 UNO 上电输出：

```text
READY,<板号>,<时长1>,<时长2>,<时长3>
```

例如：

```text
READY,PUMP_A,300,500,800
```

Python 正式模式使用：

```python
self.pump_group.connect_all(
    expected_inflate_m_ms=config.INFLATE_M_MS_PER_BOARD,
)
```

以下任一情况都会拒绝进入运行态：

- 板号错误；
- READY 缺少三路时长；
- 时长不是整数；
- Arduino 三路时长与 Python 配置不一致；
- READY 超时。

结论：**代码门禁有效，但门禁只能证明“参数字符串一致”，不能证明物理泵阀方向和停止功能正确。**

---

## 5.5 其他上一轮结论仍成立

- STOP ACK 会被读取，不再故意残留到下一条命令。
- SAFE_STOP 放气失败不会再次 STOP 正常放气板。
- 状态机使用 `recognize_current()` 实时动作，不受界面冷却影响。
- 错误动作或 `HAND_NONE` 立即进入 INFLATING。
- 第一次 `INFLATE_M` 会立即发送，此后每秒最多一次。
- `GAS_MAX=15` 后锁定充气，但状态机继续完成流程。
- 站立、坐下、跌倒不在当前动作池。

---

## 6. 刚刚单 UNO 测试现象的准确解释

用户提供的串口输出：

```text
ACK,PUMP,1,300
DONE,PUMP,1,300
EVENT,ARM_TIMEOUT,DISARMED
```

并观察到：

- 气泵确实充气；
- 电磁阀同时放气；
- 气球没有明显变大；
- 到时后气泵没有停止；
- `STOP` 也不能阻止气泵。

### 6.1 该输出不是仓库正式泵控固件的协议

仓库正式固件的单泵测试命令是：

```text
TEST_PUMP,0,0.3
```

成功只返回：

```text
ACK,PUMP_A,TEST_PUMP
```

正式固件不会输出：

```text
DONE,PUMP,...
EVENT,ARM_TIMEOUT,...
```

这些输出来自本轮临时的独立串口测试 sketch。该 sketch 当前不在 GitHub 仓库中，而且只控制气泵，没有控制电磁阀。

因此必须区分：

| 项目 | 临时测试 sketch | 仓库正式固件 |
|---|---|---|
| 单泵命令 | `PUMP,1,300` | `TEST_PUMP,0,0.3` |
| 泵编号 | 1～3 | 0～2 |
| 时长单位 | 毫秒 | 秒 |
| ARM | 有 | 无 |
| DONE | 有 | 无 |
| 阀控制 | 没有 | 有，但基于固定阀逻辑假设 |

### 6.2 `PUMP,1,300` 不是持续运行

该命令的含义是：

```text
泵1运行300毫秒 = 0.3秒
```

理论顺序：

```text
PUMP,1,300
→ ACK,PUMP,1,300
→ 0.3秒后停止
→ DONE,PUMP,1,300
```

`EVENT,ARM_TIMEOUT,DISARMED` 应在最后一次有效操作约 60 秒后出现，只代表测试权限自动锁定。

### 6.3 `DONE` 不等于物理气泵已经断电

`DONE` 只证明：

- Arduino 的 `millis()` 计时到期；
- 程序执行了停止函数；
- 串口打印了 DONE。

它不能证明：

- 继电器触点确实断开；
- 电子开关进入 OFF；
- PWM/RC 停止值正确；
- 气泵供电没有绕过继电器；
- 实体气泵已经停止。

本次现象正说明“程序状态”和“物理状态”不一致。

---

## 7. P0：气泵在 DONE/STOP 后仍运行

这是当前最高优先级问题。

### 7.1 可能原因

#### 原因 A：继电器使用了 NC，而不是 NO

正确的硬断电路径应当使用：

```text
外部电源正极 → COM
NO → 电子开关/气泵供电正极
```

如果使用 COM+NC，则继电器“关闭”时线路仍然导通，STOP 无法切断负载。

#### 原因 B：`RELAY_ACTIVE_LOW` 与实物相反

当前泵控固件固定：

```cpp
const bool RELAY_ACTIVE_LOW = true;
```

如果实际继电器为高电平触发，所有 ON/OFF 逻辑都会反向。

#### 原因 C：气泵供电绕过了继电器

如果继电器只控制指示线路或电子开关红线，而气泵动力电源存在另一条持续供电路径，Arduino 输出关闭也不能停止气泵。

#### 原因 D：电子开关不是 RC Servo 脉宽

当前代码使用：

```cpp
#include <Servo.h>
servos[i].writeMicroseconds(1500);  // 假设停止
servos[i].writeMicroseconds(2000);  // 假设启动
```

Arduino 官方 Servo 库用于 RC hobby servo 类型的控制脉冲，并不等同于 `analogWrite()` 占空比 PWM。官方文档还说明，在 UNO 等非 Mega 板上使用 Servo 库会占用计时器，并影响 D9、D10 的 `analogWrite()` PWM 功能。因此不能把“设备说明写 PWM”直接等价为当前 Servo 实现。

参考：

- Arduino Servo：<https://docs.arduino.cc/libraries/servo/>
- Arduino UNO R3：<https://docs.arduino.cc/hardware/uno-rev3/>

#### 原因 E：停止脉宽值错误

当前代码假定：

```text
1500 μs = OFF
2000 μs = ON
```

部分 RC 电子开关可能要求 1000 μs 为 OFF，或者使用完全不同的阈值。必须根据具体型号确认。

#### 原因 F：OFF 脉宽保持时间不足

正式固件的 `allOff()`：

```cpp
for (...) {
  setServoPulse(i, false);
}
delay(5);
for (...) {
  stopServo(i);
  setRelay(i, false);
}
```

这里只等待 5 ms。RC Servo 信号通常按周期发送；5 ms 不保证每个通道都实际输出过一帧新的 OFF 脉宽。`deviceOff()` 更是写入 OFF 后立即 `detach()`。

即使继电器应当提供最终硬断电，该时序仍建议重新设计，不能把“调用了 `writeMicroseconds(1500)`”等同于电子开关已经收到有效停止帧。

### 7.2 必须进行的无负载检查

先断开气泵动力负载，只保留 UNO 和继电器模块。

1. 上电后继电器指示灯必须为关闭。
2. 发送启动命令时，对应泵继电器只吸合设定时间。
3. 到时后继电器灯必须熄灭。
4. 发送 STOP 后继电器灯必须立即熄灭。
5. 使用万用表测量 COM/NO 输出：

```text
待机/STOP：0 V
运行期间：额定工作电压
到时后：重新回到 0 V
```

判断表：

| 结果 | 判断 |
|---|---|
| STOP 无 ACK | 串口指令未被接收，先检查 Newline 和波特率 |
| STOP 有 ACK，继电器灯仍亮 | 触发电平或代码引脚错误 |
| 继电器灯灭，但 COM/NO 仍有电 | 继电器接线或触点错误 |
| COM/NO 为 0 V，但气泵仍运行 | 气泵动力电源绕过继电器 |
| 硬断电正常，但只靠 S 线无法停止 | PWM 类型或 OFF 脉宽错误 |

### 7.3 推荐代码结构

不要只使用一个模糊的 `deviceOff()`。建议区分正常停止和紧急停止：

```cpp
void emergencyPumpOff(int pumpDevice) {
  // 安全优先：立即断开硬件供电
  setRelay(pumpDevice, false);
  stopServo(pumpDevice);
}

void normalPumpOff(int pumpDevice) {
  // 具体顺序必须根据电子开关说明书决定
  setServoPulse(pumpDevice, false);
  delay(RC_OFF_HOLD_MS);
  setRelay(pumpDevice, false);
  stopServo(pumpDevice);
}
```

只有确认电子开关需要 RC OFF 帧，并确认帧周期后，才能确定 `RC_OFF_HOLD_MS`。不要直接把 5 ms 当成已经验证的值。

---

## 8. P0：充气时电磁阀同时放气

### 8.1 临时测试 sketch 的直接原因

临时测试 sketch 只控制：

| 气泵 | 继电器 | S 信号 |
|---|---:|---:|
| 泵1 | D2 | D8 |
| 泵2 | D4 | D10 |
| 泵3 | D6 | D12 |

它没有控制正式系统中的阀通道：

| 阀 | 继电器 | S 信号 |
|---|---:|---:|
| 阀1 | D3 | D9 |
| 阀2 | D5 | D11 |
| 阀3 | D7 | D13 |

如果阀在无控制或断电状态下保持放气，临时测试程序必然出现“一边充气、一边放气”。

### 8.2 正式固件也存在尚未验证的阀假设

正式固件充气时执行：

```cpp
deviceOff(valveToDevice(i));
deviceOn(pumpToDevice(i));
```

放气时执行：

```cpp
deviceOff(pumpToDevice(i));
deviceOn(valveToDevice(i));
```

这等价于假设：

```text
阀 deviceOff = 关闭气路、保持气体
阀 deviceOn  = 打开放气
```

如果实物是：

```text
阀断电 = 放气
阀通电 = 关闭
```

那么正式固件的充气逻辑同样是反的，气球仍然不会变大。

### 8.3 必须确认的阀状态

需要分别确认：

1. 阀完全断电时，气路是开放还是关闭。
2. 继电器通电但 S 线为 OFF 时，气路状态。
3. S 线为 ON 时，气路状态。
4. 阀是否为常开、常闭、三通或锁存结构。
5. 阀的“打开”究竟是连接泵路、连接气囊路还是连接排气口。

### 8.4 推荐代码抽象

不要继续把阀当作普通“设备 ON/OFF”。应当明确表达物理目标：

```cpp
const bool VALVE_ENERGIZED_MEANS_OPEN = true;  // 必须由实测确定

void setValveOpen(int channel, bool open) {
  bool energize = VALVE_ENERGIZED_MEANS_OPEN ? open : !open;
  int device = valveToDevice(channel);

  if (energize) {
    deviceOn(device);
  } else {
    deviceOff(device);
  }
}
```

充气函数应写成：

```cpp
setValveOpen(channel, false);  // 明确要求关闭排气
delay(VALVE_SETTLE_MS);
setPumpRunning(channel, true);
```

放气函数应写成：

```cpp
setPumpRunning(channel, false);
setValveOpen(channel, true);
```

这样代码表达的是“阀开/阀关”，而不是把“通电”错误等价为“阀开”。

`VALVE_ENERGIZED_MEANS_OPEN` 和 `VALVE_SETTLE_MS` 必须通过实物确定，不能在报告中猜测。

---

## 9. Trae CN 遗留事项的最新判断

## 9.1 6.1：确认 RC 脉宽还是占空比 PWM

Trae CN 的问题完全成立，而且本轮 STOP 失效后优先级进一步提高。

当前仓库明确使用：

```cpp
#include <Servo.h>
Servo.writeMicroseconds(1500/2000)
```

这是 RC Servo 脉宽方案。

### 如果电子开关说明书是 RC/Servo pulse

应确认：

- 频率；
- OFF 脉宽；
- ON 脉宽；
- 输入电平；
- 是否需要持续脉冲；
- 是否锁存；
- 失去信号后的默认状态。

确认与 1500/2000 μs 一致后，才能继续使用 Servo 库。

### 如果说明书是占空比 PWM

当前实现必须重构：

- 不能继续使用 `Servo.writeMicroseconds()`；
- D8、D12、D13 不能直接作为 UNO 标准 `analogWrite()` PWM 输出；
- UNO 只有 6 路硬件 PWM 输出，而当前系统需要 6 路 S 信号加 6 路继电器；
- 可能需要重新分配引脚、使用外部 PWM 驱动器或更换控制板。

### 本轮通过条件

- [ ] 获得电子开关准确型号或说明书。
- [ ] 用示波器/逻辑分析仪测量 S 线。
- [ ] 确认启动与停止脉宽。
- [ ] 确认 STOP 后 S 线和继电器都进入安全状态。

---

## 9.2 6.2：哪些 UNO 需要重新烧录

### 灯箱 UNO

必须烧录：

```text
arduino_commands/lightbox_uno_v4_2/lightbox_uno_v4_2.ino
```

因为新固件才包含非阻塞闪烁。

### PUMP_A/B/C

原本的判断是：READY 五字段和 Python 配置一致即可决定是否重烧。

但结合本轮实机问题，最新判断应更严格：

> 即使 READY 五字段完全匹配，只要最终确认 PWM 类型、OFF 脉宽、继电器极性或阀逻辑需要修改，三块泵控 UNO 都必须烧录修订后的泵控固件。

READY 只能验证：

- 板号；
- 三个点充时间参数。

READY 不能验证：

- RC/占空比 PWM 类型；
- 继电器 NO/NC；
- 继电器高/低触发；
- 电磁阀常开/常闭；
- STOP 是否真正断电。

因此当前建议：

| 板卡 | 当前结论 |
|---|---|
| LIGHT | 必须烧录新 `.ino` |
| PUMP_A | 先完成单板修正和验证，预计需要重烧 |
| PUMP_B | 复制经过验证的控制逻辑，修改板号/时长后重烧 |
| PUMP_C | 复制经过验证的控制逻辑，修改板号/时长后重烧 |

---

## 9.3 10.2～10.8：实物测试仍然必须执行

Trae CN 的判断正确。现在应重新排列测试顺序，不能直接从单泵跳到三板。

正确顺序应是：

1. 单路继电器无负载。
2. 单路 S 线波形。
3. 单个电磁阀状态测试。
4. 单个气泵硬停止测试。
5. 单通道“泵＋阀”联合测试。
6. 单 UNO 三通道测试。
7. 三 UNO 无气囊同步测试。
8. 三 UNO 带气囊测试。
9. 灯箱测试。
10. Python 全流程。
11. `GAS_MAX` 标定。

---

## 10. 其他仍存在的代码问题

## 10.1 P2：公平 ACK 轮询没有捕获串口属性异常

当前代码直接执行：

```python
in_waiting = getattr(conn, "in_waiting", 0)
line = conn.readline()
```

真实 USB 串口被突然拔出时，`in_waiting` 属性和 `readline()` 都可能抛出异常。当前方法没有逐板 try/except，异常可能直接中断整个 `_send_all_and_collect()`。

建议：

```python
try:
    in_waiting = getattr(conn, "in_waiting", 0)
    if not in_waiting:
        continue
    line = conn.readline()
except Exception as exc:  # 生产代码可缩小到 SerialException/OSError
    logger.error("%s 串口读取失败: %s", board_id, exc)
    sender._connected = False
    results[board_id] = False
    pending.discard(board_id)
    continue
```

建议增加测试：

- A 的 `in_waiting` 抛异常；
- B/C 正常返回 ACK；
- 最终结果 A=False、B=True、C=True；
- 方法不向主循环抛异常。

---

## 10.2 P2：Arduino 数值解析不够严格

当前固件大量使用：

```cpp
String.toInt()
String.toFloat()
```

例如：

```cpp
int pumpIdx = line.substring(10, firstComma).toInt();
float seconds = line.substring(firstComma + 1).toFloat();
```

这种写法无法可靠区分完整合法数字与包含多余字符的输入。对会启动气泵的命令，应使用严格解析：

```cpp
bool parseStrictUInt(const String &text, unsigned long &value) {
  if (text.length() == 0) return false;
  unsigned long result = 0;
  for (unsigned int i = 0; i < text.length(); i++) {
    char c = text.charAt(i);
    if (c < '0' || c > '9') return false;
    result = result * 10UL + (unsigned long)(c - '0');
  }
  value = result;
  return true;
}
```

更建议把测试时长统一改成整数毫秒，避免 Arduino AVR 上的浮点解析：

```text
TEST_PUMP_MS,0,300
```

而不是：

```text
TEST_PUMP,0,0.3
```

灯箱的灯号和闪烁次数也建议使用相同的严格整数解析函数。

---

## 10.3 P3：README 仍有一处旧说明

README 配置表仍写：

```text
INFLATE_M_MS_PER_BOARD：仅供 Python 端记录
```

但 `config.py` 和实际代码已经把它用于 READY 严格启动门禁。README 应改为：

```text
Python 端期望值；SERIAL_ENABLED=True 时与 Arduino READY 三路时长严格比对
```

---

## 10.4 P3：仓库内旧复查报告已经过期

仓库中的：

```text
mediapipe_pose_py_v4.2.1_recheck_report.md
```

仍对应旧提交 `7d784f2`，并写着：

- 51 项测试；
- 灯箱没有独立 `.ino`；
- ACK 公平轮询未修；
- 摄像头回退未修。

这些内容已经被 `c15a9b0` 改变。

建议：

- 将旧报告放入 `docs/history/` 并在标题注明“历史报告”；或
- 用本次报告替换旧报告；或
- 在旧报告顶部增加醒目的过期提示和新报告链接。

---

## 10.5 P3：架构文档门禁描述不完全一致

`docs/ARCHITECTURE.md` 的某处仍只写：

```text
READY 板号匹配
```

应统一为：

```text
READY 板号和三路 INFLATE_M 时长均严格匹配
```

---

## 11. 推荐的单板测试固件方法

下一版单板测试固件不应只测试泵，应覆盖泵、阀和硬停止。

建议新增仓库文件：

```text
arduino_commands/pump_uno_single_board_test/
└── pump_uno_single_board_test.ino
```

### 11.1 推荐指令

```text
HELP
STATUS
ARM
TEST_RELAY,<device>,<ms>
TEST_SIGNAL,<device>,<off_us>,<on_us>,<ms>
VALVE_OPEN,<channel>
VALVE_CLOSE,<channel>
INFLATE_CHANNEL,<channel>,<ms>
STOP_PUMPS
SAFE_VENT
DISARM
```

### 11.2 必须输出的状态

```text
ACK,<command>
DONE,<command>
STATUS,armed=...,pump=...,valve=...,relay=...,signal=...
ERR,<reason>
```

### 11.3 重要原则

- `ACK` 只表示命令被接受。
- `DONE` 只表示软件计时结束。
- 是否真正停止必须由继电器电压、S 线波形和实体动作验证。
- 单次测试最大时间应从 300～500 ms 开始，而不是直接允许 5 秒。
- ARM 超时必须关闭泵，并按已确认的安全阀状态处理。
- STOP 必须有硬件断电路径，不能只依赖 PWM 信号。

---

## 12. 修订后的实机测试清单

## 12.1 阶段 0：立即停止当前测试

- [ ] 断开气泵独立电源。
- [ ] 释放或拆下气球，避免继续积压。
- [ ] 不运行 Python 主程序。
- [ ] 不连接其他两块泵控 UNO。
- [ ] 记录当前继电器 COM/NO/NC 接线。
- [ ] 记录电子开关和电磁阀型号。

## 12.2 阶段 1：单路继电器无负载

- [ ] 只连接 UNO 和继电器模块。
- [ ] 上电所有继电器不吸合。
- [ ] 指定通道只吸合设定时间。
- [ ] 到时后指示灯熄灭。
- [ ] STOP 后立即熄灭。
- [ ] COM+NO 待机为断路。
- [ ] 改变 `RELAY_ACTIVE_LOW` 前保持负载断开。

## 12.3 阶段 2：S 线波形

- [ ] 用示波器/逻辑分析仪测量 D8。
- [ ] 测量启动脉宽。
- [ ] 测量停止脉宽。
- [ ] 测量信号周期。
- [ ] 对照电子开关说明书。
- [ ] 确认失去信号后的默认状态。
- [ ] 确认 D10、D12 同样正确。

## 12.4 阶段 3：电磁阀独立测试

- [ ] 阀完全断电时记录气路状态。
- [ ] 阀继电器通电、S OFF 时记录状态。
- [ ] S ON 时记录状态。
- [ ] 明确“阀开”是否表示排气。
- [ ] 明确常开/常闭/三通/锁存类型。
- [ ] 确认 STOP 时希望阀处于保持还是安全放气状态。

## 12.5 阶段 4：单个气泵停止验证

- [ ] 不接气球，或使用低风险测试气路。
- [ ] 运行 100 ms 后物理停止。
- [ ] 运行 200 ms 后物理停止。
- [ ] 运行 300 ms 后物理停止。
- [ ] 运行期间发送 STOP，物理立即停止。
- [ ] 拔掉 USB 后泵不会持续运行。
- [ ] Arduino 复位时泵不会误启动。
- [ ] 继电器和电子开关无异常发热。

## 12.6 阶段 5：单通道泵阀联合

- [ ] 先关闭排气阀，再启动泵。
- [ ] 充气期间没有同时放气。
- [ ] 泵停止后气囊能按设计保持。
- [ ] 放气命令只打开对应阀。
- [ ] SAFE_VENT 能停止泵并放气。
- [ ] 任何状态下紧急断电有效。

## 12.7 阶段 6：单 UNO 三通道

- [ ] 1/2/3 通道映射正确。
- [ ] 泵和阀没有交叉接错。
- [ ] TEST_PUMP 时对应阀状态正确。
- [ ] STOP_ALL 三泵全部停止。
- [ ] DEFLATE_ALL 三阀状态正确。
- [ ] READY 五字段正确。

## 12.8 阶段 7：三 UNO 同步

- [ ] PUMP_A/B/C 板号正确。
- [ ] 三组点充时长正确。
- [ ] Python READY 门禁通过。
- [ ] A 无响应时 B/C ACK 仍被正确读取。
- [ ] 任一板失联进入 SAFE_STOP。
- [ ] 在线板能够继续安全放气。
- [ ] 三板 STOP 都有物理效果。

## 12.9 阶段 8：灯箱

- [ ] 已烧录独立 `lightbox_uno_v4_2.ino`。
- [ ] 上电输出 `READY,LIGHT`。
- [ ] `LIGHT_FLASH,3` 立即返回 ACK。
- [ ] 闪烁期间 `LIGHT_ALL_OFF` 可中断。
- [ ] 灯继电器触发电平正确。

## 12.10 阶段 9：Python 全流程

- [ ] `SERIAL_ENABLED=True`。
- [ ] 四块板全部连接。
- [ ] INIT 充气正确。
- [ ] 错误动作立即点充。
- [ ] 正确动作后泵物理停止。
- [ ] 人离开触发停止与放气。
- [ ] SAFE_STOP 物理有效。
- [ ] q/Ctrl+C 后泵停止、阀进入安全状态。

## 12.11 阶段 10：`GAS_MAX=15` 标定

只有前面全部通过后才允许执行。

保留用户原方案：

- 气囊规格相同；
- 每个通道点充时长不同；
- 按最大点充通道计算安全值；
- 使用统一 `GAS_MAX=15`。

最大通道理论累计泵运行时间：

$$
T_{max}=A+(GAS\_MAX-floor(A))\times\frac{m_{max}}{1000}
$$

默认：

```text
A = 5秒
GAS_MAX = 15
m_max = 900毫秒
```

则：

$$
T_{max}=5+(15-5)\times0.9=14秒
$$

但这只是泵运行时间预算，不是实际压力或体积。当前 STOP 和阀逻辑未通过前，该公式不能用于真实安全结论。

---

## 13. 推荐修改优先级

### 立即处理

1. 停止带载测试。
2. 确认继电器 COM/NO/NC 和触发电平。
3. 确认 STOP 是否返回 ACK，以及继电器灯/输出电压是否关闭。
4. 获取电子开关型号，确认 RC 或占空比 PWM。
5. 确认电磁阀断电/通电时的气路状态。

### 硬件逻辑确认后修改

1. 把泵与阀分成语义明确的控制函数。
2. 增加 `VALVE_ENERGIZED_MEANS_OPEN` 或等效硬件配置。
3. 重做正常停止与紧急硬断电顺序。
4. 使用经过验证的 OFF/ON 信号参数。
5. 增加单板泵阀联合测试固件。
6. 三块泵控 UNO 全部重新烧录验证后的固件。

### Python 与工程质量

1. 捕获公平轮询中的串口拔出异常。
2. 增加异常板不影响正常板的测试。
3. Arduino 命令改为严格数值解析。
4. 更新 README、架构文档和旧报告。
5. 分批清理 Ruff 提示。

---

## 14. 验收门禁

### 软件门禁

- [x] Python 依赖安装成功。
- [x] `pip check` 通过。
- [x] Python compileall 通过。
- [x] 64 项 pytest 通过。
- [x] ACK 公平轮询测试通过。
- [x] 摄像头回退测试通过。
- [x] MediaPipe/Visualizer 冒烟测试通过。
- [ ] Arduino 四份固件全部实际编译通过。

### 泵控门禁

- [ ] STOP 后继电器实际断电。
- [ ] STOP 后气泵实体停止。
- [ ] PWM/RC 类型已由说明书确认。
- [ ] ON/OFF 参数已由波形验证。
- [ ] 阀常开/常闭逻辑已确认。
- [ ] 充气时阀不会同时放气。
- [ ] 单通道 SAFE_VENT 有效。

### 系统门禁

- [ ] 单 UNO 三通道通过。
- [ ] 三 UNO 同步通过。
- [ ] 灯箱新固件通过。
- [ ] Python 全流程通过。
- [ ] `GAS_MAX=15` 实物标定通过。

---

## 15. 最终判断

最新提交 `c15a9b0` 已经正确完成上一轮报告中的 P1/P2/P3 软件修复，自动化测试结果也从 51 项增加到 64 项。就 Python、灯箱代码、ACK 公平轮询和摄像头回退而言，本次修改是有效的。

但刚刚的单 UNO 实测已经提供了比自动化测试更高优先级的证据：

- 软件输出 DONE 后气泵仍运行；
- STOP 无法阻止实体气泵；
- 充气时电磁阀同时排气。

因此当前不能再使用“没有 P0 问题”这一旧结论。准确结论应改为：

> **软件逻辑复查通过，但泵阀物理控制链存在 P0 级阻断问题。必须先完成继电器、PWM/RC、停止信号和阀状态的单路验证，再修改并重烧泵控固件；在此之前不得进行三板带载、主程序带载或 GAS_MAX 标定。**

---

## 16. 代码与资料链接

- 仓库：<https://github.com/012dim/mediapipe_pose_py>
- 本次提交：<https://github.com/012dim/mediapipe_pose_py/commit/c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf>
- Python 串口模块：<https://github.com/012dim/mediapipe_pose_py/blob/c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf/modules/serial_sender.py>
- 状态机：<https://github.com/012dim/mediapipe_pose_py/blob/c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf/modules/state_machine.py>
- 泵控固件：<https://github.com/012dim/mediapipe_pose_py/blob/c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf/arduino_commands/pump_uno_v4_2.ino>
- 灯箱固件：<https://github.com/012dim/mediapipe_pose_py/blob/c15a9b0b6809ede12ad4ac50746cfc8cb37f8eaf/arduino_commands/lightbox_uno_v4_2/lightbox_uno_v4_2.ino>
- Arduino Servo 官方文档：<https://docs.arduino.cc/libraries/servo/>
- Arduino UNO R3 官方文档：<https://docs.arduino.cc/hardware/uno-rev3/>

