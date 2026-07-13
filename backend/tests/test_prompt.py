"""验证 Prompt 构建 — 视频/文档/Excel + 格式选项 + 参考文献约束"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.generation.prompt import (
    build_notes_prompt, build_polish_prompt,
    build_excel_summary_prompt,
    get_references_format,
)

# ── 视频笔记 ──
p = build_notes_prompt('Hello world transcript', 'Test Video', 'detailed', 'video',
                       formats=['toc', 'summary'])
assert 'Test Video' in p
assert '专业的笔记助手' in p
assert '目录' in p or 'TOC' in p.upper()  # TOC format
assert 'AI 总结' in p
assert '输出格式：第一行必须是笔记标题' in p
print('PASS: video notes prompt')

# ── PDF 文档笔记 ──
p = build_notes_prompt('PDF content here', 'Test PDF', 'imrad', 'pdf',
                       formats=['references', 'toc'])
assert 'Test PDF' in p
assert '文档转录内容' in p
assert 'BibTeX' in p  # references format
assert '严禁' in p    # must NOT use ### per reference
print('PASS: PDF notes prompt with references')

# ── 整理版原文 ──
p = build_polish_prompt('Raw transcript with 嗯 fillers', 'Test Polish', 'detailed', 'video')
assert '整理版原文' in p or '文本编辑' in p
assert '输出格式：第一行必须是文档标题' in p
print('PASS: polish prompt')

# ── Excel 摘要（分析报告已移除）──
p = build_excel_summary_prompt('| X | Y |\n|---|---|\n| 3 | 4 |', 'minimal')
assert '数据摘要' in p
assert 'Sheet1' in p       # _build_data_summary 解析出 sheet 名
assert 'X' in p and 'Y' in p  # 列名保留
print('PASS: Excel summary prompt')
print('PASS: Excel summary prompt')

# ── 参考文献格式 ──
ref = get_references_format()
assert 'BibTeX' in ref
assert '```bibtex' in ref
assert '严禁' in ref  # must not create ### per reference
print('PASS: references format constraint')

# ── EPUB prompt（文档 prompt 路径）──
p = build_notes_prompt('{"chapters":[{"title":"Ch1","sections":[{"text":"Hello"}]}]}',
                       'Test EPUB', 'detailed', 'epub')
assert 'Test EPUB' in p
print('PASS: EPUB prompt (doc path)')

print('\nAll prompt tests PASS')
