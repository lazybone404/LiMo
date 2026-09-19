# 第三方许可证全文

本目录存放随发行包附带的第三方许可证全文，由 PyInstaller `datas` 收集到 `_internal/licenses/`，
满足各开源许可证的再分发义务。各包自带的 `dist-info/licenses/`（collect_all 收集）仍保留原样。

| 文件 | 对应组件 | 许可证 | 来源 |
|------|---------|--------|------|
| `GPL-3.0.txt` | ffmpeg/ffprobe（gyan.dev essentials 构建） | GPL-3.0 | html2text wheel 副本（GPL-3.0 标准文本） |
| `Apache-2.0.txt` | magika | Apache-2.0 | cryptography wheel 副本（标准文本） |
| `MIT-markitdown.txt` | markitdown | MIT | [microsoft/markitdown](https://github.com/microsoft/markitdown) 官方仓库 |

> 注 1：html2text（GPL-3.0）已于 2026-08-26 从主代码移除（webpage.py 改用 bs4）；`GPL-3.0.txt` 现仅对应 ffmpeg。
> 注 2：ebooklib（AGPL-3.0）已于 2026-08-26 彻底移除（自研 epub-container 纯标准库替代），`AGPL-3.0.txt` 已删除。
> **主 exe 已零 AGPL/GPL 库**（GPL 仅剩独立进程的 ffmpeg）。

> ⚠️ **ffmpeg 许可证说明（2026-08-26 核实）**：内置 ffmpeg 为 gyan.dev essentials 构建，gyan.dev 官方页面明确
> 「All builds are 64-bit, static and licensed as **GPLv3**」——含 libx264/libx265/libxvid 等 GPL 库，
> **不是 LGPL**。ffmpeg 以独立进程（subprocess）方式调用，不链接进 LiMo 主程序，不触发 GPL 传染；
> 随包附带 GPL-3.0 全文 + 源码获取途径即可合规。源码见 gyan.dev 或 <https://ffmpeg.org>。
