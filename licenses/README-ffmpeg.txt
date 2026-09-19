# ffmpeg / ffprobe 许可证说明

内置的 `ffmpeg.exe` / `ffprobe.exe` 来自 gyan.dev essentials 构建（8.1.1 版）。

## 许可证：GPL-3.0

gyan.dev 官方页面声明：「All builds are 64-bit, static and licensed as **GPLv3**」。
这些构建包含 libx264 / libx265 / libxvid 等 GPL 库，因此整体为 **GPLv3**（非 LGPL）。

## 使用方式与传染边界

ffmpeg/ffprobe 以独立进程（subprocess）方式被 LiMo 调用（视频转码、抽帧、拼接等），
**不链接进 LiMo 主程序**，不构成「合并作品」，因此不触发 GPL 传染义务。
随包附带本说明 + `GPL-3.0.txt` 全文以满足再分发义务。

## 源码获取

- gyan.dev 构建页：https://www.gyan.dev/ffmpeg/builds/
- ffmpeg 官方源码：https://ffmpeg.org/
