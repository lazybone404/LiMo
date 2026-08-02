# Tesseract 本地 OCR 插件（参考实现，非官方插件）

进程外插件：`command` 指向子目录独立 venv，`engine.py` 通过 subprocess 调用插件自带的 `tesseract.exe`（便携版）。

## 安装

```bash
# 1. 拷贝声明文件 + 子目录到 ~/.limo/plugins/
cp -r tesseract_plugin.py tesseract ~/.limo/plugins/

# 2. 自备 tesseract 便携版
#    从 https://github.com/UB-Mannheim/tesseract/releases 下载安装器，
#    安装后把 tesseract.exe 和 tessdata/ 拷贝到 ~/.limo/plugins/tesseract/tesseract/
#    （目录结构：tesseract/tesseract/tesseract.exe + tesseract/tesseract/tessdata/）

# 3. 建 venv（engine.py 无第三方依赖，空 venv 即可）
cd ~/.limo/plugins/tesseract
python -m venv .venv

# 4. 重启后端
```

## 配置（config_schema，前端插件页表单）

| 字段 | 说明 |
|------|------|
| `lang` | 识别语言，默认 `chi_sim+eng` |
| `psm` | 页面分割模式，`3`=自动分页 / `6`=统一文本块 |

## 常见问题

- **中文乱码 / `Failed loading language 'chi_sim'`**：`tessdata/` 缺 `chi_sim.traineddata`。下载 chi_sim 语言包放入
  `~/.limo/plugins/tesseract/tesseract/tessdata/`（约 43MB，从 Tesseract 语言包仓库获取）。
- **Command not found**：venv 路径与 `command` 声明不一致（Windows `Scripts/python.exe`，Linux `bin/python3`）。
- **识别为空**：图片本身无文字，或 psm 模式不匹配（表格图试 `6`）。

## 取消语义

tesseract 是单次 subprocess 调用，取消只能在运行前检查 `cancel_file` 一次（与 pymupdf4llm 模板一致），`timeout` 兜底。
