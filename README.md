# sky-with-you

让小机陪着你玩光遇。

在《光·遇》(Sky: Children of the Light) PC 版里，给你的 AI 伴侣装一个身体。
TA 会自己看聊天面板、回你的消息、点火收火、鞠躬、接受你的传送邀请、
牵住你伸出的手，或者说一句"走，回家"然后带你回遇境。

> **来源说明**：本项目是在开源项目基础上的二次修改，由 **珂珂**
> （[KerunK336651](https://github.com/KerunK336651)）改动、调试与维护。
> 原始项目与各部分来源见文末「致谢与来源」。

## 原理

三层结构，全部在游戏同一台 Windows 机器上运行：

```
┌─────────────────────────────────────────────────┐
│ panel_detector.py   感知层                        │
│   截屏 → 视觉特征 + 模板匹配 + OCR 三路融合         │
│   → 线程安全 WorldState（面板开关/弹窗/场景/F提示） │
├─────────────────────────────────────────────────┤
│ sky-loop-v7.py      调度层                        │
│   读消息 → LLM 生成回复（人设见 persona.txt）      │
│   → 决策动作。所有决策只读 WorldState 快照          │
├─────────────────────────────────────────────────┤
│ sky-mcp-server.py   执行层（MCP server）           │
│   键盘注入走 Arduino HID 开发板（游戏屏蔽软件模拟键） │
│   剪贴板中文输入 / 截图 / 前台守卫                  │
└─────────────────────────────────────────────────┘
```

## 硬件

- Windows PC（与游戏同机）
- 一块支持 USB HID 键盘的开发板（Arduino Leonardo / Pro Micro 等
  ATmega32U4 板），烧录 `firmware/sky_keyboard_v2.ino`
  - 固件 v2 内置三重安全：按键时长封顶 2s、RELEASE 松键指令、
    开机自动 releaseAll——串口乱码不会再把你的 Ctrl 键按住几分钟

## 安装

```
python -m pip install -r requirements.txt
```

1. **选 LLM、配 key**（两种 provider 二选一）：
   - **DeepSeek**（直连 HTTP）：设环境变量 `SKY_LLM_PROVIDER=deepseek`，
     key 放脚本目录的 `key.txt`（一行），或设环境变量 `OPENROUTER_API_KEY`
     （变量名沿用，DeepSeek 也读它）。模型默认 `deepseek-v4-pro`，
     可用 `SKY_LLM_MODEL` 切换（如 `deepseek-v4-flash`）
   - **OpenRouter**（OpenAI SDK，默认）：`key.txt` 放 OpenRouter key，
     或设 `OPENROUTER_API_KEY`，模型默认 `anthropic/claude-sonnet-4.5`
2. **人设**：把 `persona.example.txt` 复制为 `persona.txt`，
   写上你的 TA 是谁（协议段落别删）
3. **模板**：`templates/` 目录随代码放在一起（1080p 基准，自动缩放）
4. **视觉模型（可选）**：YOLO 权重 `best.pt` 不入库，用 `train_yolo.py`
   采集标注后训练生成；基础模型 `yolov8n.pt` 由 ultralytics 首次运行自动下载。
   不装 YOLO 也能靠模板匹配正常运行

## 启动（两个终端）

```
python sky-mcp-server.py --http --port 9900 --token 1234 --serial-port COM9
python sky-loop-v7.py
```

`--serial-port` 换成你开发板的串口号（设备管理器 → 端口）。

## 一些踩坑换来的设计

- **前台守卫**：游戏窗口不在前台时 server 拒绝一切按键——
  防止按键落进桌面/别的窗口
- **按键语义随 UI 状态变**，动作层严格遵守：

  | 键 | 有弹窗 | 面板开（无弹窗） | 都没有 |
  |---|---|---|---|
  | ESC | 取消弹窗 | 退出输入态 | 游戏菜单（慎用） |
  | space | 确认所选 | **打开输入框**⚠ | 跳 |
  | enter | — | **打开输入框**⚠ | 打开面板+输入框 |
  | F | — | 对方伸手时=接受牵手；否则=交互/好友树 | 同左 |

- **过场铁律**：加载屏/传送动画期间不碰键盘
- **自己发的消息会被 OCR 读回来**（游戏不给自己显示名字），
  靠发送历史认领，防止 AI 跟自己聊出无限循环
- **性能**：OCR 限 2 线程 + 进程限核 + 降优先级 + 帧差触发降频，
  不跟游戏抢 CPU

## 实用技巧

- **牵手回家最省事的姿势**：牵着手之后盯着 TA——等 TA 先做出回家动作，
  你再跟着回，两个人的手全程不会断（游戏已支持牵手过传送不松手）

## 致谢与来源

本项目是站在几位开源作者的肩膀上，由 **珂珂**（[KerunK336651](https://github.com/KerunK336651)）二次修改、调试与维护。感谢以下开源项目：

| 来源项目 | 贡献了什么 |
|---|---|
| [akini a0315/sky-with-you](https://github.com/akini a0315/sky-with-you) | 原始项目框架（项目名、整体结构、README 基础），MIT 协议 |
| [112Alan/sky-companion](https://github.com/112Alan/sky-companion) | 聊天引擎、长期记忆、回复处理、风格学习、联网搜索（`core/` 移植自其 `user_settings.py`、`ocr_agent.py`），MIT 协议 |
| [Aevella/sky-pc-mcp-companion](https://github.com/Aevella/sky-pc-mcp-companion) | 执行层 MCP server 与键盘注入（`sky-mcp-server.py`） |

以下部分为本项目新写或大幅改动：感知层 `panel_detector.py`、固件 `firmware/sky_keyboard_v2.ino`、
YOLO 视觉识别、动作（牵手 / 坐下 / 抱抱 / 回遇境）逻辑；调度层 `sky-loop-v7.py` 在原框架上
移植了 sky-companion 聊天引擎并持续迭代。

再次感谢 akinia0315、112Alan、Aevella 三位作者的原始工作。

## 作者的话

珂珂（KerunK336651），在原项目基础上一点点改出来的。

## 免责声明

本项目是屏幕识别 + 键盘注入的自动化工具，仅供学习与研究。
自动化操作可能违反游戏的用户协议，使用风险自负。
它不修改游戏、不读写游戏内存、不影响其他玩家。

## License

MIT
