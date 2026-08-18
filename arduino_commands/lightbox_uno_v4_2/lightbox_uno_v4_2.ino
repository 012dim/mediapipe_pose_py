/*
 * 灯箱控制 Uno (LIGHT 板) v4.2.2
 * 接收串口指令控制 3 路继电器(3 个灯泡),非阻塞闪烁
 *
 * 报告 7.1:本文件从 lightbox_uno_commands.txt 拆出,
 * 可直接用 Arduino IDE 打开上传,无需复制粘贴。
 *
 * v4.2.2 关键变更(报告 107d463 复查):
 *   1. 串口改为非阻塞缓冲区解析(pollSerial + rxBuffer),不再使用
 *      阻塞式 Serial.readStringUntil('\n')(报告 6.3)。
 *   2. 用 parseStrictUInt 替换宽松 toInt,拒绝 "LIGHT_ON,1abc" 等畸形
 *      指令(报告 10.1)。
 *   3. parseStrictUInt 增加 ULONG_MAX 溢出检查(报告 10.3)。
 *
 * 串口协议(9600 baud,每行一条指令,以 '\n' 结尾):
 *   LIGHT_ON,id     点亮指定灯(id=1/2/3)
 *   LIGHT_OFF,id    熄灭指定灯
 *   LIGHT_ALL_OFF   全部熄灭(闪烁进行中也能立即中断)
 *   LIGHT_FLASH,n   三灯闪烁 n 次(1..20)
 *
 * 响应(每条指令执行后回送):
 *   READY,LIGHT                 上电就绪
 *   ACK,LIGHT,<命令>            指令执行成功
 *   ERR,LIGHT,<原因>            指令拒绝/失败
 */

/* =====================================================================
 * ★★★ 用户可调参数区(在 Arduino IDE 中修改此处即可)★★★
 * ===================================================================== */

// ---- 板 ID ----
// 固定为 "LIGHT",Python 端 connect(expected_board_id="LIGHT") 会校验
const char BOARD_ID[] = "LIGHT";

// ---- 引脚定义 ----
// LIGHT_PINS:3 个灯泡的继电器引脚,分别对应灯1/灯2/灯3
//   引脚号 2/3/4 对应硬件接线表中的 D2/D3/D4
//   灯1 -> LEFT_HAND_UP(左手举起)
//   灯2 -> RIGHT_HAND_UP(右手举起)
//   灯3 -> BOTH_HANDS_UP(双手举起)
const int LIGHT_PINS[3] = {2, 3, 4};

// ---- 继电器触发电平 ----
// 常见 5V 继电器模块为【低电平触发】:
//   true  -> 输出 LOW 时继电器闭合(灯亮)
//   false -> 输出 HIGH 时继电器闭合(灯亮)
// 若你的继电器模块是高电平触发,改为 false
const bool RELAY_ACTIVE_LOW = true;

// ---- 闪烁参数(用于 LIGHT_FLASH 指令)----
// 每次闪烁:亮 FLASH_ON_MS 毫秒,灭 FLASH_OFF_MS 毫秒
const unsigned long FLASH_ON_MS = 300;   // 闪烁时灯亮时长(毫秒)
const unsigned long FLASH_OFF_MS = 300;  // 闪烁时灯灭时长(毫秒)

// ---- 串口接收缓冲区(报告 6.3:非阻塞解析)----
const uint8_t RX_BUFFER_SIZE = 32;

/* =====================================================================
 * 用户可调参数区结束
 * ===================================================================== */

/**
 * parseStrictUInt - 严格无符号整数解析(报告 10.1/10.3)
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
 * setLight - 控制单个灯泡亮/灭
 * @param idx  灯泡索引(0=灯1, 1=灯2, 2=灯3)
 * @param on   true=亮, false=灭
 */
void setLight(int idx, bool on) {
  if (idx < 0 || idx >= 3) return;   // 越界保护
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(LIGHT_PINS[idx], on ? LOW : HIGH);
  } else {
    digitalWrite(LIGHT_PINS[idx], on ? HIGH : LOW);
  }
}

void allOff() {
  for (int i = 0; i < 3; i++) setLight(i, false);
}

void allOn() {
  for (int i = 0; i < 3; i++) setLight(i, true);
}

// ---- 非阻塞闪烁状态机(报告 8.3)----
bool flashActive = false;
bool flashOnPhase = false;
int flashRemaining = 0;
unsigned long flashPhaseStart = 0;

void cancelFlash() {
  flashActive = false;
  flashOnPhase = false;
  flashRemaining = 0;
  allOff();
}

void startFlash(int times) {
  flashRemaining = times;
  flashActive = true;
  flashOnPhase = true;
  flashPhaseStart = millis();
  allOn();
}

void updateFlash() {
  if (!flashActive) return;

  unsigned long duration = flashOnPhase ? FLASH_ON_MS : FLASH_OFF_MS;
  if ((millis() - flashPhaseStart) < duration) return;

  flashPhaseStart = millis();

  if (flashOnPhase) {
    allOff();
    flashOnPhase = false;
  } else {
    flashRemaining--;
    if (flashRemaining <= 0) {
      allOff();
      flashActive = false;
      return;
    }
    allOn();
    flashOnPhase = true;
  }
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

// ============ 串口非阻塞解析(报告 6.3)============

// 串口接收缓冲区
char rxBuffer[RX_BUFFER_SIZE];
uint8_t rxLength = 0;

/**
 * pollSerial - 非阻塞串口接收并解析指令
 *
 * 报告 6.3:旧版 Serial.readStringUntil('\n') 在收到半条数据时会
 * 阻塞约 1 秒,新版使用固定缓冲区,逐字符读取,遇到 '\n' 才解析。
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

  if (trimmed.startsWith("LIGHT_ON,")) {
    // LIGHT_ON,id  (前缀 "LIGHT_ON," 长 9)
    unsigned long idUL;
    if (!parseStrictUInt(trimmed.substring(9), idUL)) {
      sendERR("BAD_LIGHT_ID");
    } else if (idUL < 1 || idUL > 3) {
      sendERR("BAD_LIGHT_ID");
    } else {
      cancelFlash();
      setLight((int)idUL - 1, true);
      sendACK("LIGHT_ON");
    }

  } else if (trimmed.startsWith("LIGHT_OFF,")) {
    // LIGHT_OFF,id  (前缀 "LIGHT_OFF," 长 10)
    unsigned long idUL;
    if (!parseStrictUInt(trimmed.substring(10), idUL)) {
      sendERR("BAD_LIGHT_ID");
    } else if (idUL < 1 || idUL > 3) {
      sendERR("BAD_LIGHT_ID");
    } else {
      cancelFlash();
      setLight((int)idUL - 1, false);
      sendACK("LIGHT_OFF");
    }

  } else if (trimmed == "LIGHT_ALL_OFF") {
    cancelFlash();
    sendACK("LIGHT_ALL_OFF");

  } else if (trimmed.startsWith("LIGHT_FLASH,")) {
    // LIGHT_FLASH,n  (前缀 "LIGHT_FLASH," 长 12)
    unsigned long timesUL;
    if (!parseStrictUInt(trimmed.substring(12), timesUL)) {
      sendERR("BAD_FLASH_COUNT");
    } else if (timesUL < 1 || timesUL > 20) {
      sendERR("BAD_FLASH_COUNT");
    } else {
      startFlash((int)timesUL);
      sendACK("LIGHT_FLASH");   // 先回 ACK(0.8s 内可达)
    }

  } else {
    sendERR("UNKNOWN_CMD");
  }
}

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < 3; i++) {
    pinMode(LIGHT_PINS[i], OUTPUT);
  }
  allOff();
  rxLength = 0;
  Serial.print("READY,");
  Serial.println(BOARD_ID);
}

void loop() {
  pollSerial();
  updateFlash();
}
