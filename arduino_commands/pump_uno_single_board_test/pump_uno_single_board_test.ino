/* =====================================================================
 * 单板泵阀联合测试固件 v1.1(报告 107d463 复查)
 *
 * 用途:实机联调前逐路验证继电器、PWM S 线、阀状态、泵停止,
 *       无需 Python 主程序即可在 Arduino 串口监视器手动测试。
 *
 * v1.1 关键变更(报告 107d463 第 5/6/8/10 节):
 *   1. STOP_PUMPS 改为 emergencyStopAllPumps():两阶段立即硬断电,
 *      无 delay(),先断全部泵继电器再清 PWM,并清理 inflateChannelActive
 *      (报告 5.2/8.3:防 STOP 后到时仍输出 DONE 误导测试人员)。
 *   2. 增加泵阀互锁:VALVE_OPEN 检查对应泵是否运行,运行则拒绝(报告 8.2)。
 *   3. 串口改为非阻塞缓冲区解析(pollSerial + rxBuffer),不再使用
 *      阻塞式 Serial.readStringUntil('\n'),半条指令不再延迟泵到时停止
 *      (报告 6.3)。
 *   4. TEST_SIGNAL 参数命名修正:off_us/on_us → off_duty/on_duty,
 *      避免把占空比值误当成 RC 脉宽(报告 10.4)。
 *   5. 定时统一改为 start + duration + elapsedSince(),防 millis 回绕
 *      (报告 10.2)。
 *   6. parseStrictUInt 增加 ULONG_MAX 溢出检查(报告 10.3)。
 *
 * 设计原则(报告 11.3):
 *   - ACK 只表示命令被接受,DONE 只表示软件计时结束
 *   - 是否真正停止必须由继电器电压、S 线波形和实体动作验证
 *   - 单次测试最大时间从 300~500ms 开始,不直接允许 5 秒
 *   - ARM 超时必须关闭泵,并按已确认的安全阀状态处理
 *   - STOP 必须有硬件断电路径(继电器),不能只依赖 PWM 信号
 *
 * 硬件模型(与 pump_uno_v4_2.ino v4.3 一致):
 *   - 占空比 PWM + 继电器供电隔离
 *   - 引脚映射与正式固件完全相同:
 *       设备 0 (泵1): RELAY=D2,  PWM=D3
 *       设备 1 (阀1): RELAY=D4,  PWM=D5
 *       设备 2 (泵2): RELAY=D7,  PWM=D6
 *       设备 3 (阀2): RELAY=D8,  PWM=D9
 *       设备 4 (泵3): RELAY=D12, PWM=D10
 *       设备 5 (阀3): RELAY=D13, PWM=D11
 *
 * 串口协议(9600 baud,每行一条,以 '\n' 结尾):
 *   HELP                       打印可用命令
 *   STATUS                     查询当前板状态
 *   ARM                        启用测试权限(60 秒后自动 DISARMED)
 *   TEST_RELAY,<device>,<ms>   单独吸合继电器 device(0..5)ms 毫秒(≤2000)
 *   TEST_SIGNAL,<device>,<off_duty>,<on_duty>,<ms>
 *                              测试 PWM S 线:输出 on_duty 占空比 ms 毫秒,
 *                              前后各输出 off_duty 占空比(≤2000)
 *   VALVE_OPEN,<channel>       打开阀 channel(0..2),若对应泵运行则拒绝
 *   VALVE_CLOSE,<channel>      关闭阀 channel(0..2)
 *   INFLATE_CHANNEL,<ch>,<ms>  关阀 ch + 等稳定 + 启动泵 ch 持续 ms(≤2000)
 *   STOP_PUMPS                  立即硬断电全部 3 泵(无 delay)
 *   SAFE_VENT                  停止所有泵 + 打开所有阀(放气)
 *   DISARM                     立即关闭测试权限
 *
 * 响应格式:
 *   ACK,<command>             命令被接受
 *   DONE,<command>            计时结束
 *   STATUS,armed=...,pump=...,valve=...,relay=xxxxxx,pwm=xxxxxx
 *   ERR,<reason>
 *
 * 安全机制:
 *   - 未 ARM 时所有写硬件命令拒绝(ERR,NOT_ARMED)
 *   - ARM 后 60 秒无操作自动 DISARMED,期间所有泵/阀关闭
 *   - DISARM 触发全停
 *   - 测试时长硬上限 2000ms,避免误操作长时间通电
 * ===================================================================== */

/* =====================================================================
 * ★★★ 用户可调参数区 ★★★
 * ===================================================================== */

const int CHANNEL_COUNT = 3;

// 引脚映射(必须与 pump_uno_v4_2.ino v4.3 一致)
const int RELAY_PINS[6] = {2, 4, 7, 8, 12, 13};
const int PWM_PINS[6]   = {3, 5, 6, 9, 10, 11};

// 继电器触发电平(必须与正式固件一致)
const bool RELAY_ACTIVE_LOW = true;

// 阀通电语义(必须与正式固件一致,且由实测确定)
const bool VALVE_ENERGIZED_MEANS_OPEN = true;

// PWM 占空比
const int PWM_ON_DUTY  = 255;
const int PWM_OFF_DUTY = 0;

// 安全参数
const unsigned long ARM_TIMEOUT_MS    = 60000UL;  // ARM 后 60 秒自动 DISARM
const unsigned long MAX_TEST_MS       = 2000UL;   // 单次测试硬上限 2 秒
const unsigned long PWM_OFF_HOLD_MS   = 50UL;     // PWM OFF 帧保持
const unsigned long VALVE_SETTLE_MS   = 30UL;     // 阀切换稳定

// 串口接收缓冲区(报告 6.3:非阻塞解析,行长度上限)
const uint8_t RX_BUFFER_SIZE = 64;

/* =====================================================================
 * 参数区结束
 * ===================================================================== */

// 运行时状态
bool armed = false;
unsigned long armTime = 0;

// 设备状态记录
int  pwmDuty[6]       = {0, 0, 0, 0, 0, 0};
bool relayClosed[6]   = {false, false, false, false, false, false};

// 活动测试:TEST_RELAY / TEST_SIGNAL / INFLATE_CHANNEL 各自独立计时
// 报告 10.2:统一使用 start + duration 形式防 millis 回绕
bool testRelayActive = false;
unsigned long testRelayStart = 0;
unsigned long testRelayDuration = 0;
int  testRelayDevice = -1;

bool testSignalActive = false;
unsigned long testSignalStart = 0;
unsigned long testSignalDuration = 0;
int  testSignalDevice = -1;
bool testSignalOnPhase = false;  // true=输出 ON_DUTY,false=输出 OFF_DUTY

bool inflateChannelActive = false;
unsigned long inflateChannelStart = 0;
unsigned long inflateChannelDuration = 0;
int  inflateChannelPumpIdx = -1;

// 串口接收缓冲区(报告 6.3)
char rxBuffer[RX_BUFFER_SIZE];
uint8_t rxLength = 0;

// ============ 辅助函数 ============

inline int pumpToDevice(int pumpIdx)  { return pumpIdx * 2; }
inline int valveToDevice(int valveIdx){ return valveIdx * 2 + 1; }

inline bool elapsedSince(unsigned long start, unsigned long duration) {
  return (millis() - start) >= duration;
}

/**
 * parseStrictUInt - 严格无符号整数解析(报告 10.3:增加溢出检查)
 *
 * 任一非数字字符返回 false,且极长数字不再无符号回绕成较小值。
 */
bool parseStrictUInt(const String &text, unsigned long &value) {
  if (text.length() == 0) return false;
  unsigned long result = 0;
  for (unsigned int i = 0; i < text.length(); i++) {
    char c = text.charAt(i);
    if (c < '0' || c > '9') return false;
    unsigned long digit = (unsigned long)(c - '0');
    // 溢出检查:result * 10 + digit > ULONG_MAX 时拒绝
    if (result > (ULONG_MAX - digit) / 10UL) return false;
    result = result * 10UL + digit;
  }
  value = result;
  return true;
}

void setRelay(int deviceIdx, bool on) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  int pin = RELAY_PINS[deviceIdx];
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(pin, on ? LOW : HIGH);
  } else {
    digitalWrite(pin, on ? HIGH : LOW);
  }
  relayClosed[deviceIdx] = on;
}

void setPwm(int deviceIdx, int duty) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  analogWrite(PWM_PINS[deviceIdx], duty);
  pwmDuty[deviceIdx] = duty;
}

void deviceOn(int deviceIdx) {
  setRelay(deviceIdx, true);
  setPwm(deviceIdx, PWM_ON_DUTY);
}

void normalPumpOff(int deviceIdx) {
  setPwm(deviceIdx, PWM_OFF_DUTY);
  delay(PWM_OFF_HOLD_MS);
  setRelay(deviceIdx, false);
}

void emergencyPumpOff(int deviceIdx) {
  setRelay(deviceIdx, false);
  setPwm(deviceIdx, PWM_OFF_DUTY);
}

void setValveOpen(int channel, bool open) {
  int device = valveToDevice(channel);
  bool energize = VALVE_ENERGIZED_MEANS_OPEN ? open : !open;
  if (energize) {
    deviceOn(device);
  } else {
    setPwm(device, PWM_OFF_DUTY);
    delay(PWM_OFF_HOLD_MS);
    setRelay(device, false);
  }
}

void setPumpRunning(int channel, bool running) {
  int device = pumpToDevice(channel);
  if (running) {
    deviceOn(device);
  } else {
    normalPumpOff(device);
  }
}

/**
 * isPumpRunning - 判断指定通道的泵是否处于运行状态
 * 报告 8.2:用于泵阀互锁,VALVE_OPEN 前检查对应泵
 */
bool isPumpRunning(int channel) {
  if (channel < 0 || channel >= CHANNEL_COUNT) return false;
  int dev = pumpToDevice(channel);
  return relayClosed[dev] && (pwmDuty[dev] == PWM_ON_DUTY);
}

/**
 * emergencyStopAllPumps - 立即硬断电全部 3 泵(报告 5.2)
 *
 * 两阶段执行,无 delay():
 *   1. 先断开全部泵继电器(硬件断电优先)
 *   2. 再清零全部泵 PWM 信号
 * 同时清理 inflateChannelActive,防止 STOP 后到时仍输出 DONE(报告 8.3)。
 *
 * 注意:只断泵,不关阀,避免误改阀状态影响放气测试。
 */
void emergencyStopAllPumps() {
  // 第一阶段:先让全部泵失去动力电源
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setRelay(pumpToDevice(ch), false);
  }
  // 第二阶段:清零全部泵控制信号
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setPwm(pumpToDevice(ch), PWM_OFF_DUTY);
  }
  // 清理 INFLATE_CHANNEL 活动标志,防止迟到 DONE(报告 8.3)
  inflateChannelActive = false;
  inflateChannelPumpIdx = -1;
}

// 全停(紧急停止语义):用于 ARM 超时 / DISARM
void allStop() {
  for (int i = 0; i < 6; i++) {
    emergencyPumpOff(i);
  }
  testRelayActive = false;
  testSignalActive = false;
  inflateChannelActive = false;
  testRelayDevice = -1;
  testSignalDevice = -1;
  inflateChannelPumpIdx = -1;
}

// ============ 响应函数 ============

void sendACK(const String &cmd) {
  Serial.print("ACK,");
  Serial.println(cmd);
}

void sendDONE(const String &cmd) {
  Serial.print("DONE,");
  Serial.println(cmd);
}

void sendERR(const String &reason) {
  Serial.print("ERR,");
  Serial.println(reason);
}

void sendStatus() {
  Serial.print("STATUS,armed=");
  Serial.print(armed ? "1" : "0");
  // pump 状态位:设备 0/2/4
  Serial.print(",pump=");
  for (int i = 0; i < 3; i++) {
    int dev = pumpToDevice(i);
    Serial.print((pwmDuty[dev] == PWM_ON_DUTY && relayClosed[dev]) ? "1" : "0");
  }
  // valve 状态位:设备 1/3/5
  Serial.print(",valve=");
  for (int i = 0; i < 3; i++) {
    int dev = valveToDevice(i);
    Serial.print((pwmDuty[dev] == PWM_ON_DUTY && relayClosed[dev]) ? "1" : "0");
  }
  Serial.print(",relay=");
  for (int i = 0; i < 6; i++) {
    Serial.print(relayClosed[i] ? "1" : "0");
  }
  Serial.print(",pwm=");
  for (int i = 0; i < 6; i++) {
    Serial.print(pwmDuty[i] == PWM_ON_DUTY ? "1" : "0");
  }
  Serial.println();
}

void printHelp() {
  Serial.println(F("HELP,available commands:"));
  Serial.println(F("  HELP"));
  Serial.println(F("  STATUS"));
  Serial.println(F("  ARM"));
  Serial.println(F("  TEST_RELAY,<device 0..5>,<ms 1..2000>"));
  // 报告 10.4:参数命名修正 off_us/on_us → off_duty/on_duty
  Serial.println(F("  TEST_SIGNAL,<device 0..5>,<off_duty 0..255>,<on_duty 0..255>,<ms 1..2000>"));
  Serial.println(F("  VALVE_OPEN,<channel 0..2>"));
  Serial.println(F("  VALVE_CLOSE,<channel 0..2>"));
  Serial.println(F("  INFLATE_CHANNEL,<channel 0..2>,<ms 1..2000>"));
  Serial.println(F("  STOP_PUMPS"));
  Serial.println(F("  SAFE_VENT"));
  Serial.println(F("  DISARM"));
}

// ============ 命令处理 ============

void cmdArm() {
  armed = true;
  armTime = millis();
  sendACK("ARM");
}

void cmdDisarm() {
  allStop();
  armed = false;
  sendACK("DISARM");
}

void cmdTestRelay(int device, unsigned long ms) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (device < 0 || device >= 6) { sendERR("BAD_DEVICE"); return; }
  if (ms == 0 || ms > MAX_TEST_MS) { sendERR("BAD_DURATION"); return; }
  // 关闭其他设备,只吸合指定继电器(不写 PWM)
  allStop();
  setRelay(device, true);
  testRelayActive = true;
  testRelayDevice = device;
  // 报告 10.2:start + duration 形式防回绕
  testRelayStart = millis();
  testRelayDuration = ms;
  sendACK("TEST_RELAY");
}

void cmdTestSignal(int device, int offDuty, int onDuty, unsigned long ms) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (device < 0 || device >= 6) { sendERR("BAD_DEVICE"); return; }
  if (offDuty < 0 || offDuty > 255) { sendERR("BAD_OFF_DUTY"); return; }
  if (onDuty < 0 || onDuty > 255) { sendERR("BAD_ON_DUTY"); return; }
  if (ms == 0 || ms > MAX_TEST_MS) { sendERR("BAD_DURATION"); return; }
  allStop();
  // 先输出 OFF_DUTY 一帧
  setRelay(device, true);
  setPwm(device, offDuty);
  delay(PWM_OFF_HOLD_MS);
  // 然后输出 ON_DUTY 持续 ms 毫秒
  setPwm(device, onDuty);
  testSignalActive = true;
  testSignalDevice = device;
  testSignalStart = millis();
  testSignalDuration = ms;
  testSignalOnPhase = true;
  sendACK("TEST_SIGNAL");
}

/**
 * cmdValveOpen - 打开阀,带泵阀互锁(报告 8.2)
 *
 * 若对应通道泵正在运行,拒绝执行并返回 ERR,PUMP_RUNNING。
 * 这样可防止"泵充气 + 阀同时放气"的冲突状态。
 */
void cmdValveOpen(int channel) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (channel < 0 || channel >= CHANNEL_COUNT) { sendERR("BAD_CHANNEL"); return; }
  if (isPumpRunning(channel)) { sendERR("PUMP_RUNNING"); return; }
  setValveOpen(channel, true);
  sendACK("VALVE_OPEN");
}

void cmdValveClose(int channel) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (channel < 0 || channel >= CHANNEL_COUNT) { sendERR("BAD_CHANNEL"); return; }
  setValveOpen(channel, false);
  sendACK("VALVE_CLOSE");
}

void cmdInflateChannel(int channel, unsigned long ms) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (channel < 0 || channel >= CHANNEL_COUNT) { sendERR("BAD_CHANNEL"); return; }
  if (ms == 0 || ms > MAX_TEST_MS) { sendERR("BAD_DURATION"); return; }
  allStop();
  // 关闭对应阀 → 等稳定 → 启动泵
  setValveOpen(channel, false);
  delay(VALVE_SETTLE_MS);
  setPumpRunning(channel, true);
  inflateChannelActive = true;
  inflateChannelPumpIdx = channel;
  // 报告 10.2:start + duration 形式防回绕
  inflateChannelStart = millis();
  inflateChannelDuration = ms;
  sendACK("INFLATE_CHANNEL");
}

/**
 * cmdStopPumps - 立即硬断电全部泵(报告 5.2)
 *
 * 走 emergencyStopAllPumps(),无 delay(),两阶段:
 *   1. 先断全部泵继电器(硬件断电)
 *   2. 再清零全部泵 PWM
 * 并清理 inflateChannelActive,防止迟到 DONE(报告 8.3)。
 */
void cmdStopPumps() {
  emergencyStopAllPumps();
  sendACK("STOP_PUMPS");
}

void cmdSafeVent() {
  // 停所有泵(立即硬断电)+ 打开所有阀放气
  emergencyStopAllPumps();
  delay(VALVE_SETTLE_MS);
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setValveOpen(i, true);
  }
  sendACK("SAFE_VENT");
}

// ============ 串口非阻塞解析(报告 6.3)============

/**
 * pollSerial - 非阻塞串口接收并解析指令
 *
 * 报告 6.3:旧版 Serial.readStringUntil('\n') 在收到半条数据时会
 * 阻塞约 1 秒(Serial 默认超时),延迟泵的到时停止检查。
 * 新版使用固定缓冲区,逐字符读取,遇到 '\n' 才解析,无任何阻塞。
 *
 * 超长指令返回 ERR,LINE_TOO_LONG 并重置缓冲区,防止溢出。
 */
void pollSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') continue;  // 忽略 CR

    if (c == '\n') {
      rxBuffer[rxLength] = '\0';
      handleCommand(String(rxBuffer));
      rxLength = 0;
      continue;
    }

    if (rxLength < RX_BUFFER_SIZE - 1) {
      rxBuffer[rxLength++] = c;
    } else {
      // 缓冲区溢出:丢弃当前指令,通知发送方
      rxLength = 0;
      sendERR("LINE_TOO_LONG");
    }
  }
}

/**
 * handleCommand - 解析并执行一行完整指令
 */
void handleCommand(const String &line) {
  String trimmed = line;
  trimmed.trim();
  if (trimmed.length() == 0) return;

  if (trimmed == "HELP") {
    printHelp();

  } else if (trimmed == "STATUS") {
    sendStatus();

  } else if (trimmed == "ARM") {
    cmdArm();

  } else if (trimmed == "DISARM") {
    cmdDisarm();

  } else if (trimmed == "STOP_PUMPS") {
    cmdStopPumps();

  } else if (trimmed == "SAFE_VENT") {
    cmdSafeVent();

  } else if (trimmed.startsWith("TEST_RELAY,")) {
    // TEST_RELAY,device,ms
    int c1 = trimmed.indexOf(',', 11);
    if (c1 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long device, ms;
    if (!parseStrictUInt(trimmed.substring(11, c1), device) ||
        !parseStrictUInt(trimmed.substring(c1 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdTestRelay((int)device, ms);
    }

  } else if (trimmed.startsWith("TEST_SIGNAL,")) {
    // TEST_SIGNAL,device,offDuty,onDuty,ms  (报告 10.4:命名修正)
    int c1 = trimmed.indexOf(',', 12);
    int c2 = (c1 >= 0) ? trimmed.indexOf(',', c1 + 1) : -1;
    int c3 = (c2 >= 0) ? trimmed.indexOf(',', c2 + 1) : -1;
    if (c1 < 0 || c2 < 0 || c3 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long device, offDuty, onDuty, ms;
    if (!parseStrictUInt(trimmed.substring(12, c1), device) ||
        !parseStrictUInt(trimmed.substring(c1 + 1, c2), offDuty) ||
        !parseStrictUInt(trimmed.substring(c2 + 1, c3), onDuty) ||
        !parseStrictUInt(trimmed.substring(c3 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdTestSignal((int)device, (int)offDuty, (int)onDuty, ms);
    }

  } else if (trimmed.startsWith("VALVE_OPEN,")) {
    unsigned long ch;
    if (!parseStrictUInt(trimmed.substring(11), ch)) {
      sendERR("BAD_CHANNEL");
    } else {
      cmdValveOpen((int)ch);
    }

  } else if (trimmed.startsWith("VALVE_CLOSE,")) {
    unsigned long ch;
    if (!parseStrictUInt(trimmed.substring(12), ch)) {
      sendERR("BAD_CHANNEL");
    } else {
      cmdValveClose((int)ch);
    }

  } else if (trimmed.startsWith("INFLATE_CHANNEL,")) {
    // INFLATE_CHANNEL,ch,ms
    int c1 = trimmed.indexOf(',', 16);
    if (c1 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long ch, ms;
    if (!parseStrictUInt(trimmed.substring(16, c1), ch) ||
        !parseStrictUInt(trimmed.substring(c1 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdInflateChannel((int)ch, ms);
    }

  } else {
    sendERR("UNKNOWN_CMD");
  }
}

// ============ setup / loop ============

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < 6; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
    relayClosed[i] = false;
  }
  for (int i = 0; i < 6; i++) {
    pinMode(PWM_PINS[i], OUTPUT);
    analogWrite(PWM_PINS[i], PWM_OFF_DUTY);
    pwmDuty[i] = PWM_OFF_DUTY;
  }
  rxLength = 0;
  Serial.println(F("READY,SINGLE_TEST"));
  Serial.println(F("HELP,type HELP for commands; ARM first"));
}

void loop() {
  // ---- 安全计时检查(报告 6.3:先于串口处理,确保准时停泵)----

  // ARM 超时
  if (armed && elapsedSince(armTime, ARM_TIMEOUT_MS)) {
    allStop();
    armed = false;
    Serial.println(F("EVENT,ARM_TIMEOUT,DISARMED"));
  }

  // TEST_RELAY 到期
  if (testRelayActive && elapsedSince(testRelayStart, testRelayDuration)) {
    setRelay(testRelayDevice, false);
    testRelayActive = false;
    testRelayDevice = -1;
    sendDONE("TEST_RELAY");
  }

  // TEST_SIGNAL 到期
  if (testSignalActive && elapsedSince(testSignalStart, testSignalDuration)) {
    // 恢复 OFF_DUTY,断继电器
    setPwm(testSignalDevice, PWM_OFF_DUTY);
    delay(PWM_OFF_HOLD_MS);
    setRelay(testSignalDevice, false);
    testSignalActive = false;
    testSignalDevice = -1;
    sendDONE("TEST_SIGNAL");
  }

  // INFLATE_CHANNEL 到期
  if (inflateChannelActive && elapsedSince(inflateChannelStart, inflateChannelDuration)) {
    setPumpRunning(inflateChannelPumpIdx, false);
    inflateChannelActive = false;
    inflateChannelPumpIdx = -1;
    sendDONE("INFLATE_CHANNEL");
  }

  // ---- 串口指令处理(报告 6.3:非阻塞 pollSerial)----
  pollSerial();

  // ---- 安全计时二次检查(报告 6.3 推荐:串口处理后再检查一次,
  //      以防 pollSerial 内部因解析复杂指令占用时间而错过到时停止)----
  if (inflateChannelActive && elapsedSince(inflateChannelStart, inflateChannelDuration)) {
    setPumpRunning(inflateChannelPumpIdx, false);
    inflateChannelActive = false;
    inflateChannelPumpIdx = -1;
    sendDONE("INFLATE_CHANNEL");
  }
}
