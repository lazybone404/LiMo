# LiMo 引擎插件

把 `.py` 文件放入 `~/.limo/plugins/` 目录，LiMo 启动时自动加载。

## 两种模式

| 模式 | 适用场景 | 依赖位置 |
|------|---------|---------|
| **进程内**（默认） | 简单插件，依赖少 | 主 venv |
| **进程外**（command 字段） | 重依赖/协议敏感 | 插件自带 `.venv` |

---

## 进程内插件示例

### pymupdf4llm_engine.py — ⭐ 推荐，效果最好

**许可证**: AGPL-3.0 ⚠️

```bash
pip install pymupdf4llm pymupdf
cp pymupdf4llm_engine.py ~/.limo/plugins/
```

### pymupdf_engine.py — 轻量版

**许可证**: AGPL-3.0 ⚠️

```bash
pip install pymupdf
cp pymupdf_engine.py ~/.limo/plugins/
```

---

## 进程外插件示例（依赖隔离）

重依赖的插件推荐用独立 venv，避免污染主环境。

### 目录结构

```
~/.limo/plugins/
├── pymupdf4llm_heavy.py              # 顶层声明文件
└── pymupdf4llm_heavy/                # 子目录
    ├── engine.py                     # 真正执行的脚本
    ├── requirements.txt
    └── .venv/                        # python -m venv .venv
```

### engine.py

```python
import sys, json

def main():
    file_path = sys.argv[sys.argv.index("--input") + 1]
    config = json.load(sys.stdin)  # {"media_dir": "/tmp/..."}
    # 用自己 venv 里的库处理...
    import pymupdf4llm
    md = pymupdf4llm.to_markdown(file_path,
        write_images=True,
        image_path=config.get("media_dir", ""),
        image_format="png", dpi=150)
    print(md)

if __name__ == "__main__":
    main()
```

### 顶层声明文件（pymupdf4llm_heavy.py）

```python
from app.engines.plugin_loader import EnginePlugin

class PyMuPDF4LLMHeavy(EnginePlugin):
    name = "pymupdf4llm"
    category = "doc"
    license = "AGPL-3.0"
    description = "PyMuPDF4LLM 进程外引擎"
    file_types = [".pdf"]

    # 进程外模式：command 指向子目录的 venv 和 engine.py
    command = ["pymupdf4llm_heavy/.venv/Scripts/python.exe",
               "pymupdf4llm_heavy/engine.py"]
    # Windows: .venv/Scripts/python.exe
    # Linux/macOS: .venv/bin/python3
```

### 安装

```bash
cp pymupdf4llm_heavy.py ~/.limo/plugins/
mkdir -p ~/.limo/plugins/pymupdf4llm_heavy
cp engine.py ~/.limo/plugins/pymupdf4llm_heavy/
cd ~/.limo/plugins/pymupdf4llm_heavy
python -m venv .venv
.venv/Scripts/pip install pymupdf4llm pymupdf  # Windows
# .venv/bin/pip install pymupdf4llm pymupdf    # Linux/macOS
```

### 协议

```
LiMo 进程 ──subprocess──→ .venv/python engine.py --input file.pdf
                          stdin ← {"media_dir": "/tmp/..."}
                          stdout → Markdown 文本
```

---

## 许可证标签

| 颜色 | 含义 | 许可证 |
|------|------|--------|
| **黄色** | ⚠️ 有风险 | AGPL-3.0, GPL-3.0, GPL-2.0 |
| **灰色** | 安全 | MIT, BSD, ISC |
| **绿色** | 宽松 | Apache-2.0

**注意**: AGPL 插件商用需购买授权，或使用 LiMo 内置的 MIT/BSD 引擎（pypdfium2、docling）。
