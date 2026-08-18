# 常见问题排查

## 安装相关

### Q1:`pip install mediapipe` 报错或卡住

**原因**:MediaPipe 包体较大(~30MB),且依赖特定版本的 protobuf / numpy。

**解决**:
1. 使用国内镜像加速:
   ```bash
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```
2. 确认 Python 版本 3.10 / 3.11 / 3.12(MediaPipe 不支持 3.13+)
3. 必须固定版本 `mediapipe==0.10.21`(新版不再提供 `mp.solutions`):
   ```bash
   pip install mediapipe==0.10.21 -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```
4. 如果报 protobuf 冲突:
   ```bash
   pip install protobuf==3.20.3 --force-reinstall
   ```

### Q2:`ModuleNotFoundError: No module named 'cv2'`

**原因**:OpenCV 未安装,或安装了 headless 版本。

**解决**:
```bash
pip install opencv-contrib-python==4.9.0.80
```
> 不要同时安装 `opencv-python`,否则两者会共同占用 `cv2` 命名空间导致冲突。
> 不要装 `opencv-python-headless`,它不带 GUI 显示功能。

### Q3:`AttributeError: module 'mediapipe' has no attribute 'solutions'`

**原因**:安装了 mediapipe 0.10.21 之后的新版本(如 0.10.35),不再提供 `mp.solutions.pose`。

**解决**:
```bash
pip uninstall -y mediapipe opencv-python opencv-contrib-python numpy
pip install -r requirements.txt
```
验证:
```bash
python -c "import mediapipe as mp; print(mp.__version__); print(hasattr(mp, 'solutions'))"
```
预期输出 `0.10.21` 和 `True`。

### Q4:虚拟环境激活失败

**Windows PowerShell 报错"无法加载文件,因为在此系统上禁止运行脚本"**:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
然后重新激活:
```cmd
venv\Scripts\activate
```

## 运行相关

### Q5:启动报 `无法打开摄像头 0,请检查设备或修改 config.py 中的 CAMERA_ID`

**排查步骤**:
1. **检查摄像头是否被占用**:关闭 Zoom、微信、QQ 等可能使用摄像头的软件
2. **检查 USB 连接**:重新插拔,确认设备管理器中摄像头无黄色感叹号
3. **换 ID**:运行时按 `c` 切换,或修改 `config.py` 中 `CAMERA_ID = 1`
4. **测试摄像头**:用 Windows 自带"相机"应用确认摄像头本身可用
5. **Linux 用户**:确认用户在 `video` 组:
   ```bash
   sudo usermod -aG video $USER
   # 重新登录后生效
   ```

### Q6:启动报 `MediaPipe Pose 初始化失败`

**原因**:通常是 mediapipe 安装不完整或 protobuf 版本冲突。

**解决**:
```bash
pip uninstall mediapipe protobuf -y
pip install mediapipe==0.10.21
pip install protobuf==3.20.3 --force-reinstall
```

### Q7:`cv2.imshow` 窗口无响应 / 一直转圈

**原因**:OpenCV 安装错误,或主循环没有调用 `cv2.waitKey`。

**解决**:
1. 确认装的是 `opencv-contrib-python` 而非 `opencv-python-headless`
2. Linux 无图形界面(SSH)需用 X11 转发或 VNC,建议直接在桌面环境运行
3. 确认 `main.py` 主循环有 `cv2.waitKey(1)`

### Q8:FPS 很低(< 15)

**优化方法**(按效果排序):
1. 降低模型复杂度:`config.py` 中 `MODEL_COMPLEXITY = 0`(轻量,提升 50%+)
2. 关闭其他占 CPU 的程序(浏览器、杀毒软件)
3. 降低分辨率:`CAMERA_WIDTH = 480, CAMERA_HEIGHT = 360`
4. 减少平滑缓冲:`SMOOTH_BUFFER_SIZE = 3`
5. 改善光照(光照差时 MediaPipe 推理更慢)
6. 用 USB 3.0 接口(避免 USB 2.0 带宽不足)

### Q9:骨骼闪烁 / 关键点位置跳动

**原因**:MediaPipe 推理本身有抖动,光照不足时更明显。

**解决**:
1. 增大平滑缓冲:`SMOOTH_BUFFER_SIZE = 8`(代价:延迟略增)
2. 确认 `SMOOTH_LANDMARKS = True`
3. 改善光照:正面补光,避免逆光
4. 站到摄像头正前方 1.5-2.5 米,确保全身入镜

## 动作识别相关

### Q10:举手没有反应

**排查**:
1. 确认手腕真的高于鼻子(超过 5% 画面高度,即 y 差 > 0.05)
2. 站近一些,确保上半身完整入镜(手腕不能超出画面)
3. 检查 `MIN_DETECTION_CONFIDENCE` 是否过低(< 0.3 会误判)
4. 按 `r` 重置冷却后重试(可能上一次触发还在冷却中)
5. 检查 `wrist.visibility` 是否 < 0.3(被遮挡)

### Q11:左右手识别反了

**原因**:画面已做水平翻转(镜像),代码中 LEFT_WRIST / RIGHT_WRIST 已交换判定。

**解决**:
- 这是设计行为:MediaPipe 的 LEFT_WRIST 实际对应用户右手(镜像后)
- 若仍反,检查是否修改过 `action_recognizer.py` 的 `_detect_hand_action`
- 确认主循环执行了 `cv2.flip(frame, 1)`

### Q12:同一动作不停触发

**原因**:冷却时间过短或被重置。

**解决**:
1. 检查 `ACTION_COOLDOWN` 默认 1.0 秒,够用
2. 是否误按 `r` 重置了状态
3. 检查 `_last_trigger` 字典是否正常更新(看日志)

## 串口相关

### Q13:`SERIAL_ENABLED=False` 时状态机卡在 SAFE_STOP

**原因**:旧版 `PumpGroupSender` 在 `SERIAL_ENABLED=False` 时 send 返回 False,触发 SAFE_STOP。

**解决**:确认使用 v4.2 代码,`PumpGroupSender(test_mode=not config.SERIAL_ENABLED)` 已自动启用 test_mode,跳过发送并返回成功。

### Q14:串口 COM3/5/7 打不开(OSError 121)

**原因**:Windows 错误 121 通常是端口被占用或 CH340 驱动异常。

**解决**:
1. 拔插 USB 重新枚举
2. 关闭其他占用该 COM 的程序(Arduino IDE 串口监视器等)
3. 重装 CH340 驱动
4. 在设备管理器中确认 4 块 UNO 的实际 COM 编号,更新 `config.py` 的 `PUMP_BOARDS` 与 `LIGHT_SERIAL_PORT`
5. 代码侧已容错,不会因此崩溃

### Q15:启动时提示"泵控板 PUMP_X 连接失败"

**原因**:`SERIAL_ENABLED=True` 时,3 板泵控 UNO 必须全部连接且 READY 板号匹配。

**解决**:
1. 确认 3 块 UNO 已通过 USB 连接
2. 确认每块 UNO 烧录了正确的 `BOARD_ID`(PUMP_A / PUMP_B / PUMP_C)
3. 确认 COM 口与 `config.py` 的 `PUMP_BOARDS` 一致
4. 检查日志中是否收到 `READY,<板号>,<时长1>,<时长2>,<时长3>` 响应(5 字段);
   若板号不匹配或三路时长与 `config.INFLATE_M_MS_PER_BOARD` 不一致会拒绝连接
   (报告 10.2:此门禁拦截"PUMP_B 烧了 PUMP_A 参数"或"改配置未重烧"错误)

### Q16:Arduino 收不到数据

**排查**:
1. 波特率要一致(默认 9600),Arduino 端 `Serial.begin(9600)`
2. 检查 RX/TX 是否接反(Arduino RX ↔ 模块 TX)
3. 共地(Arduino GND 与模块 GND 相连)
4. 用 Arduino IDE 串口监视器手动发以下指令测试:
   ```
   STATUS
   TEST_PUMP,0,1
   INFLATE_ALL,1
   DEFLATE_ALL,1
   STOP_ALL
   LIGHT_ON,1
   LIGHT_ALL_OFF
   ```

### Q17:进入 SAFE_STOP 后无法恢复

**这是设计行为**:SAFE_STOP 是终态,任一泵控板发送失败后进入,不自动恢复。

**解决**:
1. 检查日志中的 `[SAFE_STOP]` 记录,确认哪块板失败
2. 修复硬件/连接后,按 `q` 退出程序
3. 重新启动程序(SAFE_STOP 不能通过 `r` 重置,必须重启)

### Q17.5:v4.3 实机联调前必须验证的 P0 项目(报告 c15a9b0)

**背景**:上一轮实机测试发现"软件输出 DONE 后气泵仍运行"和"充气时电磁阀同时放气",根因是旧版固件假设电子开关为 RC Servo 脉冲、阀极性未配置。

**联调前必须完成的验证步骤**(详见 README 烧录步骤与报告 12.x):

1. **烧录单板测试固件** `arduino_commands/pump_uno_single_board_test/pump_uno_single_board_test.ino` 到 1 块 UNO
2. **断开气泵动力负载**,只接 UNO + 继电器模块
3. 在 Arduino IDE 串口监视器执行:
   ```
   ARM
   TEST_RELAY,0,300       # 单独吸合继电器 0(泵1)300ms
   TEST_SIGNAL,0,0,255,300  # 测试 PWM S 线:OFF→ON→OFF
   ```
4. **万用表测 COM/NO**:
   - 待机/STOP:0V(否则继电器接错 NO/NC 或触发电平反)
   - 运行期间:额定工作电压
   - 到时后:回到 0V
5. **确认 `VALVE_ENERGIZED_MEANS_OPEN` 极性**(报告 8.3):
   - `VALVE_OPEN,0` 后观察气路是否打开排气
   - 若不打开,改为 `false` 重新烧录
6. **接气路但先不接气球**,执行 `INFLATE_CHANNEL,0,300`:
   - 充气期间不应同时放气
   - 到时后气泵物理停止
7. 全部通过后才允许烧录正式固件 `pump_uno_v4_2.ino` 并接入 Python 主程序

**关键判断**(报告 7.2):
| STOP 后现象 | 判断 |
|---|---|
| 继电器灯灭,COM/NO 为 0V,气泵停止 | 正常,可继续联调 |
| 继电器灯灭,但 COM/NO 仍有电 | 继电器接线或触点错误 |
| COM/NO 为 0V,但气泵仍运行 | 气泵动力电源绕过继电器 |
| STOP 有 ACK,但继电器灯仍亮 | `RELAY_ACTIVE_LOW` 配置反了 |

## 退出相关

### Q18:按 q 或 Ctrl+C 退出时有 Python 异常堆栈

**原因**:资源释放顺序或信号处理问题。

**解决**:
1. 确认 `main.py` 中 `Application._cleanup()` 已正确 try/except
2. 不要直接杀进程,用 q 或 Esc 优雅退出
3. 如仍有异常,把日志贴到 issue

### Q19:窗口关了但进程没退出

**原因**:OpenCV 在某些 Windows 版本上 `cv2.imshow` 关闭事件不触发。

**解决**:
1. 用 `q` 键退出而非点击窗口关闭按钮
2. 或在 `main.py` 中添加窗口事件检测:
   ```python
   if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
       self._running = False
   ```

### Q20:v4.4 灯箱 USB 拔出后 Python 主程序退出(报告 9.x)

**原因**:v4.3 灯箱 `_read_ack()` 未捕获 `readline()` 抛出的 `OSError`/`SerialException`,异常冒泡到主循环导致退出。

**解决**:
1. 升级到 v4.4 `modules/serial_sender.py`,`_read_ack()` 已捕获异常并调 `_mark_disconnected_no_lock()` 标记断开,返回 False 而不抛异常。
2. 状态机/主循环对灯箱失败应只记录日志,不影响泵控安全流程。
3. 灯箱恢复连接需重启程序(目前无热重连)。
4. 单元测试覆盖:`tests/test_serial_sender.py::TestLightReadAckException`(7 个用例,验证返回 False、标记断开、清理 serial_conn、后续调用不抛异常)。

### Q21:v4.4 STOP_ALL 行为变更(报告 7.x)

**原因**:v4.3 的 `STOP_ALL` 走 `allOff()` 全断电,在 `VALVE_ENERGIZED_MEANS_OPEN=false` 配置下会让阀断电=打开,意外放掉气球。

**解决**:
1. v4.4 固件 `STOP_ALL` 改为 `safeVent()` 语义:停泵 + 打开全部阀放气(5 秒后自动关阀)。
2. 若需"停泵保压"(动作恢复 / GAS_MAX),改发新命令 `HOLD_ALL`(对应 `holdPressure()`)。
3. 两种命令均通过 `setValveOpen()` 自动映射极性,两种 `VALVE_ENERGIZED_MEANS_OPEN` 配置都正确。
4. Python 侧:`PumpSender.send_hold_all()` / `PumpGroupSender.send_hold_all()` 已就位。

### Q22:v4.4 单板测试固件 STOP_PUMPS 不立即停泵(报告 5.x)

**原因**:v1.0 单板测试固件的 `STOP_PUMPS` 走 `setPumpRunning(false)` → `normalPumpOff()`(含 `delay(50)`),三泵顺序停止,最多相差约 100ms。

**解决**:
1. 升级到 v1.1 单板测试固件,`STOP_PUMPS` 改为 `emergencyStopAllPumps()` 两阶段立即硬断电(无 `delay()`):
   - 阶段 1:全部泵继电器断开
   - 阶段 2:清零全部泵 PWM
2. 同时清理 `inflateChannelActive`,防止 STOP 后到时仍输出 `DONE,INFLATE_CHANNEL` 误导测试人员。
3. 验收命令序列(报告 5.3):
   ```
   ARM
   INFLATE_CHANNEL,0,1000
   STOP_PUMPS
   STATUS
   ```
   期望:STOP 后泵立即停止,STATUS 中 pump=000,且原 1000ms 到期不再输出 DONE。

### Q23:v4.4 单板测试固件允许"泵运行 + 阀同时放气"(报告 8.x)

**原因**:v1.0 单板测试固件 `VALVE_OPEN` 不检查对应泵是否运行,允许 `INFLATE_CHANNEL,0,2000` 后立即 `VALVE_OPEN,0`,形成泵充气 + 阀放气冲突。

**解决**:
1. 升级到 v1.1 单板测试固件,`cmdValveOpen()` 增加 `isPumpRunning(channel)` 检查:
   ```cpp
   if (isPumpRunning(channel)) { sendERR("PUMP_RUNNING"); return; }
   ```
2. 测试时若需开阀,先 `STOP_PUMPS` 或 `DISARM`,再 `VALVE_OPEN`。

### Q24:v4.4 半条串口指令延迟泵停止(报告 6.x)

**原因**:v4.3 三份 Arduino 固件使用阻塞式 `Serial.readStringUntil('\n')`,默认超时约 1 秒,半条指令(无 `\n`)会阻塞主循环,延迟泵到时停止检查。

**解决**:
1. v4.4 三份固件改为非阻塞 `pollSerial()` + `rxBuffer[]` 固定缓冲区逐字符读取。
2. `loop()` 中先检查到期、再 `pollSerial()`、再二次检查到期,确保准时停泵。
3. 超长指令(超过 `RX_BUFFER_SIZE`)返回 `ERR,LINE_TOO_LONG` 并重置缓冲区。
4. 验收:启动 300ms 泵测试,运行中发送无 `\n` 的半条指令,测量泵实际停止时间应在允许误差内,而非延长到 1 秒以上。

## 调试技巧

### 开启 DEBUG 日志

修改 `config.py`:
```python
LOG_LEVEL = "DEBUG"
```
可看到每帧的推理详情、按键码、串口发送记录等。

### 单独测试摄像头

写个最小脚本测试摄像头是否正常:
```python
import cv2
cap = cv2.VideoCapture(0)
while True:
    ok, frame = cap.read()
    if not ok: break
    cv2.imshow("test", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"): break
cap.release()
cv2.destroyAllWindows()
```

### 查看 Arduino 串口响应

用 Arduino IDE 串口监视器(波特率 9600)发送指令并查看响应:
```
发送: STATUS
返回: STATUS,PUMP_A,mode=IDLE,relay=000000,pwm=000000

发送: TEST_PUMP,0,1
返回: ACK,PUMP_A,TEST_PUMP
```

> ★ v4.3 引脚映射变更后,`relay` 与 `pwm` 位图对应的设备索引不变(0..5:泵1阀1泵2阀2泵3阀3),但物理引脚已重分配:`relay` 走 D2/4/7/8/12/13,`pwm` 走 D3/5/6/9/10/11。

> 注意:READY 只在上电 `setup()` 阶段发送一次,格式为
> `READY,PUMP_A,300,500,800`(5 字段:板号 + 三路点充时长)。
> 测试指令成功返回的是 `ACK,<板号>,<命令>`,不会再返回 READY。

### 性能分析

```bash
pip install py-spy
py-spy top --pid <python_pid>
```
可看到每个函数耗时,定位瓶颈。

## 环境信息收集

反馈问题时,请提供以下信息:
1. 操作系统(Windows 10/11、macOS、Linux 发行版)
2. Python 版本:`python --version`
3. 依赖版本:`pip list | grep -E "mediapipe|opencv|numpy"`
4. 摄像头型号(USB / 笔记本内置)
5. Arduino 接线情况(4 块 UNO 的 COM 口)
6. 完整错误日志(开启 DEBUG 级别)
