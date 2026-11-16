# sky-with-you

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23618715.svg)](https://doi.org/10.5281/zenodo.23618715)

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

## 功能特性

**看 · 视觉感知**
- 截屏 + 视觉特征 + 模板匹配 + OCR 三路融合，维护线程安全的世界状态
- YOLO 图标识别：随仓库提供 28 类权重（互动动作、好友树、光翼、聊天框、乐器、设置菜单等），
  另有 22 类、12 类、7 类历史版本；不装 YOLO 时自动退回模板匹配
- 多 OCR 引擎读取聊天文字，支持 DirectML 显卡加速
- 场景识别：你问"这是哪里"时自动判断当前地图
- 视觉大模型（VLM）按需调用，理解画面内容

**想 · 对话与记忆**
- 接入 DeepSeek / OpenRouter 多家模型，可随时切换
- 自己读聊天面板、理解上下文并回复
- 金字塔式长期记忆：短期对话 + 日记 / 周记 / 月记 / 年记，按周自动归档
- 学习并模仿你的说话风格
- 本地知识库 + 联网搜索，回答游戏相关问题

**做 · 动作执行**
- Arduino HID 硬件按键注入，绕开游戏对软件模拟键的屏蔽
- 硬件热插拔自适应：插上板子自动用硬件；没插 / 中途拔出自动退回软件模拟
  （pydirectinput 扫描码，与 SkyAuto 同款 SendInput），后台每 30 秒检测，插回自动切回硬件，全程不退出
- 自主接受牵手、抱抱、背背、击掌、碰拳、传送邀请
- 点火收火、鞠躬、坐下、跟随
- 有翼号可开启光翼自动拾取（默认关闭，无翼号必须保持关闭，见[下文](#光翼自动拾取有翼号--无翼号)）
- 牵手回家；说一句"回家"就带你回遇境
- 剪贴板中文输入，动作后自动重开面板
- **弹琴**：在游戏里打开乐器后，可按 Sky Music Studio 曲谱（`.txt`）自动演奏，
  单音 / 和弦、暂停 / 继续 / 停止 / 倍速，走与动作相同的硬件优先 + 软件兜底按键后端
  （曲谱格式与键位改编自 [windhide/SkyMusicPlay-for-Windows](https://github.com/windhide/SkyMusicPlay-for-Windows)，已获作者授权，见[下文](#弹琴)）

**稳 · 安全与工程**
- 前台守卫：游戏不在前台不按键；过场动画期间不碰键盘
- 固件三重安全：按键时长封顶、松键指令、开机释放全部按键
- 自己消息认领，防止 AI 跟自己聊出循环
- Web 调试台：单步触发动作、实时看画面与检测结果
- 配套数据采集 / 标注工具与完整离线测试套件

## 路线图

- [x] 自主**接受**牵手、抱抱、背背、击掌、碰拳、传送邀请
- [x] 主动发起抱抱（好友树导航）
- [x] 弹琴功能本体（解析曲谱 / 播放 / 和弦 / 软硬件发键）
- [ ] 把弹琴开关放进快捷动作面板（或由 AI 用 `[ACT]` 触发；弹不弹、弹哪首仍由星河判断，不写死）
- [ ] **主动发起**更多互动（摸头、打闹、熊抱、公主抱、共舞等）
      —— 前提是先在游戏里把陪伴号（星河）与玩家的**好友树全部解锁**；
      解锁后在 `FRIEND_TREE_NAV` 里补方向键序列即可
- [ ] YOLO 识别确认/取消按钮、好友树解锁状态，替代像素检测

## 硬件

- Windows PC（与游戏同机）
- 一块支持 USB HID 键盘的开发板（Arduino Leonardo / Pro Micro 等
  ATmega32U4 板），烧录 `firmware/sky_keyboard_v2.ino`（或可选的 v3）
  - 固件 v2 内置三重安全：按键时长封顶 2s、RELEASE 松键指令、
    开机自动 releaseAll——串口乱码不会再把你的 Ctrl 键按住几分钟
  - 固件 v3（`firmware/sky_keyboard_v3/`）在 v2 基础上新增 `CHORD` 命令，
    支持三键及以上真和弦，用于弹琴；**不烧 v3 也能弹**，三音及以上会自动
    改用软件模拟完整弹奏（不丢音）

### 硬件按键 vs 软件模拟（重要，先看这条）

程序有两种按键方式，默认 `--input-backend auto` 自动选择：

| | 硬件（Arduino，推荐） | 软件模拟（pydirectinput） |
| --- | --- | --- |
| 原理 | 真 USB HID 键盘，和物理键盘走完全相同的驱动，系统/游戏无法区分 | Win32 `SendInput` + 硬件扫描码 |
| 可靠性 | 几乎不吞键，最高 | 已做 SendInput 失败自动重试（默认 3 次）+ 最小按压时长（默认 50ms），**但仍可能被系统或游戏吞键**，可靠性不如硬件 |
| 热插拔 | 运行中拔出自动退回软件、插回 30 秒内自动切回 | 无需硬件 |

- **没有开发板也能直接用**：探测不到硬件时自动退回软件模拟，不影响启动；
  但请接受软件方案可能偶发吞键（按键没被游戏识别），这是操作系统模拟输入的固有局限，
  不是 bug。追求稳定请准备一块 ATmega32U4 板（成本很低）。
- 强制指定后端：`--input-backend pydirectinput`（软件）/ `arduino`（硬件），
  或设环境变量 `SKY_INPUT_BACKEND`；软件最小按压 `SKY_SOFT_MIN_PRESS_MS`、
  重试次数 `SKY_SOFT_SEND_RETRIES` 可调。

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
4. **视觉模型**：预训练权重随仓库放在 [`models/yolo/`](models/yolo/)，
   最新为 `sky_v8_28cls_best.pt`（28 类），另含同 28 类的 `sky_v7_28cls_best.pt`，
   以及 22 类（`sky_v6_22cls_best.pt`）、12 类（`sky_v5_12cls_best.pt`）、
   7 类（`sky_v4_7cls_best.pt`）历史版本；权重以 CC BY-NC-SA 4.0 发布。
   另提供 v8 的 ONNX（`sky_v8_28cls_best.onnx`）与 OpenVINO
   （`sky_v8_28cls_best_openvino_model/`）两种部署格式，便于无 PyTorch 或 CPU 加速场景使用。
   把对应权重放到程序默认模型路径即可，也可用 `train_yolo.py` 自行采集
   标注后训练。基础模型 `yolov8n.pt` 由 ultralytics 首次运行自动下载。
   不装 YOLO 也能靠模板匹配正常运行。

   **作者署名与完整性校验**：官方权重内嵌作者署名（`.pt` 的 `_attribution` 字段，
   记录作者 KerunK336651、来源仓库、CC BY-NC-SA 4.0 许可与发布版本），
   并附 `models/yolo/SHA256SUMS.txt`。下载后可校验是否为官方未篡改版本：
   `py tools/hash_models.py models/yolo --check`；给自己新训练的权重延续署名：
   `py tools/stamp_model.py 权重路径.pt --release vX.X`。

   > **安全提示**：`.pt` 基于 pickle，加载时可执行代码。请只使用本仓库
   > 或 Release 页的权重，不要使用第三方网盘、群文件或不明链接里的 `best.pt`，
   > 并先用上面的 SHA256 校验。

## 启动（两个终端）

```
python sky-mcp-server.py --http --port 9900 --token 1234 --serial-port COM9
python sky-loop-v7.py
```

`--serial-port` 换成你开发板的串口号（设备管理器 → 端口）。
不传 `--serial-port` 时会按 USB VID / 描述自动扫描 Arduino（换电脑、COM 号变化也免配置）。

**硬件热插拔自适应（默认开启）**：`--input-backend` 默认 `auto`，即「Arduino 硬件优先」——

- 插着板子：用 Arduino 真键盘注入（游戏收得到，也最不容易被屏蔽）
- 没插板子 / 中途被拔出：日志窗口打印提示后自动退回软件模拟按键
  （`pydirectinput`，底层是 Win32 `SendInput` + 硬件扫描码，参考了 SkyAuto 的做法；
  没有 pydirectinput 时再退回 pyautogui），**程序不报错、不退出**
- 后台每 30 秒检测一次硬件：拔出立即退软件，插回自动连回硬件，无需重启
- 硬件掉线的**当次按键**会立刻用软件补发，不会让当前动作半途失败
- 想强制只用软件：`--input-backend pydirectinput`；后台守护仅在 auto/arduino 时启动

## Web 调试台（手动单步调试）

不想跑完整自动循环、只想单独测试某个动作时，用 `debug_web.py`：

```
python sky-mcp-server.py --http --port 9900 --token 1234 --serial-port COM9
python debug_web.py
```

浏览器打开 http://127.0.0.1:9911 ，可以：

- 单独触发任意按键、开/关面板、发消息、回遇境、发起抱抱
- 实时看感知状态（面板 / 好友树 / 弹窗 / F 提示）、YOLO 检测结果与当前画面
- 滚动查看日志

动作执行前会自动把游戏切到前台；游戏不在前台时按键被安全拦截。
调试台只复用主程序的动作函数，不改动主程序。

## 弹琴

在游戏里打开任意一件 **15 键键盘乐器**（竖琴、笛子等）后，星河可以按曲谱自动弹奏，
支持单音、和弦、暂停 / 继续 / 停止、倍速。曲谱格式与 15 键键位改编自
[windhide/SkyMusicPlay-for-Windows](https://github.com/windhide/SkyMusicPlay-for-Windows)
（**已获作者书面授权："可以照搬标记出处就可以"**，截图见
[`docs/credits/`](docs/credits/)），发键走本项目自有的统一按键后端（硬件优先、软件兜底）。

**曲谱格式**：Sky Music Studio 通用 `.txt`（JSON，内含 `songNotes`，音符按毫秒 `time`
排列、`key` 为 `1KeyN`/`2KeyN`、可带 `duration`；同一时刻多音自动聚合成和弦）。15 键
对应三排按键（自然七声音阶，Key0=C4…Key14=C6）：

```
上排  y u i o p
中排  h j k l ;
下排  n m , . /
```

**曲谱从哪来（按优先级，自动去重）**：

1. `songs/`：随仓库附带的少量自制 / 公有领域示例（如《小星星》、C 大调音阶按键测试）；
2. `user_data/songs/`：你自己的曲谱（已被 gitignore，不会上传）；
3. 环境变量 `SKY_SONGS_DIR`：分号分隔的额外曲谱目录；
4. 自动探测本机已安装的「星星弹琴软件」曲库（`桌面\sky-music\Sky_Music\resources\
   systemMusic` 与 `myImport`），**无需任何配置就能直接弹它库里的上千首**。

**关于版权（重要）**：曲库里的歌曲大多是社区玩家转录，版权归原曲作者与转录者所有，
作者授权软件并不等于授权分发每首曲谱。因此**本仓库不打包曲谱库**，只放可自由使用的
示例；想多弹请自行安装曲谱软件或下载曲谱放到上述目录。

**怎么调用**：MCP 已提供 4 个工具（`list_songs` / `play_song` / `stop_song` /
`song_status`），`play_song` 传歌名（精确或子串）、`speed`（0.2–4）即可。也可以在 Web
调试台手动调用。"弹不弹、弹哪首"最终由星河判断，不写死脚本；把弹琴开关放进快捷动作
面板是后续待办（见路线图）。

**同名多版本怎么办**：曲库合并了上万首社区谱后，一首歌常常有好几个版本
（《小星星》就有示例版、2 分 49 秒版、23 秒版、变奏曲版）。这时星河**会先问你要哪
个**，回复序号就行（也可以直接念版本名）；被重复上传的同一份（时长和音符数都一样）
会自动合并，不会拿重复项烦你。只有一个版本时直接开弹，不多问。

> 软件模拟后端偶发吞键在弹琴时可能表现为漏音，追求稳定请用 Arduino 硬件；
> 三键及以上和弦建议烧录 v3 固件（未烧也会自动用软件完整弹奏，不丢音）。

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

## 光翼自动拾取（有翼号 / 无翼号）

检测到 `wing_light`（小金人拾取图标，底部提示"按 F 获取光之翼"）后，可自动按 F 拾取。

**默认关闭**，而且需要**两把钥匙同时到位**才会生效：

| 钥匙 | 含义 | 默认值 |
|---|---|---|
| ① `account_winged` | 这个号是不是**有翼号** | `false`（按无翼号处理，最保守） |
| ② `auto_pickup_wing` | 是否**开启**自动拾取 | `false`（关闭） |

- 两把钥匙都存在本地 `user_data/settings.json`（**已 gitignore，不进仓库**），
  所以任何人 clone 下来都是「有翼 = 否 + 拾取 = 关」的安全默认值。
- 改设置：双击 **`setup_wing.bat`**（或 `py setup_wing.py`），只问两个问题，回车即安全默认。
- 环境变量 `SKY_AUTO_PICKUP_WING=1` 可以临时替代钥匙②，但**替代不了钥匙①** ——
  账号没标记为有翼号时，就算设了 env 也会**强制关闭**并在启动日志里告警。
- 启动日志会打印一行说明，例如：
  `光翼自动拾取=关闭（settings.json 未开启；账号已标记有翼，想拾取就开这个开关）`

- **无翼号（永久无翼）必须保持关闭**：无翼角色一旦拾取光翼就无法再回到无翼状态，不可逆。
  默认关闭 + 双钥匙就是为了保证这一点，clone 下来什么都不用改就是安全的。
- **有翼号**：跑一次 `setup_wing.bat`，① 选 y（有翼）、② 选 y（开启），靠近光翼即自动拾取。

拾取为直接按 F，不经过 AI、不说话（星河当前不转视角/走动，只有靠近光翼才会触发）。
首次按 F 若没生效，会每 2.5 秒补按一次、最多 3 次（可用 `SKY_WING_PICKUP_RETRY_INTERVAL` /
`SKY_WING_PICKUP_MAX_PRESSES` 调整）；图标消失即拾取完成，同一光翼只拾一次。

> 与光翼硬门的关系：开关关闭时维持硬门拦截（附近有光翼时所有 F 都不按，保护无翼号）；
> 开关开启时只放行"这次拾取"的 F，其余安全逻辑不变。

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
| [windhide/SkyMusicPlay-for-Windows](https://github.com/windhide/SkyMusicPlay-for-Windows) | **弹琴**：曲谱格式、15 键键位、和弦聚合与播放时序（`sky_song_player.py`），**CC BY-NC 4.0，已获作者授权** |

以下部分为本项目新写或大幅改动：感知层 `panel_detector.py`、固件 `firmware/sky_keyboard_v2.ino`
与 `firmware/sky_keyboard_v3/`（新增多键和弦）、弹琴模块 `sky_song_player.py`、
YOLO 视觉识别、动作（牵手 / 坐下 / 抱抱 / 弹琴 / 回遇境）逻辑；调度层 `sky-loop-v7.py` 在原框架上
移植了 sky-companion 聊天引擎并持续迭代。

再次感谢 akinia0315、112Alan、Aevella 三位作者的原始工作，以及 windhide 授权弹琴部分的复用。
弹琴相关代码为 CC BY-NC（署名-非商业），其余自有代码为 GPL-3.0，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 引用与存档

每个 Release 都在 [Zenodo](https://zenodo.org)（CERN 托管的学术存档平台）做了不可篡改的第三方存档，并分配 DOI。

- **概念 DOI（始终指向最新版本，推荐引用）**：[10.5281/zenodo.23618715](https://doi.org/10.5281/zenodo.23618715)
- **v1.0 版本 DOI**：[10.5281/zenodo.23618716](https://doi.org/10.5281/zenodo.23618716)

归档内容为该 Release 对应的 GitHub 源码快照（含随仓库发布的 YOLO 权重）。引用时可使用：

```bibtex
@software{kerunk_sky_with_you,
  author = {KerunK336651 (珂珂)},
  title  = {sky-with-you: 光遇 AI 伴侣「星河」},
  url    = {https://github.com/KerunK336651/sky-with-you},
  doi    = {10.5281/zenodo.23618715}
}
```

> 权重文件另有内嵌作者署名（`.pt` 的 `_attribution` 字段）与 SHA256 校验，见上文「作者署名与完整性校验」。

## 作者的话

珂珂（KerunK336651），在原项目基础上一点点改出来的。

## 免责声明

本项目是屏幕识别 + 键盘注入的自动化工具，**仅供学习与研究**。
自动化操作可能违反游戏的用户协议，使用风险自负。
它不修改游戏、不读写游戏内存、不影响其他玩家。

作者明确反对将本项目用于违反《光·遇》用户协议、破坏游戏公平、
伤害其他玩家或任何商业性运营（代练、批量账号等）的用途。

## License

- **代码**：[GNU General Public License v3.0（GPL-3.0）](LICENSE)
  —— 允许使用、修改与分发，但衍生作品必须保持开源并以相同协议发布。
- **模型权重**：[CC BY-NC-SA 4.0](MODEL_WEIGHTS_LICENSE.md)
  —— 署名原作者、非商业用途、衍生权重须以相同协议共享。
- **第三方来源**：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，
  上游项目以 MIT 许可证授权，其版权声明已保留。

> 版权所有 © 2026 珂珂（KerunK336651）
> 训练所用游戏截图版权归 thatgamecompany / TGC 所有，完整数据集不公开分发。
