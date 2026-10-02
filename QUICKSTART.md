# Sky-with-you v7.1 快速开始（无硬件模式）

## 项目位置
`C:\Users\kgklg\Downloads\test\sky-with-you-main\`

## 第一步：配置 API Key

二选一：

**方式A：创建 key.txt 文件**
在项目目录创建 `key.txt`，内容就是你的 API Key（OpenRouter 或 DeepSeek）。

**方式B：设置环境变量**
编辑 `start_loop_no_hardware.bat`，取消注释并填入：
```
set OPENROUTER_API_KEY=你的API_Key
```

> OpenRouter Key 获取：https://openrouter.ai/keys
> DeepSeek Key 获取：https://platform.deepseek.com/

## 第二步：配置人设

编辑 `persona.txt`，写上你的 TA 是谁、什么性格、和你什么关系。
文件末尾保留 `[CHAT]/[ACT]/[KEY]/[IDLE]` 协议段落不要删。

## 第三步：启动（两个终端）

**终端1：MCP 服务器**
```
双击 start_mcp_no_hardware.bat
```
看到 `MCP server running on http://0.0.0.0:9900` 即成功。

**终端2：主调度循环**
```
双击 start_loop_no_hardware.bat
```
看到 `光遇循环调度 v7.1` 和窗口信息即成功。

## 切换 LLM 模型

编辑 `start_loop_no_hardware.bat`：

**用 DeepSeek（便宜快）：**
```
set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-v4-pro
```

**用 OpenRouter（Claude 等）：**
```
set SKY_LLM_PROVIDER=openrouter
set SKY_LLM_MODEL=anthropic/claude-sonnet-4.5
```

## 无硬件模式说明

| 功能 | 状态 |
|------|------|
| 屏幕截图 + OCR 识别聊天 | ✅ 正常 |
| 聊天面板/弹窗/加载检测 | ✅ 正常 |
| LLM 回复生成 | ✅ 正常 |
| 长期记忆自动整理 | ✅ 正常 |
| 联网搜索 | ✅ 正常 |
| 防重复 + 回复打磨 | ✅ 正常 |
| 按键发送到游戏 | ⚠️ 可能不生效（光遇可能屏蔽软件模拟键） |

> 即使按键不生效，程序日志会完整显示 AI 生成的回复、搜索结果、记忆整理等，
> 可以用来验证聊天引擎的全部逻辑。后续购买 Arduino Leonardo/Pro Micro
> 开发板后，把 `SKY_INPUT_BACKEND` 改回 `arduino` 即可。

## 数据文件

运行后自动生成在 `user_data/` 目录：
- `memory.json` — 长期记忆
- `search_knowledge.json` — 持久化搜索知识
- `style_knowledge.json` — 学到的说话风格

## 有硬件时

1. 烧录 `firmware/sky_keyboard_v2.ino` 到 Arduino Leonardo/Pro Micro
2. 编辑启动脚本，把 `SKY_INPUT_BACKEND` 改为 `arduino`
3. MCP 服务器启动时加 `--serial-port COM9`（换成你的串口号）
