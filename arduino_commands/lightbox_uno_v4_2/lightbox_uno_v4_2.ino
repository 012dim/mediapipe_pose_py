/*
 * 灯箱控制 Uno (LIGHT 板) v4.2.1
 * 接收串口指令控制 3 路继电器(3 个灯泡),非阻塞闪烁
 *
 * 报告 7.1:本文件从 lightbox_uno_commands.txt 拆出,
 * 可直接用 Arduino IDE 打开上传,无需复制粘贴。
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

/* =====================================================================
 * 用户可调参数区结束
 * ===================================================================== */

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

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < 3; i++) {
    pinMode(LIGHT_PINS[i], OUTPUT);
  }
  allOff();
  Serial.print("READY,");
  Serial.println(BOARD_ID);
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.length() == 0) {
      // 空行忽略
    } else if (line.startsWith("LIGHT_ON,")) {
      int id = line.substring(9).toInt();
      if (id >= 1 && id <= 3) {
        cancelFlash();
        setLight(id - 1, true);
        sendACK("LIGHT_ON");
      } else {
        sendERR("BAD_LIGHT_ID");
      }
    } else if (line.startsWith("LIGHT_OFF,")) {
      int id = line.substring(10).toInt();
      if (id >= 1 && id <= 3) {
        cancelFlash();
        setLight(id - 1, false);
        sendACK("LIGHT_OFF");
      } else {
        sendERR("BAD_LIGHT_ID");
      }
    } else if (line == "LIGHT_ALL_OFF") {
      cancelFlash();
      sendACK("LIGHT_ALL_OFF");
    } else if (line.startsWith("LIGHT_FLASH,")) {
      int times = line.substring(12).toInt();
      if (times <= 0 || times > 20) {
        sendERR("BAD_FLASH_COUNT");
      } else {
        startFlash(times);
        sendACK("LIGHT_FLASH");   // 先回 ACK(0.8s 内可达)
      }
    } else {
      sendERR("UNKNOWN_CMD");
    }
  }

  updateFlash();
}
