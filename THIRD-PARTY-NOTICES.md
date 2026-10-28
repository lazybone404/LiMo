# THIRD-PARTY NOTICES

LiMo 发行版（安装包）内嵌以下第三方组件的代码与资源。所有第三方代码版权归其各自原作者所有，
LiMo 不主张任何归属。各许可证全文随发行包附带，位置见各节「全文位置」。

## 1. 后端 Python 组件

后端 `LiMoBackend.exe`（PyInstaller 打包）内嵌以下 Python 包。许可证全文随包附带：

- **`_internal/<pkg>.dist-info/licenses/`** — 各包自带的许可证（collect_all 自动收集，各 dist-info）
- **`_internal/licenses/`** — 统一汇总的许可证全文（GPL-3.0 / Apache-2.0 / MIT）

完整依赖清单与许可证见仓库 [docs/ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) 第二节。

| 组件 | 许可证 | 全文位置 |
|------|--------|---------|
| magika | Apache-2.0 | `_internal/licenses/Apache-2.0.txt` |
| markitdown | MIT | `_internal/licenses/MIT-markitdown.txt` |
| feedparser | BSD-2-Clause | `_internal/feedparser-*.dist-info/licenses/` |
| 其余包（aiohttp/dashscope/numpy/lxml/cryptography 等） | 各许可证 | `_internal/*.dist-info/licenses/` |

## 2. 前端组件

前端编译产物 `frontend/dist` 内嵌以下运行时依赖（Vite 打包）。许可证全文见各项目官方仓库。

| 组件 | 许可证 |
|------|--------|
| react / react-dom | MIT |
| react-markdown + remark-gfm / remark-math + rehype-katex / rehype-slug | MIT |
| zustand | MIT |
| markmap-common / markmap-lib / markmap-view | MIT |
| github-markdown-css | MIT |
| @tailwindcss/vite | MIT |
| react-syntax-highlighter | MIT |
| lucide-react | ISC |
| react-medium-image-zoom | BSD-3-Clause |

> KaTeX / highlight.js / d3 的离线资源与许可证见下节。

## 3. 离线静态资源

`_internal/static/` 内嵌以下库的编译产物（导出 HTML 用）。许可证全文随包附带于 `_internal/static/licenses/`：

| 资源 | 许可证 | 全文位置 |
|------|--------|---------|
| KaTeX（CSS/JS/字体） | MIT | `_internal/static/licenses/KaTeX-LICENSE` |
| markmap-lib / markmap-view | MIT | `_internal/static/licenses/markmap-{lib,view}-LICENSE` |
| d3.min.js | ISC | `_internal/static/licenses/d3-LICENSE` |
| highlight.js（经 react-syntax-highlighter 内联） | BSD-3-Clause | 见 [highlight.js](https://github.com/highlightjs/highlight.js) 仓库 |

## 4. 独立进程组件

以下组件以独立进程方式随包分发，不链接进 LiMo 主程序（无许可证传染），但仍需满足再分发义务：

| 组件 | 许可证 | 位置与说明 |
|------|--------|-----------|
| **ffmpeg / ffprobe** | **GPLv3** | `ffmpeg/` 目录。gyan.dev essentials 构建，官方声明「All builds are 64-bit, static and licensed as GPLv3」。全文见 `ffmpeg/GPL-3.0.txt` + `ffmpeg/README-ffmpeg.txt`。源码：<https://ffmpeg.org> |
| SearXNG 便携版 | AGPL-3.0 | **当前未随包**（如启用请附全文）。进程外启动，不并入核心 |

## 5. 合规与尊重承诺

1. 不剥离任何版权声明，各许可证文本随发行包附带：本文件（随包于 `_internal/licenses/THIRD-PARTY-NOTICES.md`）
   + 汇总全文 `_internal/licenses/` + 静态资源 `_internal/static/licenses/` + 各包自带 `_internal/*.dist-info/licenses/`。
   安装目录内的读者可先看同目录 `README.md`，它说明了本目录内容与其他许可证位置。
2. AGPL/GPL 类组件：ffmpeg 以独立进程隔离（GPL-3.0 全文随包）；ebooklib（AGPL-3.0）与 html2text（GPL-3.0）已于 2026-08-26 彻底移除（ebooklib → 自研 epub-container 纯标准库；html2text → bs4）；自研抖音下载路径（含头部声明 GPLv3 的 `abogus` 签名模块，源自 TikTokDownloader）已于 2026-10-06 移除，抖音改走 yt-dlp 通用路径，**主 exe 已零 AGPL/GPL 库**，不再随包附带 AGPL/GPL 全文。
3. 完整许可证文本以各项目官方仓库为准；本文件与 [ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) 如有出入，以官方为准。
