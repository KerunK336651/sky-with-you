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
