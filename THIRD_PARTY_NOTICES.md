# 第三方来源与版权声明

本项目（sky-with-you）由 **珂珂（KerunK336651）** 在以下开源项目的基础上
二次开发。本项目整体代码以 **GNU General Public License v3.0（GPL-3.0）**
发布；下列上游组件以 MIT 许可证授权，其版权与许可声明在此按要求保留。

## 上游项目

| 项目 | 提供的内容 | 许可证 |
|---|---|---|
| [akini a0315/sky-with-you](https://github.com/akini%20a0315/sky-with-you) | 原始项目框架、整体结构、README 基础 | MIT |
| [112Alan/sky-companion](https://github.com/112Alan/sky-companion) | 聊天引擎、长期记忆、回复处理、风格学习、联网搜索（`core/` 移植自其 `user_settings.py`、`ocr_agent.py`） | MIT |
| [Aevella/sky-pc-mcp-companion](https://github.com/Aevella/sky-pc-mcp-companion) | 执行层 MCP server 与键盘注入（`sky-mcp-server.py`） | MIT |
| [windhide/SkyMusicPlay-for-Windows](https://github.com/windhide/SkyMusicPlay-for-Windows) | **弹琴功能**：曲谱格式解析、15 键键位、和弦聚合、播放时序（`sky_song_player.py`）；已获作者书面授权 | **CC BY-NC 4.0**（见下） |

## 弹琴功能：windhide 项目（CC BY-NC 4.0，已获书面授权）

本项目的弹琴功能（`sky_song_player.py` 中曲谱解析、键位映射、和弦聚合与播放时序）
改编自 windhide 的「星星弹琴软件」。已通过作者所在用户群的临时会话取得其本人同意，
授权原话为 **"可以照搬标记出处就可以"**（聊天截图见
[`docs/credits/`](docs/credits/)）。

- 上游：https://github.com/windhide/SkyMusicPlay-for-Windows
- 作者：windhide
- 许可：**Creative Commons 署名-非商业性使用 4.0 国际（CC BY-NC 4.0）**
- 该部分代码**单独声明为 CC BY-NC 4.0**（文件头已标注来源/作者/链接/许可），与本项目
  其余自有代码的 GPL-3.0 相互独立；**不得用于商业用途**，转载或改编须署名 windhide。
- 本项目**未照搬**上游的 `PostMessageW` 后台发键实现，实际发键由本项目自有统一按键后端
  （Arduino 真 USB HID 优先 / pydirectinput 软件兜底）完成。
- **曲谱不随仓库分发**：本地曲库里的 `.txt` 多为社区用户转录，版权归原曲作者与转录者
  所有；作者授权软件并不等于授权分发每首曲谱。仓库仅在 `songs/` 提供少量自制/公有领域
  示例，其余曲谱请自行获取并放入 `user_data/songs/` 或用 `SKY_SONGS_DIR` 指定。

CC BY-NC 4.0 许可全文：https://creativecommons.org/licenses/by-nc/4.0/

## MIT 许可证声明

上述项目均依据 MIT 许可证授权：

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 模型权重

本项目附带的 YOLO 权重文件（`best.pt` 等，通过 GitHub Release 分发，**不包含在
git 仓库中**）以 **Creative Commons 署名-非商业性使用-相同方式共享 4.0 国际
（CC BY-NC-SA 4.0）** 许可证发布，详见 [MODEL_WEIGHTS_LICENSE.md](MODEL_WEIGHTS_LICENSE.md)。

## 版权

- 本项目新增与修改部分：Copyright (c) 2026 珂珂（KerunK336651）
- 上游原始部分：版权归上述各项目原作者所有
