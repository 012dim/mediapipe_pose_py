/* =====================================================================
 * 气泵控制 Uno (v4.4) - 3 泵 + 3 阀 = 6 设备
 *
 * 同一份代码烧录到 3 块 UNO(PUMP_A/B/C),只需修改下方 BOARD_ID
 *
 * ★ v4.4 关键变更(报告 107d463 复查):
 *   1. 拆分阀停止语义(报告 7.3):新增 stopPumpsImmediately / holdPressure
 *      / safeVent 三个独立函数,不再用单一 allOff() 表达所有停止场景。
 *   2. 新增 HOLD_ALL 命令(报告 7.4):停泵并关闭全部阀,保持当前气量。
 *      适配 VALVE_ENERGIZED_MEANS_OPEN 两种极性配置,修复"断电=阀打开"
 *      的语义冲突(原 STOP_ALL 在 false 配置下会放掉气球)。
 *   3. STOP_ALL 改为"立即停泵 + 安全放气"(safeVent)的明确语义,
 *      不再依赖"全断电"歧义。
 *   4. 串口改为非阻塞缓冲区解析(pollSerial + rxBuffer),不再使用
 *      阻塞式 Serial.readStringUntil('\n'),半条指令不再延迟泵到时停止
 *      (报告 6.3)。
 *   5. parseStrictUInt 增加 ULONG_MAX 溢出检查(报告 10.3)。
 *
 * ★ v4.3 关键变更(实机复查报告 c15a9b0):
 *   1. 弃用 <Servo.h> + writeMicroseconds(RC 脉冲),
 *      改用 analogWrite() 占空比 PWM(用户已确认电子开关为 PWM 类型)。
 *   2. 引脚重分配:6 路 S 信号全部位于 UNO 硬件 PWM 引脚(D3/5/6/9/10/11),
 *      继电器改用非 PWM 数字引脚(D2/4/7/8/12/13)。
 *   3. 新增 VALVE_ENERGIZED_MEANS_OPEN 配置,把"通电"和"阀开"语义解耦,
 *      修复"充气时电磁阀同时放气"的 P0 问题。
 *   4. 区分 normalPumpOff / emergencyPumpOff:
 *      正常停止先保持 PWM OFF 帧 PWM_OFF_HOLD_MS 再断继电器,
 *      紧急停止立即断继电器 + PWM=0(安全优先)。
 *   5. 严格数值解析:parseStrictUInt / parseStrictFloatSeconds 替代 toInt/toFloat,
 *      拒绝 "5.0abc" 等畸形输入(报告 10.2)。
 *
 * 硬件模型(v4.3): 占空比 PWM + 继电器供电隔离
 *   - 每台设备(泵/阀)占用 1 路继电器(供电通断)+ 1 路 PWM S 线(启停信号)
 *   - 继电器闭合 → 设备得电;analogWrite(PWM_ON_DUTY) → 启动
 *   - 上电默认:继电器断开(RELAY_ACTIVE_LOW 时输出 HIGH),PWM=0
 *
 * 设备映射(CHANNEL_COUNT=3,泵/阀交错排列):
 *   索引 0: 泵1  (D2  继电器 + D3  PWM)
 *   索引 1: 阀1  (D4  继电器 + D5  PWM)
 *   索引 2: 泵2  (D7  继电器 + D6  PWM)
 *   索引 3: 阀2  (D8  继电器 + D9  PWM)
 *   索引 4: 泵3  (D12 继电器 + D10 PWM)
 *   索引 5: 阀3  (D13 继电器 + D11 PWM)
 *
 * 串口协议(9600 baud,每行一条,以 '\n' 结尾):
 *   INFLATE_ALL,a    全部 3 泵充气 a 秒(0 < a <= 30)
 *   DEFLATE_ALL,b    全部 3 阀打开放气 b 秒(0 < b <= 30)
 *   INFLATE_M        9 泵(本板 3 泵)同步点充,每泵独立时长
 *                    (见 INFLATE_M_MS_PER_PUMP[3]);Python 每秒广播一次
 *                    刷新,本地最多持续 INFLATE_M_LOCAL_TIMEOUT_MS 防呆
 *   HOLD_ALL         停泵并关闭全部阀,保持当前气量(报告 7.4)
 *   STOP_ALL         立即停泵并打开全部阀安全放气(报告 7.4)
 *   STATUS           查询当前状态
 *   TEST_PUMP,i,t    测试第 i 号泵(0..2),持续 t 秒(0 < t <= 5)
 *
 * 响应(每条指令执行后回送):
 *   READY,<板号>,<泵1时长>,<泵2时长>,<泵3时长>
 *                       上电就绪(同时回送本板 3 泵点充时长,单位 ms,
 *                       用于 Python 端核对烧录参数是否与标定表一致)
 *   ACK,<板号>,<命令>                  指令执行成功
 *   ERR,<板号>,<原因>                  指令拒绝/失败
 *   STATUS,<板号>,mode=...,relay=xxxxxx,pwm=xxxxxx
 * ===================================================================== */

/* =====================================================================
 * ★★★ 用户可调参数区(在 Arduino IDE 中修改此处即可)★★★
 * ===================================================================== */

// ---- 板 ID ----
// 3 块 UNO 烧同一份代码,只需改此处为 "PUMP_A" / "PUMP_B" / "PUMP_C"
const char BOARD_ID[] = "PUMP_A";

// ---- 通道数(每板 3 泵 + 3 阀,固定为 3)----
const int CHANNEL_COUNT = 3;

// ---- 引脚映射(v4.3:PWM 全部走硬件 PWM 引脚 D3/5/6/9/10/11)----
// 设备索引 i (i=0..5):
//   偶数 i = 泵(i/2 + 1)
//   奇数 i = 阀(i/2 + 1)
// 注意:RELAY_PINS 必须全部为非 PWM 数字引脚,
//       PWM_PINS 必须全部为 UNO 硬件 PWM 引脚(3/5/6/9/10/11)。
const int RELAY_PINS[6] = {2, 4, 7, 8, 12, 13};      // 继电器引脚(非 PWM)
const int PWM_PINS[6]   = {3, 5, 6, 9, 10, 11};      // S 线 PWM 引脚(硬件 PWM)

// ---- 继电器触发电平 ----
// true  -> 输出 LOW 时继电器闭合(常见 5V 继电器模块)
// false -> 输出 HIGH 时继电器闭合
// ★ 必须用万用表实测:待机时 COM+NO 应为断路
const bool RELAY_ACTIVE_LOW = true;

// ---- ★ 阀通电语义(报告 8.4:必须由实物实测确定)★ ----
// true  -> 阀通电 = 排气口打开(放气);断电 = 关闭气路(保持气体)
// false -> 阀通电 = 关闭气路(保持气体);断电 = 排气口打开(放气)
// 实测方法:断开阀控制信号,只通电/断电继电器,观察气路状态
// 当前默认值 = true(继电器通电时阀打开排气)。
// 若实测发现"断电时阀放气",改为 false。
// ★ v4.4 重要:无论 true/false,HOLD_ALL 都会正确关阀保压,
//   STOP_ALL 都会正确开阀放气(已由 setValveOpen 自动映射)。
const bool VALVE_ENERGIZED_MEANS_OPEN = true;

// ---- PWM 占空比参数(0..255)----
// PWM_ON_DUTY  = 启动设备(满占空比 255)
// PWM_OFF_DUTY = 停止设备(零占空比 0)
// 若电子开关需要特定阈值(如 ≥200 才识别为 ON),在此调整
// 注意(报告 4.1):AVR 核心中 analogWrite(pin, 255) 实际为常高电平,
//                 不输出周期性方波;若电子开关要求持续方波,
//                 应改为 254 并用示波器确认波形。
const int PWM_ON_DUTY  = 255;
const int PWM_OFF_DUTY = 0;

// ---- PWM OFF 帧保持时长(报告 7.3:替代旧版 delay(5))----
// 正常停止时先写 PWM_OFF_DUTY 并保持该时长,确保电子开关
// 收到完整 OFF 帧再断继电器。5ms 太短不足一帧;50ms 较稳妥。
// 实测后可调小但不应小于 20ms。
const unsigned long PWM_OFF_HOLD_MS = 50;

// ---- 阀切换稳定时间(报告 8.4)----
// 关闭阀 → 等待 VALVE_SETTLE_MS → 启动泵(避免充气瞬间阀仍在放气)
// 默认 30ms,实测后调整(机械阀响应较慢可加大到 80ms)
const unsigned long VALVE_SETTLE_MS = 30;

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

// ---- 串口接收缓冲区(报告 6.3:非阻塞解析)----
const uint8_t RX_BUFFER_SIZE = 64;

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
  MODE_HOLD,        // v4.4:停泵保压
  MODE_SAFE_VENT,   // v4.4:停泵放气(STOP_ALL 后等待放气时长)
};
Mode currentMode = MODE_IDLE;

// ---- 运行时变量 ----
// PWM 不需要 attach/detach,只需记录当前每路 PWM 值
int pwmDuty[6] = {0, 0, 0, 0, 0, 0};           // 6 路 PWM 当前占空比
bool relayClosed[6] = {false, false, false, false, false, false};

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

// v4.4:STOP_ALL 安全放气计时(放气持续 DEFLATE_ALL_DEFAULT_MS 后自动关阀)
unsigned long safeVentStartTime = 0;
unsigned long safeVentDuration  = 0;
const unsigned long DEFLATE_ALL_DEFAULT_MS = 5000;  // STOP_ALL 后放气 5 秒

bool inflatingAll = false;
bool deflatingAll = false;
bool inflatingM   = false;
bool testingPump  = false;
bool safeVenting  = false;   // v4.4:STOP_ALL 安全放气进行中

// 串口接收缓冲区(报告 6.3)
char rxBuffer[RX_BUFFER_SIZE];
uint8_t rxLength = 0;


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
 * parseStrictUInt - 严格无符号整数解析(报告 10.3:增加溢出检查)
 *
 * 与 String.toInt() 不同:任一非数字字符返回 false,
 * 拒绝 "12abc" / "" / "-5" / "3.5" 等畸形输入。
 * 极长数字不再无符号回绕成较小值(ULONG_MAX 检查)。
 *
 * @param text  输入字符串
 * @param value 输出解析结果(仅当返回 true 时有效)
 * @return true 解析成功;false 输入非法
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

/**
 * parseStrictFloatSeconds - 严格浮点秒解析(报告 10.2)
 *
 * 允许格式:[0-9]+(.[0-9]*)? 或 [0-9]*.[0-9]+
 * 拒绝 "5.0abc" / "" / "." / "5.5.5" / "-1.0"
 *
 * @param text  输入字符串
 * @param value 输出秒数(仅当返回 true 时有效)
 * @return true 解析成功;false 输入非法
 */
bool parseStrictFloatSeconds(const String &text, float &value) {
  if (text.length() == 0) return false;
  bool seenDot = false;
  bool seenDigit = false;
  unsigned long intPart = 0;
  float fracVal = 0.0;
  float divisor = 10.0;
  for (unsigned int i = 0; i < text.length(); i++) {
    char c = text.charAt(i);
    if (c >= '0' && c <= '9') {
      seenDigit = true;
      if (!seenDot) {
        unsigned long digit = (unsigned long)(c - '0');
        // 整数部分溢出检查
        if (intPart > (ULONG_MAX - digit) / 10UL) return false;
        intPart = intPart * 10UL + digit;
      } else {
        fracVal += (float)(c - '0') / divisor;
        divisor *= 10.0;
      }
    } else if (c == '.' && !seenDot) {
      seenDot = true;
    } else {
      return false;  // 非法字符
    }
  }
  if (!seenDigit) return false;  // "." 不合法
  value = (float)intPart + fracVal;
  return true;
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
  relayClosed[deviceIdx] = on;
}

/**
 * setPwm - 写 PWM 占空比到 S 线引脚
 * @param deviceIdx 设备索引(0..5)
 * @param duty      占空比(0..255)
 *
 * v4.3:替代旧版 setServoPulse/writeMicroseconds。
 * analogWrite 在硬件 PWM 引脚上输出 ~490Hz(D5/D6 约 980Hz)方波。
 */
void setPwm(int deviceIdx, int duty) {
  if (deviceIdx < 0 || deviceIdx >= 6) return;
  analogWrite(PWM_PINS[deviceIdx], duty);
  pwmDuty[deviceIdx] = duty;
}

/**
 * deviceOn - 启动单台设备:闭合继电器 + 输出 PWM_ON_DUTY
 */
void deviceOn(int deviceIdx) {
  setRelay(deviceIdx, true);
  setPwm(deviceIdx, PWM_ON_DUTY);
}

/**
 * normalPumpOff - 正常停止:先 PWM OFF 保持 PWM_OFF_HOLD_MS,再断继电器
 *
 * 报告 7.3:旧版 delay(5) 不足一帧,电子开关可能未收到 OFF 帧。
 * 新版保持 PWM_OFF_HOLD_MS(默认 50ms)确保 OFF 帧完整发送。
 */
void normalPumpOff(int deviceIdx) {
  setPwm(deviceIdx, PWM_OFF_DUTY);
  delay(PWM_OFF_HOLD_MS);   // 保持 OFF 帧,让电子开关确实停止
  setRelay(deviceIdx, false);
}

/**
 * emergencyPumpOff - 紧急停止:立即断继电器 + PWM=0
 *
 * 安全优先:不等待 PWM_OFF_HOLD_MS,直接断电。
 * 用于 STOP_ALL / SAFE_STOP / 通信失联等紧急场景。
 */
void emergencyPumpOff(int deviceIdx) {
  setRelay(deviceIdx, false);   // 硬断电优先
  setPwm(deviceIdx, PWM_OFF_DUTY);
}

/**
 * deviceOff - 停止单台设备(通用接口,内部走正常停止)
 *
 * 用于模式切换/到时自动停止等正常场景。
 * 紧急路径应直接调用 emergencyPumpOff / emergencyStopAllPumps。
 */
void deviceOff(int deviceIdx) {
  normalPumpOff(deviceIdx);
}

/**
 * setValveOpen - 阀控制语义化抽象(报告 8.4)
 *
 * @param channel 通道索引(0..2)
 * @param open    true=打开排气阀(放气),false=关闭排气阀(保持气体)
 *
 * 根据 VALVE_ENERGIZED_MEANS_OPEN 配置自动映射:
 *   - true  (通电=排气):  open=true  -> deviceOn;  open=false -> deviceOff
 *   - false (通电=关闭):  open=true  -> deviceOff; open=false -> deviceOn
 *
 * 这层抽象解耦了"通电"和"阀开",修复旧版"充气时阀仍在放气"问题。
 * v4.4:HOLD_ALL / STOP_ALL 都通过本函数控制阀,两种极性均正确。
 */
void setValveOpen(int channel, bool open) {
  int device = valveToDevice(channel);
  bool energize = VALVE_ENERGIZED_MEANS_OPEN ? open : !open;
  if (energize) {
    deviceOn(device);
  } else {
    deviceOff(device);
  }
}

/**
 * setPumpRunning - 泵控制语义化抽象
 *
 * @param channel 通道索引(0..2)
 * @param running true=启动泵,false=停止泵
 */
void setPumpRunning(int channel, bool running) {
  int device = pumpToDevice(channel);
  if (running) {
    deviceOn(device);
  } else {
    deviceOff(device);
  }
}

/**
 * stopPumpsImmediately - 立即停止全部泵(报告 7.3)
 *
 * 两阶段硬断电,无 delay():
 *   1. 先断开全部泵继电器(硬件断电优先)
 *   2. 再清零全部泵 PWM 信号
 * 不触碰阀,允许调用者随后选择 holdPressure(关阀)或 safeVent(开阀)。
 */
void stopPumpsImmediately() {
  // 第一阶段:全部泵继电器断开
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setRelay(pumpToDevice(ch), false);
  }
  // 第二阶段:清零全部泵 PWM
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setPwm(pumpToDevice(ch), PWM_OFF_DUTY);
  }
}

/**
 * holdPressure - 停泵并关闭全部阀,保持当前气量(报告 7.3/7.4)
 *
 * 用于:
 *   - 动作恢复后停泵保压
 *   - 达到 GAS_MAX 后停泵保压
 *
 * 行为:
 *   1. 立即停全部泵(硬断电)
 *   2. 关闭全部排气阀(VALVE_ENERGIZED_MEANS_OPEN 自动映射极性)
 *   3. 清除所有运行标志,进入 MODE_HOLD
 *
 * 报告 7.2:无论 VALVE_ENERGIZED_MEANS_OPEN 是 true 还是 false,
 *          本函数都能正确关闭阀保压(因为通过 setValveOpen 自动映射)。
 */
void holdPressure() {
  stopPumpsImmediately();
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setValveOpen(ch, false);  // 关闭排气阀 = 保持气体
  }
  // 清除所有运行标志
  inflatingAll = false;
  deflatingAll = false;
  inflatingM   = false;
  testingPump  = false;
  safeVenting  = false;
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    inflateMPumpActive[i] = false;
  }
  currentMode = MODE_HOLD;
}

/**
 * safeVent - 停泵并打开全部阀,安全放气(报告 7.3/7.4)
 *
 * 用于:
 *   - STOP_ALL 指令(替代旧版 allOff 的歧义语义)
 *   - SAFE_STOP
 *   - 人离开触发的安全放气
 *
 * 行为:
 *   1. 立即停全部泵(硬断电)
 *   2. 打开全部排气阀(VALVE_ENERGIZED_MEANS_OPEN 自动映射极性)
 *   3. 清除所有运行标志,进入 MODE_SAFE_VENT
 *
 * 报告 7.2:无论 VALVE_ENERGIZED_MEANS_OPEN 是 true 还是 false,
 *          本函数都能正确打开阀放气。
 */
void safeVent() {
  stopPumpsImmediately();
  for (int ch = 0; ch < CHANNEL_COUNT; ch++) {
    setValveOpen(ch, true);   // 打开排气阀 = 放气
  }
  // 清除所有运行标志
  inflatingAll = false;
  deflatingAll = false;
  inflatingM   = false;
  testingPump  = false;
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    inflateMPumpActive[i] = false;
  }
  currentMode = MODE_SAFE_VENT;
  safeVentStartTime = millis();
  safeVentDuration  = DEFLATE_ALL_DEFAULT_MS;
  safeVenting = true;
}

/**
 * allOff - 兼容旧调用:全部 6 台设备停止 + 继电器断开
 *
 * v4.4 注:此函数不再用于 STOP_ALL(STOP_ALL 改走 safeVent)。
 * 仅供 setup() 初始化、模式切换前清理使用。
 * 报告 7.3:统一走 emergencyPumpOff,确保硬件断电优先。
 */
void allOff() {
  for (int i = 0; i < 6; i++) {
    emergencyPumpOff(i);
  }
  // 清除所有模式标志
  inflatingAll = false;
  deflatingAll = false;
  inflatingM   = false;
  testingPump  = false;
  safeVenting  = false;
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
  return (seconds > 0.0f) && (seconds <= MAX_DURATION_SEC);
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
 * 流程(报告 8.4 修复):
 *   1. 关闭所有排气阀(VALVE_ENERGIZED_MEANS_OPEN 自动映射极性)
 *   2. 等待 VALVE_SETTLE_MS 让阀稳定
 *   3. 启动所有泵
 */
void startInflateAll(float seconds) {
  enterMode(MODE_INFLATE_ALL);
  // 1. 关闭所有排气阀(防气路冲突)
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setValveOpen(i, false);
  }
  // 2. 等阀稳定(避免充气瞬间阀仍在放气)
  delay(VALVE_SETTLE_MS);
  // 3. 启动所有泵
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setPumpRunning(i, true);
  }
  inflateAllStartTime = millis();
  inflateAllDuration  = (unsigned long)(seconds * 1000);
  inflatingAll = true;
}

/**
 * startDeflateAll - 全部 3 阀打开放气,持续 seconds 秒
 *
 * 流程:
 *   1. 停止所有泵(正常停止:PWM OFF 保持 → 断继电器)
 *   2. 打开所有排气阀(VALVE_ENERGIZED_MEANS_OPEN 自动映射)
 */
void startDeflateAll(float seconds) {
  enterMode(MODE_DEFLATE_ALL);
  // 1. 停止所有泵
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setPumpRunning(i, false);
  }
  // 2. 打开所有排气阀
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setValveOpen(i, true);
  }
  deflateAllStartTime = millis();
  deflateAllDuration  = (unsigned long)(seconds * 1000);
  deflatingAll = true;
}

/**
 * startInflateM - 9 泵同步点充(本板 3 泵),每泵独立时长
 *
 * 行为:
 *   - 首次进入(currentMode != MODE_INFLATE_M):allOff + 关闭所有阀 + 启动 3 泵
 *   - 已在 INFLATE_M 模式(刷新):仅重启 3 泵周期(不断阀,因为阀已关闭)
 *
 * 每泵各自计时,到时自动停泵;Python 每秒广播一次重启周期
 * 本地看门狗:INFLATE_M_LOCAL_TIMEOUT_MS(1500ms)无刷新则全停
 */
void startInflateM() {
  if (currentMode != MODE_INFLATE_M) {
    enterMode(MODE_INFLATE_M);
    // 进入新周期:关闭所有排气阀(防气路冲突)
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setValveOpen(i, false);
    }
    // 等阀稳定
    delay(VALVE_SETTLE_MS);
  }
  // 每收到 INFLATE_M(无论首次还是刷新)都重启所有泵周期
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    setPumpRunning(i, true);
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
  // 关闭对应阀,等阀稳定,再启动泵(报告 8.4 语义)
  setValveOpen(pumpIdx, false);
  delay(VALVE_SETTLE_MS);
  setPumpRunning(pumpIdx, true);
  testStartTime = millis();
  testDuration  = (unsigned long)(seconds * 1000);
  testingPump = true;
  return true;
}

/**
 * sendStatus - 回复当前板状态
 * 格式: STATUS,<板号>,mode=...,relay=xxxxxx,pwm=xxxxxx
 *   relay 位图:6 字符,1=闭合,0=断开
 *   pwm  位图:6 字符,1=ON(PWM_ON_DUTY),0=OFF
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
    case MODE_HOLD:        Serial.print("HOLD"); break;
    case MODE_SAFE_VENT:   Serial.print("SAFE_VENT"); break;
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

  if (trimmed == "STATUS") {
    sendStatus();

  } else if (trimmed == "STOP_ALL") {
    // v4.4:STOP_ALL 改为"停泵 + 安全放气"明确语义(报告 7.4)
    safeVent();
    sendACK("STOP_ALL");

  } else if (trimmed == "HOLD_ALL") {
    // v4.4 新增:停泵保压(报告 7.4)
    holdPressure();
    sendACK("HOLD_ALL");

  } else if (trimmed == "INFLATE_M") {
    bool wasInInflateM = (currentMode == MODE_INFLATE_M);
    startInflateM();
    sendACK(wasInInflateM ? "INFLATE_M_REFRESH" : "INFLATE_M");

  } else if (trimmed.startsWith("INFLATE_ALL,")) {
    // INFLATE_ALL,a  (前缀 "INFLATE_ALL," 长 12)
    float a;
    if (!parseStrictFloatSeconds(trimmed.substring(12), a)) {
      sendERR("BAD_DURATION");
    } else if (!validateDuration(a)) {
      sendERR("BAD_DURATION");
    } else {
      startInflateAll(a);
      sendACK("INFLATE_ALL");
    }

  } else if (trimmed.startsWith("DEFLATE_ALL,")) {
    // DEFLATE_ALL,b  (前缀 "DEFLATE_ALL," 长 12)
    float b;
    if (!parseStrictFloatSeconds(trimmed.substring(12), b)) {
      sendERR("BAD_DURATION");
    } else if (!validateDuration(b)) {
      sendERR("BAD_DURATION");
    } else {
      startDeflateAll(b);
      sendACK("DEFLATE_ALL");
    }

  } else if (trimmed.startsWith("TEST_PUMP,")) {
    // TEST_PUMP,pumpIdx,seconds  (前缀 "TEST_PUMP," 长 10)
    int firstComma = trimmed.indexOf(',', 10);
    if (firstComma < 0) {
      sendERR("BAD_ARGS");
    } else {
      unsigned long pumpIdxUL;
      float seconds;
      if (!parseStrictUInt(trimmed.substring(10, firstComma), pumpIdxUL)) {
        sendERR("BAD_PUMP_INDEX");
      } else if (!parseStrictFloatSeconds(trimmed.substring(firstComma + 1), seconds)) {
        sendERR("BAD_TEST_DURATION");
      } else {
        int pumpIdx = (int)pumpIdxUL;
        if (testPump(pumpIdx, seconds)) {
          sendACK("TEST_PUMP");
        }
        // 失败时 testPump 内部已发 ERR
      }
    }

  } else {
    sendERR("UNKNOWN_CMD");
  }
}

// ============ setup / loop ============

/**
 * setup - 上电初始化
 *
 * 继电器引脚设为输出,默认按 RELAY_ACTIVE_LOW 计算断开电平
 * PWM 引脚设为输出,默认 PWM_OFF_DUTY(0)
 */
void setup() {
  Serial.begin(9600);
  // 继电器引脚设为输出,默认断开(按 RELAY_ACTIVE_LOW 计算 OFF 电平)
  for (int i = 0; i < 6; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
    relayClosed[i] = false;
  }
  // PWM 引脚默认输出 0(停止)
  for (int i = 0; i < 6; i++) {
    pinMode(PWM_PINS[i], OUTPUT);
    analogWrite(PWM_PINS[i], PWM_OFF_DUTY);
    pwmDuty[i] = PWM_OFF_DUTY;
  }
  rxLength = 0;
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
 * 1. 先检查各模式是否到期(报告 6.3:先于串口处理,确保准时停泵)
 * 2. 接收并解析串口指令(非阻塞 pollSerial)
 * 3. 再次检查到期(防止 pollSerial 解析复杂指令占用时间错过到时停止)
 */
void loop() {
  // ---- 1. 到时自动断开(用 millis() 差值防回绕)----

  // 全充气到期
  if (inflatingAll && elapsedSince(inflateAllStartTime, inflateAllDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setPumpRunning(i, false);
    }
    inflatingAll = false;
    currentMode = MODE_IDLE;
  }

  // 全放气到期:关闭所有阀(保持气体)
  if (deflatingAll && elapsedSince(deflateAllStartTime, deflateAllDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setValveOpen(i, false);
    }
    deflatingAll = false;
    currentMode = MODE_IDLE;
  }

  // INFLATE_M:每泵独立时长到期(分别停泵)
  if (inflatingM) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      if (inflateMPumpActive[i] &&
          elapsedSince(inflateMPumpStart[i], INFLATE_M_MS_PER_PUMP[i])) {
        setPumpRunning(i, false);
        inflateMPumpActive[i] = false;
      }
    }
    // 本地看门狗:INFLATE_M_LOCAL_TIMEOUT_MS 内无 Python 刷新则全停
    if (elapsedSince(inflateMLastRefresh, INFLATE_M_LOCAL_TIMEOUT_MS)) {
      for (int i = 0; i < CHANNEL_COUNT; i++) {
        if (inflateMPumpActive[i]) {
          setPumpRunning(i, false);
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
      setPumpRunning(i, false);
    }
    testingPump = false;
    currentMode = MODE_IDLE;
  }

  // v4.4:STOP_ALL 安全放气到期,关闭所有阀(回到 IDLE)
  if (safeVenting && elapsedSince(safeVentStartTime, safeVentDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setValveOpen(i, false);
    }
    safeVenting = false;
    currentMode = MODE_IDLE;
  }

  // ---- 2. 串口指令处理(报告 6.3:非阻塞 pollSerial)----
  pollSerial();

  // ---- 3. 二次检查到期(报告 6.3 推荐:防止 pollSerial 内部
  //         解析复杂指令占用时间错过到时停止)----
  if (inflatingAll && elapsedSince(inflateAllStartTime, inflateAllDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setPumpRunning(i, false);
    }
    inflatingAll = false;
    currentMode = MODE_IDLE;
  }
  if (testingPump && elapsedSince(testStartTime, testDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setPumpRunning(i, false);
    }
    testingPump = false;
    currentMode = MODE_IDLE;
  }
  if (safeVenting && elapsedSince(safeVentStartTime, safeVentDuration)) {
    for (int i = 0; i < CHANNEL_COUNT; i++) {
      setValveOpen(i, false);
    }
    safeVenting = false;
    currentMode = MODE_IDLE;
  }
}
