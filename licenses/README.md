# 第三方许可证全文

本目录存放随发行包附带的第三方许可证全文与总清单，由 PyInstaller `datas` 收集到安装目录的
`_internal/licenses/`，满足各开源许可证的再分发义务。各包自带的 `dist-info/licenses/`
（`collect_all` 收集）仍保留原样，不在本目录内。

## 先看总清单

同目录下的 **[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)** 是发行版内嵌的**全部**第三方
组件清单 —— 组件名 / 许可证 / 全文位置，分「后端 Python 组件 / 前端组件 / 离线静态资源 /
独立进程组件」四类。查「某个组件是什么许可证、全文在哪」，先翻它；本文件只讲**这个目录**。

## 本目录内的文件

| 文件 | 对应组件 | 许可证 | 来源 |
|------|---------|--------|------|
| `THIRD-PARTY-NOTICES.md` | —（总清单，非许可证全文） | — | 仓库根目录同文件 |
| `GPL-3.0.txt` | ffmpeg/ffprobe（gyan.dev essentials 构建） | GPL-3.0 | html2text wheel 副本（GPL-3.0 标准文本） |
| `Apache-2.0.txt` | magika | Apache-2.0 | cryptography wheel 副本（标准文本） |
| `MIT-markitdown.txt` | markitdown | MIT | [microsoft/markitdown](https://github.com/microsoft/markitdown) 官方仓库 |
| `README-ffmpeg.txt` | ffmpeg 构建来源与许可说明 | — | 自制（与 `ffmpeg/` 内同名文件一致） |

## 安装目录中其他许可证位置

以下路径以**安装目录**（含 `LiMo.exe` 的那一层）为基准，本目录即 `_internal/licenses/`：

| 位置 | 内容 |
|------|------|
| `_internal/static/licenses/` = 本目录的 `../static/licenses/` | KaTeX（MIT）+ markmap-lib/view（MIT）+ d3（ISC） |
| `_internal/*.dist-info/licenses/` = `../*.dist-info/licenses/` | 各 Python 包自带的许可证（PyInstaller `collect_all` 自动收集） |
| `ffmpeg/` = `../../ffmpeg/` | ffmpeg/ffprobe 二进制旁紧邻放置的 GPL-3.0 全文 + 构建说明 |

> 注 1：html2text（GPL-3.0）已于 2026-08-26 从主代码移除（webpage.py 改用 bs4）；`GPL-3.0.txt` 现仅对应 ffmpeg。
> 注 2：ebooklib（AGPL-3.0）已于 2026-08-26 彻底移除（自研 epub-container 纯标准库替代），`AGPL-3.0.txt` 已删除。
> **主 exe 已零 AGPL/GPL 库**（GPL 仅剩独立进程的 ffmpeg）。

> ⚠️ **ffmpeg 许可证说明（2026-08-26 核实）**：内置 ffmpeg 为 gyan.dev essentials 构建，gyan.dev 官方页面明确
> 「All builds are 64-bit, static and licensed as **GPLv3**」——含 libx264/libx265/libxvid 等 GPL 库，
> **不是 LGPL**。ffmpeg 以独立进程（subprocess）方式调用，不链接进 LiMo 主程序，不触发 GPL 传染；
> 随包附带 GPL-3.0 全文 + 源码获取途径即可合规。源码见 gyan.dev 或 <https://ffmpeg.org>。
