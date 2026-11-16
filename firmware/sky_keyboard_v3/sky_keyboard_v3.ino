// sky keyboard firmware v3 (2026-10-23)
// 协议与 v2 完全兼容（PING/RELEASE/PRESS/HOTKEY），新增 CHORD 多键和弦。
//
// v3 新增：
//   CHORD n k1 k2 ... kn ms
//     n 个键【同时按下】、保持 ms 毫秒后一起松开，用于弹琴的三键及以上真和弦。
//     n 取 1~6（USB HID 6KRO 上限），ms 封顶 2000。
//   例：CHORD 3 121 107 44 200   （同时按 y k , 保持 200ms）
//
// v2 已有（保留）：
//   PRESS kc ms         单键按下保持后松开
//   HOTKEY k1 k2 ms     双键同时按下保持后松开
//   PING                回 PONG
//   RELEASE             急救：松开所有键
//
// 安全特性（沿用 v2）：时长封顶 2 秒；开机先 releaseAll；接收缓冲限长；
// 任何一次按键结束都 releaseAll，兜住解析意外。
//
// 不升级 v3 也能弹琴：sky-mcp-server 检测到固件为 v2 时，单音走 PRESS、
// 双音走 HOTKEY、三音及以上自动改用软件模拟完整弹奏（不会丢音）。

#include <Keyboard.h>

String buf = "";
bool overflow = false;

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    delay(10);
  }
  delay(1000);
  Keyboard.begin();
  Keyboard.releaseAll();          // 复位/重插后，确保没有键悬着
  Serial.println("READY v3");
}

void handleCmd(String cmd) {
  cmd.trim();

  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  if (cmd == "RELEASE") {         // 急救指令：松开一切
    Keyboard.releaseAll();
    Serial.println("OK");
    return;
  }

  if (cmd.startsWith("PRESS ")) {
    int sp = cmd.indexOf(' ', 6);
    if (sp < 0) { Serial.println("ERR"); return; }
    int kc = cmd.substring(6, sp).toInt();
    long ms = cmd.substring(sp + 1).toInt();
    if (kc <= 0) { Serial.println("ERR badkey"); return; }
    ms = constrain(ms, 1, 2000);  // 乱码也压不死键盘
    Keyboard.press(kc);
    delay(ms);
    Keyboard.releaseAll();
    Serial.println("OK");
    return;
  }

  if (cmd.startsWith("HOTKEY ")) {
    String p = cmd.substring(7);
    int s1 = p.indexOf(' ');
    int s2 = p.indexOf(' ', s1 + 1);
    if (s1 < 0 || s2 < 0) { Serial.println("ERR"); return; }
    int k1 = p.substring(0, s1).toInt();
    int k2 = p.substring(s1 + 1, s2).toInt();
    long ms = p.substring(s2 + 1).toInt();
    if (k1 <= 0 || k2 <= 0) { Serial.println("ERR badkey"); return; }
    ms = constrain(ms, 1, 2000);
    Keyboard.press(k1);
    delay(10);
    Keyboard.press(k2);
    delay(ms);
    Keyboard.releaseAll();
    Serial.println("OK");
    return;
  }

  if (cmd.startsWith("CHORD ")) {
    // CHORD n k1 k2 ... kn ms
    String p = cmd.substring(6);
    int s0 = p.indexOf(' ');
    if (s0 < 0) { Serial.println("ERR"); return; }
    int n = p.substring(0, s0).toInt();
    if (n < 1 || n > 6) { Serial.println("ERR badn"); return; }

    int codes[6];
    int cur = s0 + 1;
    for (int i = 0; i < n; i++) {
      int sp = p.indexOf(' ', cur);
      if (sp < 0) { Serial.println("ERR"); return; }
      codes[i] = p.substring(cur, sp).toInt();
      if (codes[i] <= 0) { Serial.println("ERR badkey"); return; }
      cur = sp + 1;
    }
    long ms = p.substring(cur).toInt();   // 最后一段是保持时长
    ms = constrain(ms, 1, 2000);

    for (int i = 0; i < n; i++) {
      Keyboard.press(codes[i]);
      delay(8);                           // 极短间隔让 HID 报告逐键合上
    }
    delay(ms);
    Keyboard.releaseAll();
    Serial.println("OK");
    return;
  }

  Serial.println("ERR unknown");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (overflow) {
        Serial.println("ERR overflow");  // 超长=乱码，整行丢弃
      } else {
        handleCmd(buf);
      }
      buf = "";
      overflow = false;
    } else if (buf.length() < 96) {     // CHORD 6 键命令比 v2 长，缓冲提到 96
      buf += c;
    } else {
      overflow = true;
    }
  }
}
