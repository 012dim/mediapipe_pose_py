/* =====================================================================
 * 气泵控制 Uno (v4.2) - 3 泵 + 3 阀 = 6 设备
 *
 * 同一份代码烧录到 3 块 UNO(PUMP_A/B/C),只需修改下方 BOARD_ID
 *
 * 硬件模型(v4.1): RC 脉冲 + 继电器供电隔离
 *   - 每台设备(泵/阀)占用 1 路继电器(供电通断) + 1 路 S 线(RC 脉冲)
 *   - 继电器闭合 → 设备得电;Servo 输出 RC 脉冲 → 控制设备启停
 *   - 上电默认:继电器断开(RELAY_ACTIVE_LOW 时输出 HIGH),无 RC 脉冲
 *
 * 设备映射(CHANNEL_COUNT=3,泵/阀交错排列):
 *   索引 0: 泵1  (D2 继电器 + D8  S 线)
 *   索引 1: 阀1  (D3 继电器 + D9  S 线)
 *   索引 2: 泵2  (D4 继电器 + D10 S 线)
 *   索引 3: 阀2  (D5 继电器 + D11 S 线)
 *   索引 4: 泵3  (D6 继电器 + D12 S 线)
 *   索引 5: 阀3  (D7 继电器 + D13 S 线)
 *
 * 串口协议(9600 baud,每行一条,以 '\n' 结尾):
 *   INFLATE_ALL,a    全部 3 泵充气 a 秒(0 < a <= 30)
 *   DEFLATE_ALL,b    全部 3 阀打开放气 b 秒(0 < b <= 30)
 *   INFLATE_M        9 泵(本板 3 泵)同步点充,每泵独立时长
 *                    (见 INFLATE_M_MS_PER_PUMP[3]);Python 每秒广播一次
 *                    刷新,本地最多持续 INFLATE_M_LOCAL_TIMEOUT_MS 防呆
 *   STOP_ALL         立即停止全部设备(模式互斥优先级最高)
 *   STATUS           查询当前状态
 *   TEST_PUMP,i,t    测试第 i 号泵(0..2),持续 t 秒(0 < t <= 5)
 *
 * 响应(每条指令执行后回送):
 *   READY,<板号>,<泵1时长>,<泵2时长>,<泵3时长>
 *                       上电就绪(同时回送本板 3 泵点充时长,单位 ms,
 *                       用于 Python 端核对烧录参数是否与标定表一致)
 *   ACK,<板号>,<命令>                  指令执行成功
 *   ERR,<板号>,<原因>                  指令拒绝/失败
 *   STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx
 * ===================================================================== */

#include <Servo.h>

/* =====================================================================
 * ★★★ 用户可调参数区(在 Arduino IDE 中修改此处即可)★★★
 * ===================================================================== */

// ---- 板 ID ----
// 3 块 UNO 烧同一份代码,只需改此处为 "PUMP_A" / "PUMP_B" / "PUMP_C"
const char BOARD_ID[] = "PUMP_A";

// ---- 通道数(每板 3 泵 + 3 阀,固定为 3)----
const int CHANNEL_COUNT = 3;

// ---- 引脚映射(6 设备交错:泵/阀/泵/阀/泵/阀)----
// 设备索引 i (i=0..5):
//   偶数 i = 泵(i/2 + 1),对应 RELAY_PINS[i] / SERVO_PINS[i]
//   奇数 i = 阀(i/2 + 1),对应 RELAY_PINS[i] / SERVO_PINS[i]
const int RELAY_PINS[6] = {2, 3, 4, 5, 6, 7};        // 继电器引脚 D2-D7
const int SERVO_PINS[6] = {8, 9, 10, 11, 12, 13};   // S 线引脚   D8-D13

// ---- 继电器触发电平 ----
// true  -> 输出 LOW 时继电器闭合(常见 5V 继电器模块)
// false -> 输出 HIGH 时继电器闭合
const bool RELAY_ACTIVE_LOW = true;

// ---- RC 脉冲参数(微秒)----
// Servo.writeMicroseconds() 输出 1000-2000us 周期脉冲
// 1500us = 中性(停止),2000us = 正向启动
const int RC_PULSE_OFF_US = 1500;
const int RC_PULSE_ON_US  = 2000;

/* ---------------------------------------------------------------------
 * ★ INFLATE_M 每泵独立吸气时长(毫秒)★
 * ---------------------------------------------------------------------
 * 9 泵(本板 3 泵)同步启动,但每泵持续时长不同(均 ≤1000ms)
 * Python 端每秒广播一次 INFLATE_M,每次广播重启所有泵周期
 *
 *   INFLATE_M_MS_PER_PUMP[0] -> 泵1 吸气时长
 *   INFLATE_M_MS_PER_PUMP[1] -> 泵2 吸气时长
 *   INFLATE_M_MS_PER_PUMP[2] -> 泵3 吸气时长
 *
 * ★ 占位值,实物标定后必须修改 ★
 * --------------------------------------------------------------------- */
const unsigned long INFLATE_M_MS_PER_PUMP[3] = {300, 500, 800};

// ---- INFLATE_M 本地截止(毫秒)----
// 即使 Python 没及时刷新,本地最多持续 1500ms 即自动停止
// (大于 Python 每秒一次的刷新周期 1000ms,避免误超时)
const unsigned long INFLATE_M_LOCAL_TIMEOUT_MS = 1500;

// ---- INFLATE_ALL / DEFLATE_ALL 时长上限(秒)----
// 超过此值 reject,不 clamp
const float MAX_DURATION_SEC = 30.0;

// ---- TEST_PUMP 测试时长上限(秒)----
const float TEST_PUMP_MAX_SEC = 5.0;

/* =====================================================================
 * 用户可调参数区结束
 * ===================================================================== */


// ---- 模式定义(模式互斥:同一时刻只能处于一个模式)----
enum Mode {
  MODE_IDLE = 0,
  MODE_INFLATE_ALL,
  MODE_DEFLATE_ALL,
  MODE_INFLATE_M,
  MODE_TEST,
};
Mode currentMode = MODE_IDLE;

// ---- 运行时变量 ----
Servo servos[6];                       // 6 个 Servo 对象(对应 6 设备)
bool servoAttached[6] = {false, false, false, false, false, false};

// 起始时刻 + 持续时长(用 millis() 差值判断超时,防回绕)
unsigned long inflateAllStartTime = 0;
unsigned long inflateAllDuration  = 0;
unsigned long deflateAllStartTime = 0;
unsigned long deflateAllDuration  = 0;
unsigned long inflateMStartTime   = 0;   // INFLATE_M 本轮周期启动时刻
unsigned long inflateMLastRefresh = 0;   // 上次 Python 刷新时刻(看门狗用)
unsigned long testStartTime       = 0;
unsigned long testDuration        = 0;

unsigned long inflateMPumpStart[3]    = {0, 0, 0};  // 每泵本轮启动时刻
bool          inflateMPumpActive[3]   = {false, false, false};

bool inflatingAll = false;
bool deflatingAll = false;
bool inflatingM   = false;
bool testingPump  = false;


// ============ 辅助函数 ============

// 设备索引 -> 泵索引 (0,2,4 -> 0,1,2)
inline int pumpToDevice(int pumpIdx)  { return pumpIdx * 2; }
// 设备索引 -> 阀索引 (1,3,5 -> 0,1,2)
inline int valveToDevice(int valveIdx){ return valveIdx * 2 + 1; }

/**
 * elapsedSince - 防回绕超时判断
 *
 * millis() 每 49.7 天回绕一次。直接比较 millis() >= deadline 在回绕边界
 * 会判断错误。正确做法是用 unsigned 减法:(now - start) 自动回绕,
 * 只要实际 elapsed < 49.7 天就始终正确。
 *
 * @param start    起始时刻(millis())
 * @param duration 持续时长(ms)
 * @return true 表示已超时
 */
inline bool elapsedSince(unsigned long start, unsigned long duration) {
  return (millis() - start) >= duration;
}

/**
 * setRelay - 控制单个继电器供电通断
 * @param deviceIdx 设备索引(0..5)
 * @param on true=供电(继电器闭合),false=断电(继电器断开)
 */
void setRelay(int deviceIdx, bool on) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  int pin = RELAY_PINS[deviceIdx];
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(pin, on ? LOW : HIGH);
  } else {
    digitalWrite(pin, on ? HIGH : LOW);
  }
}

/**
 * setServoPulse - 输出 RC 脉冲控制设备启停
 * @param deviceIdx 设备索引(0..5)
 * @param on true=启动(RC_PULSE_ON_US),false=停止(RC_PULSE_OFF_US)
 *
 * 首次调用时 attach Servo 到对应引脚(占用 timer1 资源)
 */
void setServoPulse(int deviceIdx, bool on) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  if (!servoAttached[deviceIdx]) {
    servos[deviceIdx].attach(SERVO_PINS[deviceIdx]);
    servoAttached[deviceIdx] = true;
  }
  servos[deviceIdx].writeMicroseconds(on ? RC_PULSE_ON_US : RC_PULSE_OFF_US);
}

/**
 * stopServo - 停止 RC 脉冲并 detach(释放定时器资源)
 */
void stopServo(int deviceIdx) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  if (servoAttached[deviceIdx]) {
    servos[deviceIdx].detach();
    servoAttached[deviceIdx] = false;
  }
}

/**
 * deviceOn - 启动单台设备:闭合继电器 + 启动 RC 脉冲
 */
void deviceOn(int deviceIdx) {
  setRelay(deviceIdx, true);
  setServoPulse(deviceIdx, true);
}

/**
 * deviceOff - 停止单台设备:停止 RC 脉冲 + 断开继电器
 *
 * 顺序:先停止 RC 脉冲 -> 短暂保持让设备停稳 -> 断电 -> detach
 * 避免继电器断电瞬间电弧/反电动势损坏设备
 */
void deviceOff(int deviceIdx) {
  setServoPulse(deviceIdx, false);
  stopServo(deviceIdx);
  setRelay(deviceIdx, false);
}

/**
 * allOff - 全部 6 台设备停止 + 继电器断开
 * 用于 STOP_ALL 指令、模式切换前清理、上电初始化
 */
void allOff() {
  // 先停止所有 RC 脉冲(让设备停稳)
  for (int i = 0; i < 6; i++) {
    setServoPulse(i, false);
  }
  delay(5);  // 短暂保持脉冲让设备停稳
  // 然后断开所有继电器并 detach
  for (int i = 0; i < 6; i++) {
    stopServo(i);
    setRelay(i, false);
  }
  // 清除所有模式标志
  inflatingAll = false;
  deflatingAll = false;
  inflatingM   = false;
  testingPump  = false;
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    inflateMPumpActive[i] = false;
  }
  currentMode = MODE_IDLE;
}

/**
 * validateDuration - 校验时长合法(>0 且 <= MAX_DURATION_SEC)
 * 不合法返回 false(reject,不 clamp)
 */
inline bool validateDuration(float seconds) {
  return (seconds > 0.0) && (seconds <= MAX_DURATION_SEC);
}

/**
 * enterMode - 进入新模式前先 allOff(模式互斥)
 *
 * INFLATE_M 刷新(currentMode == MODE_INFLATE_M 时不调用本函数,
 * 由 startInflateM() 直接处理)。
 */
void enterMode(Mode newMode) {
  allOff();
  currentMode = newMode;
}

// ============ 响应函数 ============

void sendACK(const String &cmd) {
  Serial.print("ACK,");
  Serial.print(BOARD_ID);
  Serial.print(",");
  Serial.println(cmd);
}

void sendERR(const String &reason) {
  Serial.print("ERR,");
  Serial.print(BOARD_ID);
  Serial.print(",");
  Serial.println(reason);
}

// ============ 指令实现 ============

/**
 * startInflateAll - 全部 3 泵充气,持续 seconds 秒
 *
 * 流程:先断开所有阀(防气路冲突) -> 启动所有泵
 */
void startInflateAll(float seconds) {
  enterMode(MODE_INFLATE_ALL);
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    deviceOff(valveToDevice(i));   // 断开阀
  }
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    deviceOn(pumpToDevice(i));     // 启动泵
  }
  inflateAllStartTime = millis();
  inflateAllDuration  = (unsigned long)(seconds * 1000);
  inflatingAll = true;
}

/**
 * startDeflateAll - 全部 3 阀打开(放气),持续 seconds 秒
 *
 * 流程:先断开所有泵(防气路冲突) -> 打开所有阀
 */
void startDeflateAll(float seconds) {
  enterMode(MODE_DEFLATE_ALL);
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    deviceOff(pumpToDevice(i));    // 断开泵
  }
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    deviceOn(valveToDevice(i));    // 打开阀
  }
  deflateAllStartTime = millis();
  deflateAllDuration  = (unsigned long)(seconds * 1000);
  deflatingAll = true;
}

/**
 * startInflateM - 9 泵同步点充(本板 3 泵),每泵独立时长
 *
 * 行为:
 *   - 首次进入(currentMode != MODE_INFLATE_M):allOff + 断开所有阀 + 启动 3 泵
 *   - 已在 INFLATE_M 模式(刷新):仅重启 3 泵周期(不断阀,因为阀已断开)
 *
 * 每泵各自计时,到时自动停泵;Python 每秒广播一次重启周期
 * 本地看门狗:INFLATE_M_LOCAL_TIMEOUT_MS(1500ms)无刷新则全停
 */
void startInflateM() {
  if (currentMode != MODE_INFLATE_M) {
    enterMode(MODE_INFLATE_M);
    // 进入新周期:断开所有阀(防气路冲突)
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      deviceOff(valveToDevice(i));
    }
  }
  // 每收到 INFLATE_M(无论首次还是刷新)都重启所有泵周期
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    deviceOn(pumpToDevice(i));
    inflateMPumpStart[i]  = millis();
    inflateMPumpActive[i] = true;
  }
  inflateMStartTime   = millis();
  inflateMLastRefresh = millis();
  inflatingM = true;
}

/**
 * testPump - 测试单个泵
 * @param pumpIdx 泵索引(0/1/2)
 * @param seconds 时长(秒,0 < t <= TEST_PUMP_MAX_SEC)
 * @return true 成功启动;false 参数非法(已发 ERR)
 */
bool testPump(int pumpIdx, float seconds) {
  if (pumpIdx < 0 || pumpIdx >= CHANNEL_COUNT) {
    sendERR("BAD_PUMP_INDEX");
    return false;
  }
  if (seconds <= 0.0f || seconds > TEST_PUMP_MAX_SEC) {
    sendERR("BAD_TEST_DURATION");
    return false;
  }
  enterMode(MODE_TEST);
  deviceOff(valveToDevice(pumpIdx));   // 断开对应阀
  deviceOn(pumpToDevice(pumpIdx));    // 启动对应泵
  testStartTime = millis();
  testDuration  = (unsigned long)(seconds * 1000);
  testingPump = true;
  return true;
}

/**
 * sendStatus - 回复当前板状态
 * 格式: STATUS,<板号>,mode=...,relay=xxxxxx,servo=xxxxxx
 *   relay/servo 位图:6 字符,从设备 0 到设备 5,1=闭合/已 attach,0=断开/未 attach
 */
void sendStatus() {
  Serial.print("STATUS,");
  Serial.print(BOARD_ID);
  Serial.print(",mode=");
  switch (currentMode) {
    case MODE_IDLE:        Serial.print("IDLE"); break;
    case MODE_INFLATE_ALL: Serial.print("INFLATE_ALL"); break;
    case MODE_DEFLATE_ALL: Serial.print("DEFLATE_ALL"); break;
    case MODE_INFLATE_M:   Serial.print("INFLATE_M"); break;
    case MODE_TEST:        Serial.print("TEST"); break;
  }
  Serial.print(",relay=");
  for (int i = 0; i < 6; i++) {
    int s = digitalRead(RELAY_PINS[i]);
    bool closed = RELAY_ACTIVE_LOW ? (s == LOW) : (s == HIGH);
    Serial.print(closed ? "1" : "0");
  }
  Serial.print(",servo=");
  for (int i = 0; i < 6; i++) {
    Serial.print(servoAttached[i] ? "1" : "0");
  }
  Serial.println();
}

// ============ setup / loop ============

/**
 * setup - 上电初始化
 *
 * 继电器引脚设为输出,默认按 RELAY_ACTIVE_LOW 计算断开电平
 * Servo 引脚不主动 attach(节省 timer1 资源,需要时才 attach)
 */
void setup() {
  Serial.begin(9600);
  // 继电器引脚设为输出,默认断开(按 RELAY_ACTIVE_LOW 计算 OFF 电平)
  for (int i = 0; i < 6; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
  }
  allOff();  // 清除所有运行时标志(双重保险)
  // 上电就绪:回送本板 ID + 3 泵点充时长(用于 Python 端核对烧录参数)
  // 格式: READY,<板号>,<泵1时长>,<泵2时长>,<泵3时长>  (单位 ms)
  Serial.print("READY,");
  Serial.print(BOARD_ID);
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    Serial.print(",");
    Serial.print(INFLATE_M_MS_PER_PUMP[i]);
  }
  Serial.println();
}

/**
 * loop - 主循环
 *
 * 1. 接收并解析串口指令(模式互斥:进入新指令前先 allOff)
 * 2. 检查各模式是否到期(用 millis() 差值防回绕),到期自动断开
 */
void loop() {
  // ---- 1. 接收串口指令 ----
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.length() == 0) {
      // 空行忽略
    } else if (line == "STATUS") {
      sendStatus();

    } else if (line == "STOP_ALL") {
      allOff();
      sendACK("STOP_ALL");

    } else if (line == "INFLATE_M") {
      bool wasInInflateM = (currentMode == MODE_INFLATE_M);
      startInflateM();
      sendACK(wasInInflateM ? "INFLATE_M_REFRESH" : "INFLATE_M");

    } else if (line.startsWith("INFLATE_ALL,")) {
      // INFLATE_ALL,a  (前缀 "INFLATE_ALL," 长 12)
      float a = line.substring(12).toFloat();
      if (!validateDuration(a)) {
        sendERR("BAD_DURATION");
      } else {
        startInflateAll(a);
        sendACK("INFLATE_ALL");
      }

    } else if (line.startsWith("DEFLATE_ALL,")) {
      // DEFLATE_ALL,b  (前缀 "DEFLATE_ALL," 长 12)
      float b = line.substring(12).toFloat();
      if (!validateDuration(b)) {
        sendERR("BAD_DURATION");
      } else {
        startDeflateAll(b);
        sendACK("DEFLATE_ALL");
      }

    } else if (line.startsWith("TEST_PUMP,")) {
      // TEST_PUMP,pumpIdx,seconds  (前缀 "TEST_PUMP," 长 10)
      int firstComma = line.indexOf(',', 10);
      if (firstComma < 0) {
        sendERR("BAD_ARGS");
      } else {
        int pumpIdx   = line.substring(10, firstComma).toInt();
        float seconds = line.substring(firstComma + 1).toFloat();
        if (testPump(pumpIdx, seconds)) {
          sendACK("TEST_PUMP");
        }
        // 失败时 testPump 内部已发 ERR
      }

    } else {
      sendERR("UNKNOWN_CMD");
    }
  }

  // ---- 2. 到时自动断开(用 millis() 差值防回绕)----

  // 全充气到期
  if (inflatingAll && elapsedSince(inflateAllStartTime, inflateAllDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      deviceOff(pumpToDevice(i));
    }
    inflatingAll = false;
    currentMode = MODE_IDLE;
  }

  // 全放气到期
  if (deflatingAll && elapsedSince(deflateAllStartTime, deflateAllDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      deviceOff(valveToDevice(i));
    }
    deflatingAll = false;
    currentMode = MODE_IDLE;
  }

  // INFLATE_M:每泵独立时长到期(分别停泵)
  if (inflatingM) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      if (inflateMPumpActive[i] &&
          elapsedSince(inflateMPumpStart[i], INFLATE_M_MS_PER_PUMP[i])) {
        deviceOff(pumpToDevice(i));
        inflateMPumpActive[i] = false;
      }
    }
    // 本地看门狗:INFLATE_M_LOCAL_TIMEOUT_MS 内无 Python 刷新则全停
    if (elapsedSince(inflateMLastRefresh, INFLATE_M_LOCAL_TIMEOUT_MS)) {
      for (int i = 0; i < CHANNEL_COUNT; i++) {
        if (inflateMPumpActive[i]) {
          deviceOff(pumpToDevice(i));
          inflateMPumpActive[i] = false;
        }
      }
      inflatingM = false;
      currentMode = MODE_IDLE;
    }
  }

  // 测试到期
  if (testingPump && elapsedSince(testStartTime, testDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      deviceOff(pumpToDevice(i));
    }
    testingPump = false;
    currentMode = MODE_IDLE;
  }
}
