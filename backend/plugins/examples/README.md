# LiMo 引擎插件示例

把任意 `.py` 文件放入 `~/.limo/plugins/` 目录，LiMo 启动时自动加载。

## 可用插件

### pymupdf4llm_engine.py — ⭐ 推荐，效果最好

**许可证**: AGPL-3.0 ⚠️ （商用需获取授权）

**安装**:
```bash
pip install pymupdf4llm pymupdf
cp pymupdf4llm_engine.py ~/.limo/plugins/
```

**功能**: 一行 `to_markdown()` 自动处理文字 + 表格 + 嵌入图 + 矢量图，API 最简洁。

### pymupdf_engine.py — 轻量版

**许可证**: AGPL-3.0 ⚠️

**安装**:
```bash
pip install pymupdf
cp pymupdf_engine.py ~/.limo/plugins/
```

**功能**: 手写 PyMuPDF 文字 + 表格 + 图片，不依赖 pymupdf4llm 额外包。

---

**注意**: 两个插件均为 AGPL v3 许可证。商用需购买授权或选择 LiMo 内置的 MIT/BSD 引擎（pypdfium2、docling）。

---

## 许可证声明

所有示例插件包含 `license` 字段，LiMo 会在设置页显示对应的许可证标签：
- **黄色** = AGPL/GPL 风险
- **灰色** = MIT/BSD 安全
- **绿色** = Apache 2.0
