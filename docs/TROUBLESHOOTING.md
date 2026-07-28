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
3. 单独安装 mediapipe 看具体错误:
   ```bash
   pip install mediapipe==0.10.14 -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```
4. 如果报 protobuf 冲突:
   ```bash
   pip install protobuf==3.20.3 --force-reinstall
   ```

### Q2:`ModuleNotFoundError: No module named 'cv2'`

**原因**:OpenCV 未安装,或安装了 headless 版本。

**解决**:
```bash
pip install opencv-python==4.9.0.80
```
> 不要装 `opencv-python-headless`,它不带 GUI 显示功能。

### Q3:虚拟环境激活失败

**Windows PowerShell 报错"无法加载文件,因为在此系统上禁止运行脚本"**:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
然后重新激活:
```cmd
venv\Scripts\activate
```

## 运行相关

### Q4:启动报 `无法打开摄像头 0,请检查设备或修改 config.py 中的 CAMERA_ID`

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

### Q5:启动报 `MediaPipe Pose 初始化失败`

**原因**:通常是 mediapipe 安装不完整或 protobuf 版本冲突。

**解决**:
```bash
pip uninstall mediapipe protobuf -y
pip install mediapipe==0.10.14
pip install protobuf==3.20.3 --force-reinstall
```

### Q6:`cv2.imshow` 窗口无响应 / 一直转圈

**原因**:OpenCV 安装错误,或主循环没有调用 `cv2.waitKey`。

**解决**:
1. 确认装的是 `opencv-python` 而非 `opencv-python-headless`
2. Linux 无图形界面(SSH)需用 X11 转发或 VNC,建议直接在桌面环境运行
3. 确认 `main.py` 主循环有 `cv2.waitKey(1)`

### Q7:FPS 很低(< 15)

**优化方法**(按效果排序):
1. 降低模型复杂度:`config.py` 中 `MODEL_COMPLEXITY = 0`(轻量,提升 50%+)
2. 关闭其他占 CPU 的程序(浏览器、杀毒软件)
3. 降低分辨率:`CAMERA_WIDTH = 480, CAMERA_HEIGHT = 360`
4. 减少平滑缓冲:`SMOOTH_BUFFER_SIZE = 3`
5. 改善光照(光照差时 MediaPipe 推理更慢)
6. 用 USB 3.0 接口(避免 USB 2.0 带宽不足)

### Q8:骨骼闪烁 / 关键点位置跳动

**原因**:MediaPipe 推理本身有抖动,光照不足时更明显。

**解决**:
1. 增大平滑缓冲:`SMOOTH_BUFFER_SIZE = 8`(代价:延迟略增)
2. 确认 `SMOOTH_LANDMARKS = True`
3. 改善光照:正面补光,避免逆光
4. 站到摄像头正前方 1.5-2.5 米,确保全身入镜

## 动作识别相关

### Q9:举手没有反应

**排查**:
1. 确认手腕真的高于鼻子(超过 5% 画面高度,即 y 差 > 0.05)
2. 站近一些,确保上半身完整入镜(手腕不能超出画面)
3. 检查 `MIN_DETECTION_CONFIDENCE` 是否过低(< 0.3 会误判)
4. 按 `r` 重置冷却后重试(可能上一次触发还在冷却中)
5. 检查 `wrist.visibility` 是否 < 0.3(被遮挡)

### Q10:跌倒误报(明明站着却显示 FALL_DETECTED)

**原因**:跌倒判定比率 `|肩Y − 髋Y| / 肩宽 < 0.3`,坐姿或半蹲可能误判。

**解决**:
1. 调高 `FALL_RATIO_THRESHOLD`(如 0.4)
2. 检查肩膀是否水平(头部歪斜会导致 shoulder_dy > 0.2 被排除,但有时仍误判)
3. 跌倒后立即站起,避免长时间停留

### Q11:STAND / SIT 切换不灵敏

**原因**:膝关节角度阈值边界设置。

**解决**:
- 站立时角度通常 170-180°,坐下时 80-110°
- 中间区域(130-160°)不触发任何动作(半蹲状态)
- 如需更灵敏:调低 `KNEE_ANGLE_SIT`(如 120)或调高 `KNEE_ANGLE_STAND`(如 150)

### Q12:同一动作不停触发

**原因**:冷却时间过短或被重置。

**解决**:
1. 检查 `ACTION_COOLDOWN` 默认 1.0 秒,够用
2. 是否误按 `r` 重置了状态
3. 检查 `_last_trigger` 字典是否正常更新(看日志)

## 串口相关

### Q13:串口打不开,但程序继续运行

**这是预期行为**:串口是可选外设,失败仅警告不崩溃。日志会显示:
```
串口 COM3 打开失败: ...,继续运行(动作识别正常,仅不发送)。
```

**修复**:
1. 确认 Arduino 已连接,设备管理器查看端口名
2. 关闭 Arduino IDE 的串口监视器(占用冲突)
3. 修改 `config.py` 中 `DEFAULT_SERIAL_PORT_WIN = "COM4"`(实际端口)
4. 安装驱动:CH340 / CP2102 等 USB 转串口芯片需装驱动
5. 确认装了 pyserial:`pip install pyserial`

### Q14:Arduino 收不到数据

**排查**:
1. 波特率要一致(默认 9600),Arduino 端 `Serial.begin(9600)`
2. 检查 RX/TX 是否接反(Arduino RX ↔ 模块 TX)
3. 共地(Arduino GND 与模块 GND 相连)
4. 在 Arduino IDE 串口监视器手动发 `POSE,BOTH_HANDS_UP\n` 测试接收逻辑

## 退出相关

### Q15:按 q 或 Ctrl+C 退出时有 Python 异常堆栈

**原因**:资源释放顺序或信号处理问题。

**解决**:
1. 确认 `main.py` 中 `Application._cleanup()` 已正确 try/except
2. 不要直接杀进程,用 q 或 Esc 优雅退出
3. 如仍有异常,把日志贴到 issue

### Q16:窗口关了但进程没退出

**原因**:OpenCV 在某些 Windows 版本上 `cv2.imshow` 关闭事件不触发。

**解决**:
1. 用 `q` 键退出而非点击窗口关闭按钮
2. 或在 `main.py` 中添加窗口事件检测:
   ```python
   if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
       self._running = False
   ```

## 调试技巧

### 开启 DEBUG 日志

修改 `config.py`:
```python
LOG_LEVEL = "DEBUG"
```
可看到每帧的推理详情、按键码等。

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

### 查看关键点坐标

在 `main.py` 主循环加一行打印鼻子坐标:
```python
if pose_result.landmarks:
    nose = pose_result.landmarks[0]
    print(f"nose: x={nose.x:.3f} y={nose.y:.3f} vis={nose.visibility:.2f}")
```

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
5. 完整错误日志(开启 DEBUG 级别)
