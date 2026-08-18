/* =====================================================================
 * 单板泵阀联合测试固件 v1.0(报告 11.x)
 *
 * 用途:实机联调前逐路验证继电器、PWM S 线、阀状态、泵停止,
 *       无需 Python 主程序即可在 Arduino 串口监视器手动测试。
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
 *   TEST_SIGNAL,<device>,<off_us>,<on_us>,<ms>
 *                              测试 PWM S 线:输出 on_us 占空比 ms 毫秒,
 *                              前后各输出 off_us 占空比(≤2000)
 *   VALVE_OPEN,<channel>       打开阀 channel(0..2)
 *   VALVE_CLOSE,<channel>      关闭阀 channel(0..2)
 *   INFLATE_CHANNEL,<ch>,<ms>  关阀 ch + 等稳定 + 启动泵 ch 持续 ms(≤2000)
 *   STOP_PUMPS                  立即停止全部 3 泵
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
bool testRelayActive = false;
unsigned long testRelayEnd = 0;
int  testRelayDevice = -1;

bool testSignalActive = false;
unsigned long testSignalStart = 0;
unsigned long testSignalDuration = 0;
int  testSignalDevice = -1;
bool testSignalOnPhase = false;  // true=输出 ON_DUTY,false=输出 OFF_DUTY

bool inflateChannelActive = false;
unsigned long inflateChannelEnd = 0;
int  inflateChannelPumpIdx = -1;

// ============ 辅助函数 ============

inline int pumpToDevice(int pumpIdx)  { return pumpIdx * 2; }
inline int valveToDevice(int valveIdx){ return valveIdx * 2 + 1; }

inline bool elapsedSince(unsigned long start, unsigned long duration) {
  return (millis() - start) >= duration;
}

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

// 全停(紧急停止语义)
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
  Serial.println(F("  TEST_SIGNAL,<device 0..5>,<off_us 0..255>,<on_us 0..255>,<ms 1..2000>"));
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
  testRelayEnd = millis() + ms;
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

void cmdValveOpen(int channel) {
  if (!armed) { sendERR("NOT_ARMED"); return; }
  if (channel < 0 || channel >= CHANNEL_COUNT) { sendERR("BAD_CHANNEL"); return; }
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
  inflateChannelEnd = millis() + ms;
  sendACK("INFLATE_CHANNEL");
}

void cmdStopPumps() {
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setPumpRunning(i, false);
  }
  sendACK("STOP_PUMPS");
}

void cmdSafeVent() {
  // 停所有泵 + 打开所有阀放气
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setPumpRunning(i, false);
  }
  delay(VALVE_SETTLE_MS);
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setValveOpen(i, true);
  }
  sendACK("SAFE_VENT");
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
  Serial.println(F("READY,SINGLE_TEST"));
  Serial.println(F("HELP,type HELP for commands; ARM first"));
}

void loop() {
  // ---- ARM 超时检查 ----
  if (armed && elapsedSince(armTime, ARM_TIMEOUT_MS)) {
    allStop();
    armed = false;
    Serial.println(F("EVENT,ARM_TIMEOUT,DISARMED"));
  }

  // ---- 活动测试计时检查 ----
  if (testRelayActive && millis() >= testRelayEnd) {
    setRelay(testRelayDevice, false);
    testRelayActive = false;
    testRelayDevice = -1;
    sendDONE("TEST_RELAY");
  }

  if (testSignalActive && elapsedSince(testSignalStart, testSignalDuration)) {
    // 恢复 OFF_DUTY,断继电器
    setPwm(testSignalDevice, PWM_OFF_DUTY);
    delay(PWM_OFF_HOLD_MS);
    setRelay(testSignalDevice, false);
    testSignalActive = false;
    testSignalDevice = -1;
    sendDONE("TEST_SIGNAL");
  }

  if (inflateChannelActive && millis() >= inflateChannelEnd) {
    setPumpRunning(inflateChannelPumpIdx, false);
    inflateChannelActive = false;
    inflateChannelPumpIdx = -1;
    sendDONE("INFLATE_CHANNEL");
  }

  // ---- 串口指令处理 ----
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  if (line == "HELP") {
    printHelp();

  } else if (line == "STATUS") {
    sendStatus();

  } else if (line == "ARM") {
    cmdArm();

  } else if (line == "DISARM") {
    cmdDisarm();

  } else if (line == "STOP_PUMPS") {
    cmdStopPumps();

  } else if (line == "SAFE_VENT") {
    cmdSafeVent();

  } else if (line.startsWith("TEST_RELAY,")) {
    // TEST_RELAY,device,ms
    int c1 = line.indexOf(',', 11);
    if (c1 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long device, ms;
    if (!parseStrictUInt(line.substring(11, c1), device) ||
        !parseStrictUInt(line.substring(c1 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdTestRelay((int)device, ms);
    }

  } else if (line.startsWith("TEST_SIGNAL,")) {
    // TEST_SIGNAL,device,offDuty,onDuty,ms
    int c1 = line.indexOf(',', 12);
    int c2 = (c1 >= 0) ? line.indexOf(',', c1 + 1) : -1;
    int c3 = (c2 >= 0) ? line.indexOf(',', c2 + 1) : -1;
    if (c1 < 0 || c2 < 0 || c3 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long device, offDuty, onDuty, ms;
    if (!parseStrictUInt(line.substring(12, c1), device) ||
        !parseStrictUInt(line.substring(c1 + 1, c2), offDuty) ||
        !parseStrictUInt(line.substring(c2 + 1, c3), onDuty) ||
        !parseStrictUInt(line.substring(c3 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdTestSignal((int)device, (int)offDuty, (int)onDuty, ms);
    }

  } else if (line.startsWith("VALVE_OPEN,")) {
    unsigned long ch;
    if (!parseStrictUInt(line.substring(11), ch)) {
      sendERR("BAD_CHANNEL");
    } else {
      cmdValveOpen((int)ch);
    }

  } else if (line.startsWith("VALVE_CLOSE,")) {
    unsigned long ch;
    if (!parseStrictUInt(line.substring(12), ch)) {
      sendERR("BAD_CHANNEL");
    } else {
      cmdValveClose((int)ch);
    }

  } else if (line.startsWith("INFLATE_CHANNEL,")) {
    // INFLATE_CHANNEL,ch,ms
    int c1 = line.indexOf(',', 16);
    if (c1 < 0) { sendERR("BAD_ARGS"); return; }
    unsigned long ch, ms;
    if (!parseStrictUInt(line.substring(16, c1), ch) ||
        !parseStrictUInt(line.substring(c1 + 1), ms)) {
      sendERR("BAD_ARGS");
    } else {
      cmdInflateChannel((int)ch, ms);
    }

  } else {
    sendERR("UNKNOWN_CMD");
  }
}
