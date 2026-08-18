# MediaPipe Pose × Arduino 控制系统 v4.2.1 代码复查与实机联调报告

> 仓库：<https://github.com/012dim/mediapipe_pose_py>  
> 复查提交：`7d784f2cb5eee60058fa99e8b24ab78b9efffd8a`  
> 提交说明：`v4.2.1: 串口可靠性修复 + 灯箱非阻塞闪烁 + 参数校验 + 测试扩展`  
> 复查日期：2026-08-18  
> 报告性质：静态代码审查、全新环境安装验证、自动化测试与硬件联调方案  

---

## 1. 复查结论

本次更新已经修复上一轮报告中的主要软件问题。就当前仓库提交而言：

- 未发现阻止进入硬件台架测试的 P0 级软件缺陷。
- Python 依赖可在全新 Python 3.12 环境安装，模块能够编译并导入。
- 自动化测试共 `51` 项，全部通过。
- `STOP_ALL` ACK 残留、SAFE_STOP 放气、READY 时长校验、动作冷却影响实时判断、灯箱阻塞闪烁等核心问题已按预期修改。
- 用户确认保留的方案已保留：`GAS_MAX=15`、错误动作立即触发第一次充气、每个气囊使用不同点充时长、删除站立/坐下/跌倒判定。

但是，当前结论只能表述为：**软件已具备进入实机联调的条件，尚不能表述为整机已经通过安全验收。**

正式通电前仍应处理或确认以下事项：

1. 查明电子开关要求的是 RC 舵机脉宽还是占空比 PWM；当前 Arduino 程序实现的是 RC 舵机脉宽。
2. 将灯箱固件从说明性 `.txt` 中拆成可直接编译上传的 `.ino` 文件。
3. 修复摄像头切换失败时不能真正切回旧摄像头的问题。
4. 确认三块泵控 UNO 的 `BOARD_ID` 和三组 `INFLATE_M_MS_PER_PUMP` 与 Python 配置逐项一致。
5. 执行单设备、单板、三板、灯箱和全流程硬件测试。

### 1.1 风险等级汇总

| 等级 | 数量 | 说明 |
|---|---:|---|
| P0 阻断软件缺陷 | 0 | 未发现会直接阻止软件进入台架测试的问题 |
| P1 部署问题 | 1 | 灯箱新固件没有独立 `.ino`，容易上传旧版或复制不完整 |
| P2 功能/可靠性问题 | 2 | 三板 ACK 顺序读取存在饥饿边界；摄像头失败回退错误 |
| P3 文档/工程质量 | 若干 | README、架构文档和排障文档部分协议说明已落后；测试覆盖不完整 |
| 实物待确认 | 3 类 | PWM 类型、电气方向与极性、气囊安全阈值及全流程行为 |

---

## 2. 本次复查范围与方法

### 2.1 复查范围

本次检查覆盖以下文件和功能：

- `main.py`：启动门禁、主循环、实时动作输入、摄像头切换、清理流程。
- `config.py`：气泵参数、串口配置、状态机参数和 `GAS_MAX`。
- `modules/action_recognizer.py`：三种手部动作识别和动作匹配。
- `modules/state_machine.py`：九状态流程、立即惩罚充气、上限锁定、离场放气和 SAFE_STOP。
- `modules/serial_sender.py`：READY 校验、ACK/ERR 解析、三板广播与安全放气。
- `arduino_commands/pump_uno_v4_2.ino`：泵/阀、RC 脉宽、局部定时和 READY 参数。
- `arduino_commands/lightbox_uno_commands.txt`：灯箱指令和非阻塞闪烁。
- `tests/`：动作识别、串口协议和状态机测试。
- `README.md`、`docs/`：部署和排障说明的一致性。

### 2.2 使用的检查方法

本次不是只阅读代码，而是使用了以下方法交叉验证：

1. **提交差异检查**：确认最新提交修改了 10 个文件，变更量为 `+453/-100`。
2. **静态调用链检查**：沿 `main.py → ActionRecognizer → StateMachine → PumpGroupSender/LightSender → Arduino` 检查真实执行路径。
3. **全新环境安装**：使用全新的 Python 3.12.13 虚拟环境安装 `requirements.txt`，避免已有环境掩盖依赖错误。
4. **依赖一致性检查**：运行 `pip check`。
5. **语法编译检查**：运行 `python -m compileall`。
6. **入口导入检查**：直接导入 `main`。
7. **自动化测试**：运行全部 pytest，并单独复查 STOP/READY 相关测试。
8. **最小推理冒烟测试**：向 MediaPipe Pose 输入一张 640×480 黑色合成帧，并调用可视化绘制。
9. **覆盖率检查**：查看关键模块的实际测试覆盖情况。
10. **代码规范检查**：使用 Ruff 检查未定义符号、无效导入、过长行和其他工程问题。
11. **协议逐项比对**：比对 Python 发送命令、Python 接受的 ACK、Arduino 返回内容和 README 中的描述。

### 2.3 实际执行结果

| 检查项 | 结果 |
|---|---|
| Python | 3.12.13 |
| `pip install -r requirements.txt` | 通过 |
| `pip check` | `No broken requirements found.` |
| `python -m compileall` | 通过 |
| `import main` | 通过 |
| 全部 pytest | `51 passed, 2 warnings in 2.84s` |
| STOP/READY 定向测试 | `8 passed` |
| MediaPipe 合成帧推理 | 通过，未检测到人体，符合预期 |
| 可视化合成帧绘制 | 通过，输出 `(480, 640, 3) uint8` |
| Ruff | 79 项提示；未发现 `F821` 未定义名称 |
| Arduino 实际编译 | 未执行，环境没有 Arduino 工具链 |
| Arduino 实机串口和负载 | 未执行，需要四块 UNO 和真实负载 |

pytest 的两条 warning 来自 protobuf 对未来 Python 3.14 的弃用提示，不影响仓库当前声明支持的 Python 3.10～3.12。

### 2.4 依赖版本验证

| 依赖 | 实际版本 | 结论 |
|---|---:|---|
| MediaPipe | 0.10.21 | 与 `mp.solutions.pose` 代码匹配 |
| OpenCV contrib | 4.9.0.80 | 与 MediaPipe 依赖方案匹配 |
| NumPy | 1.26.4 | 通过 |
| PySerial | 3.5 | 通过 |
| Pillow | 10.4.0 | 通过 |
| pytest | 8.4.2 | 通过 |

---

## 3. 已确认完成的修改

## 3.1 `STOP_ALL` ACK 已被正确消费

### 原问题

旧流程可能只发送 `STOP_ALL` 而不读取它的 ACK。这样下一条命令等待 ACK 时，会先读到：

```text
ACK,PUMP_A,STOP_ALL
```

如果当前命令实际是 `DEFLATE_ALL`，旧 ACK 会污染本次判断，使正常流程被误判为失败。

### 当前使用的方法

现在 `PumpSender.send_stop_all()` 使用与普通命令相同的 `send_and_wait()`：

```python
def send_stop_all(self) -> bool:
    return self.send_and_wait(
        "STOP_ALL",
        expected_board_id=self.board_id,
        accepted_commands={"STOP_ALL"},
    )
```

泵组层的 `stop_all_best_effort()` 复用三板“先写后收集”方法：

```python
results = self._send_all_and_collect(
    "STOP_ALL",
    accepted_commands={"STOP_ALL"},
)
```

同时，`_read_ack()` 在读到不属于当前命令的旧 ACK 时会消费该行并继续等待，而不是立即把当前命令判为失败：

```python
if cmd not in accepted_commands:
    logger.warning("跳过旧 ACK ...")
    continue
```

### 复查结论

该修改方向正确。STOP ACK 不再故意留在缓冲区；即使出现迟到 ACK，读取端也会跳过并继续等当前命令。

---

## 3.2 SAFE_STOP 的放气不会再被第二次 STOP 取消

### 当前使用的方法

进入 SAFE_STOP 时使用以下顺序：

1. `stop_all_best_effort()`：停止当前充气/放气动作。
2. `light.send_all_off()`：灯箱全灭。
3. `send_deflate_all_best_effort()`：向仍在线的泵控板发送安全放气。
4. 某一块板失败时只记录日志，不再次广播 `STOP_ALL`。

关键实现：

```python
def send_deflate_all_best_effort(self, seconds: float) -> dict:
    results = self._send_all_and_collect(
        f"DEFLATE_ALL,{seconds}",
        accepted_commands={"DEFLATE_ALL"},
    )
    for board_id, ok in results.items():
        if not ok:
            logger.error("[SAFE_STOP] %s DEFLATE_ALL 未确认", board_id)
    return results
```

这里没有在部分失败后调用 `stop_all_best_effort()`，因此已经开始放气的正常板不会被新 STOP 取消。

### 复查结论

该问题已经修复，并有自动化测试覆盖。进入 SAFE_STOP 后状态机会保持等待退出，不会自动恢复，也不能按 `r` 重新启动充气。

---

## 3.3 三块泵控板使用“先全部写入，再收集回复”

### 当前使用的方法

`_send_all_and_collect()` 先连续向 PUMP_A、PUMP_B、PUMP_C 写入同一条命令，再读取 ACK：

```python
for board_id in self.board_ids:
    ok = self.boards[board_id]._write(command)
    results[board_id] = None if ok else False

deadline = time.monotonic() + response_timeout
for board_id in self.board_ids:
    remaining = max(0.0, deadline - time.monotonic())
    results[board_id] = self.boards[board_id]._read_ack(
        board_id, accepted_commands, remaining,
    )
```

这样不会出现“必须等待 A 回复后才给 B 发送”的明显串行启动延迟。所有板还共享一个 0.8 秒截止时间，三板总等待不会变成最坏 `3 × 0.8` 秒。

### 复查结论

命令同步性较旧版明显改善。仍有一个回复收集顺序的边界问题，详见第 7.2 节，但它不改变命令已经写入所有在线板这一事实。

---

## 3.4 READY 门禁已经校验板号和三路点充时长

### 当前协议

泵控 UNO 上电返回：

```text
READY,<板号>,<泵1毫秒>,<泵2毫秒>,<泵3毫秒>
```

例如：

```text
READY,PUMP_A,300,500,800
```

### Arduino 使用的方法

泵控固件在 `setup()` 中直接打印板号和本板三路参数：

```cpp
Serial.print("READY,");
Serial.print(BOARD_ID);
for (int i = 0; i < CHANNEL_COUNT; i++) {
  Serial.print(",");
  Serial.print(INFLATE_M_MS_PER_PUMP[i]);
}
Serial.println();
```

### Python 使用的方法

`main.py` 在正式串口模式下把配置表传给连接门禁：

```python
if not self.pump_group.connect_all(
    expected_inflate_m_ms=config.INFLATE_M_MS_PER_BOARD,
):
    # 拒绝进入运行态
```

`SerialSender.connect()` 会拒绝以下情况：

- 板号与目标串口不一致；
- READY 缺少三路时长；
- 时长不是整数；
- 三路时长与 Python 配置不完全相等；
- READY 超时。

### 复查结论

门禁逻辑已经可以拦截“PUMP_B 烧了 PUMP_A 固件参数”以及“Python 改了配置但 Arduino 没重烧”的常见部署错误。

注意：只有 `config.SERIAL_ENABLED=True` 时才启用真实串口和严格门禁。仓库默认仍为 `False`，正式实机必须改为 `True`。

---

## 3.5 灯箱闪烁已改成非阻塞状态机

### 原问题

旧版如果用多次 `delay()` 完成三闪，会阻塞约 1.8 秒。Python 只等待 0.8 秒，因此可能出现：

- Python 先超时；
- Arduino 后发送迟到 ACK；
- 闪烁期间无法响应 `LIGHT_ALL_OFF`。

### 当前使用的方法

灯箱固件使用 `millis()` 和运行时变量保存闪烁阶段：

```cpp
bool flashActive = false;
bool flashOnPhase = false;
int flashRemaining = 0;
unsigned long flashPhaseStart = 0;
```

收到 `LIGHT_FLASH,n` 后只启动状态机并立即返回 ACK：

```cpp
startFlash(times);
sendACK("LIGHT_FLASH");
```

每轮 `loop()` 尾部调用：

```cpp
updateFlash();
```

`LIGHT_ON`、`LIGHT_OFF`、`LIGHT_ALL_OFF` 都会先调用 `cancelFlash()`，所以闪烁可以被新命令立即中断。

### 复查结论

代码逻辑已解决阻塞超时。该结论仍需在灯箱 UNO 重新烧录后，通过串口计时和真实灯泡测试确认。

---

## 3.6 状态机使用不受冷却限制的实时动作

### 当前使用的方法

主循环将动作数据分成两个用途：

```python
state = self.action_recognizer.recognize_current(pose_result)

# 只用于历史记录和界面显示
self.action_recognizer.recognize(pose_result)

# 状态机使用实时结果
snapshot = self.state_machine.update(
    pose_result, state.hand_action,
)
```

- `recognize_current()`：每帧实时判断，供状态机使用，不受显示冷却影响。
- `recognize()`：保留动作历史和冷却，仅供界面显示。

### 复查结论

动作显示冷却不会再延迟状态机判断；该修改正确。

---

## 3.7 旧版站立、坐下、跌倒判定已从执行路径删除

当前动作池只有：

```python
ACTION_POOL = (
    "LEFT_HAND_UP",
    "RIGHT_HAND_UP",
    "BOTH_HANDS_UP",
)
```

系统只需要识别左手举起、右手举起、双手举起三种目标动作。站立、坐下、跌倒不再参与当前状态机逻辑，符合用户要求。

---

## 4. 用户确认保留方案的实现说明

## 4.1 保留 `GAS_MAX=15` 和不同点充时长

### 用户方案

- 九个气囊规格相同。
- 使用不同点充时长制造不同大小效果。
- 安全值按照单次点充时长最大的气囊计算。
- 继续使用 `GAS_MAX=15`，不改成九个气囊分别计数。

该方案可以保留，但应明确：代码中的 `gass` 是**成功执行的充气周期计数/预算指标**，不是压力传感器测得的真实气量。

### 当前代码方法

系统在 INIT 后执行：

```python
self.gass = int(config.INFLATE_TIME_A)
```

在惩罚充气中，每成功广播一次 `INFLATE_M`：

```python
self.gass += 1
```

达到 `GAS_MAX` 后：

```python
self._inflate_locked = True
self.pump.send_stop_all()
self._enter_counting_resume()
```

之后不再触发点充，直到正常进入 `DEFLATING`，再将 `gass` 清零。

### 按最大气囊计算的方法

设：

- 初始充气时间为 $A$ 秒；
- `GAS_MAX` 为 $G$；
- 最大单次点充时间为 $m_{max}$ 毫秒；
- 初始 `gass = floor(A)`；
- 达到上限前最多点充次数为 $N=G-floor(A)$。

则最大点充时长气囊的理论累计泵运行时间为：

$$
T_{max}=A+N\times\frac{m_{max}}{1000}
$$

按当前默认参数：

- $A=5$ 秒；
- $G=15$；
- $m_{max}=900$ 毫秒；
- $N=15-5=10$ 次。

因此：

$$
T_{max}=5+10\times0.9=14\text{ 秒}
$$

这表示理论上最大气囊对应气泵累计工作约 14 秒。它不等于气囊压力或体积，最终安全值仍必须通过实物标定得到。

### 标定 `GAS_MAX` 的反推方法

先通过实物测试得到最大气囊允许的保守泵运行时间 $T_{safe}$，再反推点充次数：

$$
N_{allowed}=\left\lfloor\frac{T_{safe}-A}{m_{max}/1000}\right\rfloor
$$

建议设置：

$$
GAS\_MAX\leq floor(A)+N_{allowed}
$$

标定时应对同规格气囊的个体差异、管路弯折、漏气、泵流量偏差、电源电压偏差和环境温度预留安全余量。不要只测一个气囊一次就把结果直接作为上限。

### 一个需要注意的代码边界

当前 `gass` 对 `INFLATE_TIME_A` 使用 `int()` 截断。如果将来把初始充气改成 `5.5` 秒，代码仍只记为 `5`。建议保持 `INFLATE_TIME_A` 为整数秒；若要使用小数，需将 `gass` 改为独立的“安全预算值”，避免截断造成计算偏差。

---

## 4.2 保留“错误动作立即触发一次充气”

当前代码满足该要求。

### 使用的方法

`COUNTING` 中发现动作不匹配，包括 `HAND_NONE`：

```python
elif match == MATCH_WRONG:
    if not self._inflate_locked:
        self._enter_inflating()
```

进入 `INFLATING` 时：

```python
self._last_inflate_m_time = 0.0
```

下一次状态更新满足：

```python
if self._last_inflate_m_time <= 0.0:
    self.pump.send_inflate_m()
```

因此第一次错误帧会进入 `INFLATING`，下一帧立即发送第一次 `INFLATE_M`，之后最多每秒一次。动作恢复正确时发送 `STOP_ALL` 并回到原 COUNTING 计时。

用户已经确认软件测试中未出现不可接受的误触发，因此本报告不要求增加多帧确认或消抖。

---

## 4.3 当前“PWM”实际是 RC 舵机脉宽

泵控固件使用：

```cpp
#include <Servo.h>

const int RC_PULSE_OFF_US = 1500;
const int RC_PULSE_ON_US  = 2000;

servos[deviceIdx].writeMicroseconds(
    on ? RC_PULSE_ON_US : RC_PULSE_OFF_US
);
```

这不是 Arduino `analogWrite()` 的占空比 PWM，而是 Servo 库产生的周期性 RC 控制脉冲：

- `1500 μs`：代码定义为停止/中性；
- `2000 μs`：代码定义为启动；
- S 线当前使用 D8～D13；
- 设备供电另由 D2～D7 的继电器控制。

因此仅说“开关是 PWM”仍不足以确认兼容。必须查电子开关说明书中的频率、脉宽、占空比、输入电平和停止方式。

---

## 5. 当前状态机和安全链路

当前实际状态为 9 个：

```text
INIT
WAITING
EXTRACTING
COUNTING
INFLATING
INTERVAL
ENDING
DEFLATING
SAFE_STOP
```

主要流程：

1. `INIT`：全部气囊初始充气 `INFLATE_TIME_A` 秒。
2. `WAITING`：可靠人体持续出现 `PERSON_CONFIRM_N1` 秒。
3. `EXTRACTING`：随机选择三种手部动作之一并亮对应灯。
4. `COUNTING`：只有动作正确时计时；错误或无动作时暂停并进入惩罚充气。
5. `INFLATING`：立即点充一次，此后每秒一次；动作正确则停止并恢复计时。
6. `INTERVAL`：灭灯，等待下一题。
7. `ENDING`：三灯闪烁，等待人离开；超时也会强制放气。
8. `DEFLATING`：正常放气，随后重新进入 INIT。
9. `SAFE_STOP`：串口失败后的停止和放气状态，不自动恢复。

### 5.1 人体可靠性检查

代码不是只相信 `person_detected`，还检查鼻子和双肩的可见度：

```python
CORE_LANDMARK_INDICES = (0, 11, 12)
CORE_VISIBILITY_THRESHOLD = 0.5
```

这有助于拦截椅子、衣架或海报形成的低质量假人体，同时允许半身入镜，不强制检查髋部。

### 5.2 离场安全

在 EXTRACTING、COUNTING、INFLATING、INTERVAL、ENDING 中，如果可靠人体消失达到 `ABSENCE_TIMEOUT_N4`，系统会触发安全序列：

```text
STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL
```

### 5.3 Arduino 本地兜底

`INFLATE_M` 还有 Arduino 端本地超时：

```cpp
const unsigned long INFLATE_M_LOCAL_TIMEOUT_MS = 1500;
```

即使 Python 停止刷新，Arduino 也不会无限保持点充模式。该本地保护应保留。

---

## 6. Trae CN 三项疑问的判断

## 6.1 PWM 类型：疑问成立，必须查实物说明书

Trae CN 提出的第一项是正确的，而且是正式带载前的必要确认项。

### 情况 A：说明书写的是 RC/Servo 脉宽

如果说明书明确包含类似以下描述：

- 50 Hz 左右；
- 1000～2000 μs 脉宽；
- 1500 μs 中位/停止；
- 三线为电源、地、信号；
- 可接受 Servo/PPM/RC pulse 输入；

则当前 `Servo.writeMicroseconds(1500/2000)` 方法方向正确，D8～D13 可由 Servo 库使用，不要求全部是 Arduino 标注的 `~` PWM 引脚。

### 情况 B：说明书写的是占空比 PWM

如果说明书要求：

- 固定频率，例如 490 Hz、1 kHz、10 kHz 等；
- 0～100% duty cycle；
- 使用 `analogWrite()` 或明确占空比控制；

则当前 Servo 方案不兼容，需要重做信号输出。UNO 常用硬件 PWM 引脚为 D3、D5、D6、D9、D10、D11；当前六路 S 线的 D8、D12、D13 不能直接提供标准 `analogWrite()` PWM。

由于 D2～D7 已用于继电器，若确实需要六路硬件占空比 PWM，可能需要：

- 重新分配引脚；
- 使用 PCA9685 等外部多路 PWM 驱动器；
- 或换用具有足够硬件 PWM 通道的控制板。

### 确认方法

1. 记录开关的准确型号和厂家。
2. 查说明书中的输入信号类型、频率、最小/最大脉宽或占空比、输入电压。
3. 在不接气泵负载时，用示波器或逻辑分析仪测量 Arduino S 线。
4. 先使用限流电源和单路设备测试停止/启动逻辑。
5. 验证 `1500 μs` 是否确实停止，`2000 μs` 是否确实启动；不能仅凭代码注释判断。

---

## 6.2 “三块 UNO 都需要重新烧录”：表述需要拆分

准确结论如下：

| 板卡 | 是否必须重新烧录 | 判断依据 |
|---|---|---|
| 灯箱 UNO | 是 | 要解决 `LIGHT_FLASH` 阻塞问题，必须运行新增的非阻塞固件 |
| PUMP_A | 条件性 | 当前固件若已经返回五字段 READY 且时长正确，可不重烧 |
| PUMP_B | 条件性 | 同上；必须确认板号和 B 组时长 |
| PUMP_C | 条件性 | 同上；必须确认板号和 C 组时长 |

### 泵控板无需重烧的充要条件

串口监视器上电时分别返回：

```text
READY,PUMP_A,300,500,800
READY,PUMP_B,400,600,700
READY,PUMP_C,500,700,900
```

并且这些数值与最终标定后的 `config.INFLATE_M_MS_PER_BOARD` 完全一致。

### 当前仓库固件的特别注意点

仓库中的 `pump_uno_v4_2.ino` 默认是：

```cpp
const char BOARD_ID[] = "PUMP_A";
const unsigned long INFLATE_M_MS_PER_PUMP[3] = {300, 500, 800};
```

因此不能把该文件不修改就连续烧到三块板。烧录 B/C 前必须改成对应参数：

| 板卡 | `BOARD_ID` | `INFLATE_M_MS_PER_PUMP` |
|---|---|---|
| A | `PUMP_A` | `{300, 500, 800}` |
| B | `PUMP_B` | `{400, 600, 700}` |
| C | `PUMP_C` | `{500, 700, 900}` |

如果后续实物标定值改变，应同时修改 Arduino 固件和 Python 配置，再重新烧录对应板。新的 READY 门禁会阻止两端参数不一致的系统启动。

---

## 6.3 14.2～14.5 实机测试：仍然待执行，而且是正式验收必需项

Trae CN 的第三项判断正确。自动化测试不能验证以下物理事实：

- 电子开关实际信号类型；
- 继电器高/低电平触发方向；
- 气泵与电磁阀接线是否互换；
- `1500/2000 μs` 是否对应真实停止/启动；
- 三块板的实际同步误差；
- 某板失联后其他板能否持续放气；
- 气囊真实体积、压力、材料公差和破裂风险；
- 紧急断电后系统的机械状态。

因此，软件测试通过后仍必须执行第 10 节的实机测试清单。

---

## 7. 仍建议修改的代码问题

## 7.1 P1：灯箱固件应提供独立可上传的 `.ino`

### 当前问题

新的灯箱代码位于：

```text
arduino_commands/lightbox_uno_commands.txt
```

该文件同时包含说明、分隔线和完整代码，不是一个清晰的 Arduino Sketch 目录。实际部署时容易出现：

- 复制范围不完整；
- 把文件末尾说明文字一起复制进 IDE；
- 误烧旧版灯箱程序；
- 后续维护者不知道哪段才是最终固件。

### 建议方法

新增独立目录和文件：

```text
arduino_commands/
├── lightbox_uno_commands.txt
├── lightbox_uno_v4_2/
│   └── lightbox_uno_v4_2.ino
└── pump_uno_v4_2.ino
```

`.ino` 中只保留可以直接编译的 C++/Arduino 代码。说明文档保留参数解释和上传步骤，并链接到 `.ino`。

### 验收标准

- Arduino IDE 直接打开 `.ino`，无需复制粘贴。
- Verify/Compile 无错误。
- 上电输出 `READY,LIGHT`。
- `LIGHT_FLASH,3` 在 0.8 秒内返回 `ACK,LIGHT,LIGHT_FLASH`。

---

## 7.2 P2：三板 ACK 顺序读取存在“前板占满截止时间”的边界

### 当前问题

命令已经先写给三块板，这一点是正确的。但 ACK 仍按 A → B → C 顺序读取。

如果 PUMP_A 完全不回复并占满共享的 0.8 秒，那么 B、C 的 ACK 即使早已到达，也会因共享 deadline 已过而直接记为失败，不再从缓冲区读取。后果主要是：

- B、C 的实际成功会被日志记成失败；
- B、C 的 ACK 留在串口缓冲区，需由后续读取跳过；
- 故障诊断结果不够准确。

由于命令已经发送到 B、C，该问题通常不会阻止它们执行物理动作，因此当前不是 P0 安全阻断项。

### 推荐修改方法一：轮询每个串口的可读数据

在共同 deadline 内循环检查每个串口的 `in_waiting`，每次只处理一行，不让无回复的 A 阻塞 B/C。建议将现有解析逻辑拆成：

```python
def _parse_response_line(
    self,
    text: str,
    expected_board_id: str,
    accepted_commands: set[str],
) -> bool | None:
    """True=正确 ACK，False=明确 ERR/板号错，None=旧 ACK 或无关消息。"""
```

泵组层使用：

```python
deadline = time.monotonic() + response_timeout
pending = {
    board_id for board_id, result in results.items()
    if result is None
}

while pending and time.monotonic() < deadline:
    progressed = False
    for board_id in tuple(pending):
        sender = self.boards[board_id]
        conn = sender.serial_conn
        if conn is None or conn.in_waiting <= 0:
            continue

        raw = conn.readline()
        progressed = True
        text = raw.decode("utf-8", errors="replace").strip()
        parsed = sender._parse_response_line(
            text, board_id, accepted_commands,
        )
        if parsed is not None:
            results[board_id] = parsed
            pending.remove(board_id)

    if not progressed:
        time.sleep(0.005)

for board_id in pending:
    results[board_id] = False
```

### 推荐修改方法二：并行读取三个独立串口

也可以用三个工作线程同时调用各板 `_read_ack()`。三个线程只读取各自的 `SerialSender`，不会竞争同一个串口。此方法改动较小，但每秒 `INFLATE_M` 都创建线程池会有额外开销，最好复用固定线程池。

### 建议新增测试

模拟以下情况：

1. A 无响应；
2. B、C 在 50 ms 内返回正确 ACK；
3. 方法在约 0.8 秒内结束；
4. 结果必须是 `A=False, B=True, C=True`；
5. B、C 缓冲区不应遗留本次 ACK。

---

## 7.3 P2：摄像头切换失败时没有真正回退到旧 ID

### 当前问题

`Camera.switch(new_id)` 会先把 `camera_id` 改成新 ID：

```python
self.release()
self.camera_id = new_id
return self.open()
```

如果新摄像头打开失败，`main._switch_camera()` 当前调用 `self.camera.open()`，但这时 `camera_id` 已经是失败的新 ID，所以它只是再次打开失败设备，并没有切回旧摄像头。

### 建议修改方法

在切换前保存旧 ID，失败后显式切回：

```python
def _switch_camera(self) -> None:
    ids = config.AVAILABLE_CAMERA_IDS
    if not ids:
        logger.warning("未配置 AVAILABLE_CAMERA_IDS")
        return

    old_id = self.camera.camera_id
    try:
        idx = ids.index(old_id)
    except ValueError:
        idx = -1

    new_id = ids[(idx + 1) % len(ids)]
    if self.camera.switch(new_id):
        return

    logger.warning("切换到摄像头 %d 失败，尝试恢复摄像头 %d", new_id, old_id)
    if not self.camera.switch(old_id):
        logger.error("恢复摄像头 %d 也失败", old_id)
```

### 同时建议修复资源释放

`Camera.open()` 第一次用 `CAP_DSHOW` 打开失败后，应先释放失败的 `VideoCapture` 再使用默认 API：

```python
self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
if not self.cap.isOpened():
    self.cap.release()
    self.cap = cv2.VideoCapture(self.camera_id)
```

### 建议新增测试

使用 mock 的 `VideoCapture` 覆盖：

- 新 ID 成功；
- 新 ID 失败、旧 ID 恢复成功；
- 新 ID 和旧 ID 都失败；
- 第一次后端失败时调用了 `release()`。

---

## 7.4 P3：文档与当前协议有不一致

建议统一修改以下内容：

| 文件 | 当前问题 | 应改内容 |
|---|---|---|
| `main.py` 文件头 | 仍有“8 状态”旧描述 | 改为 9 状态并加入 SAFE_STOP |
| `README.md` | READY 表格仍可能写成两字段 | 泵控 READY 改为五字段；灯箱仍为两字段 |
| `README.md` | 串口门禁只强调板号 | 加入三路 `INFLATE_M` 时长严格匹配 |
| `config.py` 注释 | 写成“仅供记录/可视化” | 改为“正式模式下作为 READY 启动门禁” |
| `docs/ARCHITECTURE.md` | `connect()` 签名和门禁说明过期 | 写入 `expected_ready_params` |
| `docs/TROUBLESHOOTING.md` | 测试泵后示例写 READY | 改为 `ACK,<板号>,TEST_PUMP`；READY 只在上电时输出 |
| Arduino 路径说明 | 灯箱只有 `.txt` | 指向新增的灯箱 `.ino` |

尤其要删除“执行 `TEST_PUMP` 后会再次收到 READY”的描述。当前泵控固件只在 `setup()` 上电阶段发送 READY，测试指令成功返回的是：

```text
ACK,PUMP_A,TEST_PUMP
```

---

## 7.5 P3：代码规范和测试覆盖率

Ruff 共报告 79 项，主要属于：

- 未使用导入或变量；
- 类型标注可改进；
- 行过长；
- 文档格式；
- 直接访问私有属性等。

未发现 `F821` 未定义名称，因此这些问题目前不构成运行阻断。建议先自动修复安全项：

```bash
ruff check . --fix
```

再人工处理剩余项，并在每批修改后重新运行 pytest。

当前覆盖率约为：

| 模块 | 覆盖率 |
|---|---:|
| `action_recognizer.py` | 94% |
| `state_machine.py` | 87% |
| `serial_sender.py` | 76% |
| `pose_detector.py` | 41% |
| `camera.py` | 0% |
| `visualizer.py` | 0% |
| 总计 | 55% |

建议优先补摄像头切换测试、灯箱串口响应测试和三板 ACK 饥饿边界测试。可视化模块可以保留较低优先级。

---

## 8. 固件烧录方案

## 8.1 烧录前准备

1. 断开气泵和电磁阀的高功率供电，只保留 UNO USB。
2. 为四块板贴实体标签：PUMP_A、PUMP_B、PUMP_C、LIGHT。
3. 记录每块板的 USB 端口号。
4. 确认 UNO 与信号模块共地。
5. 核对继电器是高触发还是低触发。
6. 确认外部电源电压、电流、保险丝和紧急断电方案。
7. 不要用 UNO 5V 引脚直接为气泵供电。

## 8.2 灯箱 UNO

灯箱必须烧录包含 `startFlash()`、`updateFlash()` 和 `cancelFlash()` 的非阻塞版本。

烧录后串口监视器应先显示：

```text
READY,LIGHT
```

发送：

```text
LIGHT_FLASH,3
```

应立即收到：

```text
ACK,LIGHT,LIGHT_FLASH
```

闪烁期间发送：

```text
LIGHT_ALL_OFF
```

三灯应立即熄灭，并返回：

```text
ACK,LIGHT,LIGHT_ALL_OFF
```

## 8.3 三块泵控 UNO

分别为每块板设置正确 `BOARD_ID` 和三路时长。每烧录完一块都立即打开串口监视器验证 READY，不要等三块全部烧完才检查。

如果串口输出仍是：

```text
READY,PUMP_A
```

而没有三个时长字段，则说明仍是旧固件，必须重烧。

## 8.4 正式 Python 配置

实机时至少修改：

```python
SERIAL_ENABLED = True
```

并按设备管理器填写：

```python
PUMP_BOARDS = [
    {"id": "PUMP_A", "port": "实际端口"},
    {"id": "PUMP_B", "port": "实际端口"},
    {"id": "PUMP_C", "port": "实际端口"},
]
LIGHT_SERIAL_PORT = "实际端口"
```

Python 端三组时长必须与 Arduino 完全相同：

```python
INFLATE_M_MS_PER_BOARD = {
    "PUMP_A": [300, 500, 800],
    "PUMP_B": [400, 600, 700],
    "PUMP_C": [500, 700, 900],
}
```

---

## 9. 软件复测命令

建议每次修改后，在仓库根目录执行：

```bash
python -m venv .venv
```

Windows：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -m compileall -q .
python -m pytest -q
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -m compileall -q .
python -m pytest -q
```

串口未接硬件的软件演示模式：

```python
SERIAL_ENABLED = False
```

真实系统验收必须改为：

```python
SERIAL_ENABLED = True
```

---

## 10. 实机测试清单

以下测试应按顺序执行。任何上一步失败，都不要继续扩大到更多设备或更高功率。

## 10.1 断电检查

- [ ] 记录电子开关型号和说明书。
- [ ] 确认 RC 脉宽或占空比 PWM 类型。
- [ ] 确认输入信号电平。
- [ ] 确认继电器高/低触发极性。
- [ ] 确认泵与阀的物理编号对应代码编号。
- [ ] 确认外部电源极性和额定电流。
- [ ] 确认 UNO 与控制信号共地。
- [ ] 确认气路无折弯、堵塞和反接。
- [ ] 准备机械泄压方式和人工紧急断电。
- [ ] 初次测试使用限流电源，先不连接气囊或使用安全测试负载。

## 10.2 单路电子开关与波形测试

- [ ] 不接泵负载，测量 S 线输出。
- [ ] 停止状态是否为说明书规定波形。
- [ ] 启动状态是否为说明书规定波形。
- [ ] 上电复位期间是否出现意外启动脉冲。
- [ ] `STOP_ALL` 后继电器是否断开、信号是否停止/回中位。
- [ ] 断开 USB 或 Python 进程崩溃后，设备是否会在本地超时停止。

## 10.3 单块泵控板无负载测试

以 PUMP_A 为例，启动串口监视器后确认：

```text
READY,PUMP_A,300,500,800
```

依次发送并检查回复：

| 发送 | 期望回复 | 检查点 |
|---|---|---|
| `STATUS` | `STATUS,PUMP_A,...` | 初始应为 idle/off |
| `TEST_PUMP,0,0.5` | `ACK,PUMP_A,TEST_PUMP` | 仅泵 1 控制通道启动 |
| `STOP_ALL` | `ACK,PUMP_A,STOP_ALL` | 所有通道立即停止 |
| `INFLATE_M` | `ACK,PUMP_A,INFLATE_M` | 三泵同时开始，各自按时结束 |
| 1 秒内再次 `INFLATE_M` | `ACK,PUMP_A,INFLATE_M_REFRESH` | 本轮周期刷新 |
| `DEFLATE_ALL,1` | `ACK,PUMP_A,DEFLATE_ALL` | 仅三路阀启动 |
| 非法时长 | `ERR,PUMP_A,BAD_DURATION` | 不应启动负载 |

对 PUMP_B、PUMP_C 重复相同测试。

## 10.4 单块泵控板带载测试

- [ ] 每次只接一台泵或一个阀。
- [ ] `TEST_PUMP,0,t` 只动作对应泵。
- [ ] 代码泵 1/2/3 与物理气囊编号一致。
- [ ] 阀 1/2/3 与对应气囊一致。
- [ ] 泵和阀不能同时错误导通。
- [ ] 运行到期后继电器和控制信号均停止。
- [ ] 连续多次 STOP 不产生异常重启或遗留动作。
- [ ] USB 串口拔掉后 Arduino 本地超时能停止点充。

## 10.5 三板同步测试

- [ ] 三块板分别输出正确 READY 和时长。
- [ ] Python 的严格门禁全部通过。
- [ ] 人为把 PUMP_B 参数改错时，Python 必须拒绝启动。
- [ ] `INFLATE_ALL` 三板近似同时开始。
- [ ] `INFLATE_M` 九泵近似同时开始，各自按配置停止。
- [ ] `STOP_ALL` 三板全部立即停止。
- [ ] 拔掉一块板后，正常命令应进入 SAFE_STOP。
- [ ] 一块板失联时，另外两块在线板仍收到 `DEFLATE_ALL` 并保持放气到期。
- [ ] 日志中的每板成功/失败与实际一致。

建议用手机高帧率录像、逻辑分析仪或示波器测量三板启动时间差，不要只凭肉眼判断“同步”。

## 10.6 灯箱测试

- [ ] 上电输出 `READY,LIGHT`。
- [ ] `LIGHT_ON,1/2/3` 分别点亮正确灯。
- [ ] `LIGHT_OFF,1/2/3` 分别熄灭正确灯。
- [ ] `LIGHT_FLASH,3` 在 0.8 秒内返回 ACK。
- [ ] 闪烁三次的亮/灭节奏正确。
- [ ] 闪烁中发送 `LIGHT_ALL_OFF` 可立即中断。
- [ ] 非法灯号和非法闪烁次数返回 ERR。

## 10.7 全流程测试

- [ ] `SERIAL_ENABLED=True` 时缺任意一块板都不能进入运行态。
- [ ] INIT 初始充气时长正确。
- [ ] 无人时保持 WAITING。
- [ ] 真人持续出现 n1 后才抽题。
- [ ] 三种手部动作与灯号一一对应。
- [ ] 正确动作只推进 COUNTING，不触发惩罚充气。
- [ ] 错误动作或无动作立即触发第一次 `INFLATE_M`。
- [ ] 持续错误时约每秒触发一次。
- [ ] 恢复正确动作后立即 `STOP_ALL`，COUNTING 从暂停处继续。
- [ ] `gass` 达 15 后不再发点充命令。
- [ ] 达上限后流程仍能完成，不会立即错误放气。
- [ ] 三题完成后灯箱三闪。
- [ ] 人离开 n4 后进入 DEFLATING。
- [ ] ENDING 长时间误检时，30 秒兜底能够强制放气。
- [ ] 任一泵控板故障进入 SAFE_STOP。
- [ ] SAFE_STOP 中按 `r` 不恢复，必须退出并排障重启。
- [ ] 按 `q` 或 Ctrl+C 时先 STOP/灭灯，再关闭串口。

## 10.8 `GAS_MAX` 实物标定

建议至少使用多个同规格气囊重复测试，不要只测试一个样本。

记录表：

| 气囊编号 | 初始充气 A | 单次点充 ms | 点充次数 | 最大尺寸/压力 | 是否安全 | 备注 |
|---|---:|---:|---:|---:|---|---|
| A1 |  | 300 |  |  |  |  |
| A2 |  | 500 |  |  |  |  |
| A3 |  | 800 |  |  |  |  |
| B1 |  | 400 |  |  |  |  |
| B2 |  | 600 |  |  |  |  |
| B3 |  | 700 |  |  |  |  |
| C1 |  | 500 |  |  |  |  |
| C2 |  | 700 |  |  |  |  |
| C3 |  | 900 |  |  |  |  |

以最大时长的 C3 或最终标定后真正最大的通道作为上限计算对象，再给其他同规格气囊留出制造公差和老化余量。

---

## 11. 验收标准

系统只有同时满足以下条件，才能从“可台架测试”升级为“可进行完整演示验收”：

### 11.1 软件验收

- [x] 全新环境依赖安装成功。
- [x] `pip check` 通过。
- [x] Python 编译和入口导入成功。
- [x] 51 项自动化测试通过。
- [x] STOP ACK、SAFE_STOP、READY 参数门禁有测试。
- [ ] 修复摄像头失败回退。
- [ ] 建议修复三板 ACK 收集饥饿边界。
- [ ] 更新过期协议文档。

### 11.2 固件验收

- [ ] 灯箱固件拆成可直接上传的 `.ino`。
- [ ] 灯箱 UNO 已烧录非阻塞版本。
- [ ] 三块泵控板 READY 都包含正确板号和三路时长。
- [ ] Arduino IDE 编译四块板固件全部通过。
- [ ] PWM/RC 类型与开关说明书完全一致。

### 11.3 硬件验收

- [ ] 单路波形正确。
- [ ] 单板泵/阀方向和编号正确。
- [ ] 三板同步和 STOP 正确。
- [ ] 某板失联后在线板仍安全放气。
- [ ] 灯箱闪烁可中断且无 ACK 超时。
- [ ] `GAS_MAX=15` 已通过最大气囊的实物安全标定。
- [ ] 全流程测试全部通过。

---

## 12. 建议修改优先顺序

### 通电前必须完成

1. 确认开关 PWM 类型和电气参数。
2. 生成并编译独立灯箱 `.ino`。
3. 烧录灯箱非阻塞固件。
4. 核对三块泵控板 READY、板号和三路时长。
5. 修复摄像头切换失败回退，或在本次展出中禁用 `c` 切换功能。

### 联调过程中建议完成

1. 改进三板 ACK 的公平收集。
2. 补充 A 无响应、B/C 正常响应的自动化测试。
3. 补摄像头切换 mock 测试。
4. 更新 README、架构文档和排障文档。

### 后续工程整理

1. 分批清理 Ruff 提示。
2. 提升 camera、pose_detector 和串口边界覆盖率。
3. 将 A/B/C 固件做成三个明确的构建配置，减少手动改参数后烧错板的概率。

---

## 13. 最终结论

提交 `7d784f2` 已经完成上一轮核心问题的主要修复，Python 软件测试结果良好，可以进入硬件台架联调。用户要求保留的 `GAS_MAX=15`、不同点充时长、错误动作立即充气和三种手部动作方案均已在当前代码中实现。

Trae CN 提到的三个遗留项中：

1. **PWM 类型确认完全正确，属于通电前必须完成的硬件兼容性确认。**
2. **灯箱 UNO 必须重烧；三块泵控 UNO 是否重烧取决于 READY 五字段、板号和时长是否已经与 Python 一致。**
3. **单板、三板、灯箱和全流程实机测试仍未完成，必须执行后才能做最终整机验收。**

因此当前推荐状态为：

> **软件复查通过，可进入受控的硬件台架测试；尚未达到免测试直接正式运行的条件。**

---

## 14. 代码参考

- 仓库主页：<https://github.com/012dim/mediapipe_pose_py>
- 本次复查提交：<https://github.com/012dim/mediapipe_pose_py/commit/7d784f2cb5eee60058fa99e8b24ab78b9efffd8a>
- 串口实现：<https://github.com/012dim/mediapipe_pose_py/blob/7d784f2cb5eee60058fa99e8b24ab78b9efffd8a/modules/serial_sender.py>
- 状态机实现：<https://github.com/012dim/mediapipe_pose_py/blob/7d784f2cb5eee60058fa99e8b24ab78b9efffd8a/modules/state_machine.py>
- 泵控固件：<https://github.com/012dim/mediapipe_pose_py/blob/7d784f2cb5eee60058fa99e8b24ab78b9efffd8a/arduino_commands/pump_uno_v4_2.ino>
- 灯箱固件说明：<https://github.com/012dim/mediapipe_pose_py/blob/7d784f2cb5eee60058fa99e8b24ab78b9efffd8a/arduino_commands/lightbox_uno_commands.txt>

