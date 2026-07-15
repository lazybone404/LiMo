"""验证 Prompt 构建 — 视频/文档/Excel + 格式选项 + 参考文献约束 + 风格隔离 + 语言策略"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.generation.prompt import (
    build_notes_prompt, build_polish_prompt,
    build_excel_summary_prompt,
    get_references_format,
    normalize_target_language,
)
from app.generation.prompts.notes_styles import normalize_style

# ── 视频笔记 ──
p = build_notes_prompt('Hello world transcript', 'Test Video', 'detailed', 'video',
                       formats=['toc', 'summary'])
assert 'Test Video' in p
assert '专业的笔记助手' in p
assert '目录' in p or 'TOC' in p.upper()
assert 'AI 总结' in p
assert '输出格式：第一行必须是笔记标题' in p
print('PASS: video notes prompt')

# ── PDF 文档笔记 ──
p = build_notes_prompt('PDF content here', 'Test PDF', 'imrad', 'pdf',
                       formats=['references', 'toc'])
assert 'Test PDF' in p
assert '文档转录内容' in p
assert 'BibTeX' in p
assert '严禁' in p
print('PASS: PDF notes prompt with references')

# ── 整理版原文 ──
p = build_polish_prompt('Raw transcript with 嗯 fillers', 'Test Polish', 'detailed', 'video')
assert '整理版原文' in p or '文本编辑' in p
assert '输出格式：第一行必须是文档标题' in p
print('PASS: polish prompt')

# ── Excel 摘要 ──
p = build_excel_summary_prompt('| X | Y |\n|---|---|\n| 3 | 4 |', 'minimal')
assert '数据摘要' in p
assert 'Sheet1' in p
assert 'X' in p and 'Y' in p
print('PASS: Excel summary prompt')

# ── 参考文献格式 ──
ref = get_references_format()
assert 'BibTeX' in ref
assert '```bibtex' in ref
assert '严禁' in ref
print('PASS: references format constraint')

# ── EPUB prompt（文档 prompt 路径）──
p = build_notes_prompt('{"chapters":[{"title":"Ch1","sections":[{"text":"Hello"}]}]}',
                       'Test EPUB', 'detailed', 'epub')
assert 'Test EPUB' in p
print('PASS: EPUB prompt (doc path)')

# ═══════════════════════════════════════════════════════════════
# Commit 4: 风格隔离 + 模块化结构 防回归测试
# ═══════════════════════════════════════════════════════════════

# ── Polish 防回归 ──
p = build_polish_prompt('测试', '测试', style='none', source_type='video')
assert '<style_preference>' not in p, 'Polish 不应有 style_preference 节'
assert '保持原文' in p, 'Polish 应包含保持原文约束'
print('PASS: polish has no style section')

p = build_polish_prompt('测试', '测试', style='business', source_type='video')
assert '结论先行' not in p, 'Polish+business 不应注入商业风格指令'
print('PASS: polish ignores business style')

p = build_polish_prompt('测试', formats=['toc'])
assert '目录' in p, 'Polish 应允许 toc 格式'
print('PASS: polish allows toc')

p = build_polish_prompt('测试', formats=['summary'])
assert 'AI 总结' not in p, 'Polish 应禁止 summary 格式'
print('PASS: polish blocks summary')

p = build_polish_prompt('测试', formats=['link'])
assert 'Content-[mm:ss]' in p, 'Polish 应允许 link 格式（截图+跳转对整理版同样适用）'
print('PASS: polish allows link')

# ── 文档 Polish 独有硬约束 ──
p = build_polish_prompt('测试', source_type='pdf')
assert '保留图片引用' in p, '文档 Polish 应保留图片引用约束'
assert '修正表格错误' in p, '文档 Polish 应保留表格修正约束'
assert '修正 OCR 错误' in p, '文档 Polish 应保留 OCR 修正约束'
print('PASS: doc polish preserves all 5 constraints')

# ── Notes 风格隔离 ──
p = build_notes_prompt('测试', style='business')
assert '<style_preference>' in p, 'Notes 应有 style_preference 节'
assert '结论先行' in p, 'Notes+business 应有商业风格文本'
print('PASS: notes has business style')

# ── meeting key alias ──
p = build_notes_prompt('测试', style='meeting_minutes')
assert '会议纪要' in p or '议题' in p, 'meeting_minutes alias 应生效'
print('PASS: meeting_minutes alias works')

# ── normalize_style ──
assert normalize_style('meeting_minutes') == 'meeting'
assert normalize_style('business') == 'business'
print('PASS: normalize_style')

# ── 格式语义防回归 ──
p = build_notes_prompt('网页内容', source_type='webpage')
assert '视频转录' not in p, '网页 prompt 不应包含"视频转录"'
print('PASS: webpage prompt has no video text')

# ── 模块化结构完整性 ──
p = build_notes_prompt('测试', formats=['toc'])
for tag in ['<role>', '<task>', '<source_content>',
            '<instruction_priority>', '<output_language>',
            '<hard_constraints>', '<output_format>']:
    assert tag in p, f'Notes prompt 缺少 {tag} 节'
print('PASS: notes has all required sections')

p = build_polish_prompt('测试')
for tag in ['<role>', '<task>', '<source_content>',
            '<instruction_priority>', '<output_language>',
            '<output_format>']:
    assert tag in p, f'Polish prompt 缺少 {tag} 节'
print('PASS: polish has all required sections')

# ── XML 结构一致性 ──
assert p.count('<role>') == 1 and p.count('</role>') == 1
assert p.count('<style_preference>') == 0  # Polish 无 style
assert p.count('<output_language>') == 1 and p.count('</output_language>') == 1
print('PASS: XML tags balanced')

# ═══════════════════════════════════════════════════════════════
# P0-2: 统一语言策略 防回归测试
# ═══════════════════════════════════════════════════════════════

# ── normalize_target_language ──
assert normalize_target_language('zh-CN') == 'zh-CN'
assert normalize_target_language('zh') == 'zh-CN'
assert normalize_target_language('zh_cn') == 'zh-CN'
assert normalize_target_language('en-US') == 'en'
assert normalize_target_language('en') == 'en'
assert normalize_target_language('unknown') == 'unknown'
print('PASS: normalize_target_language')

# ── output_language 节存在 ──
p = build_notes_prompt('测试', target_language='zh-CN')
assert '<output_language>' in p, 'Notes prompt 应有 output_language 节'
assert '简体中文' in p, 'zh-CN 应对应简体中文'
print('PASS: notes has output_language (zh-CN)')

p = build_notes_prompt('测试', target_language='en')
assert 'English' in p, 'en 应对应 English'
assert '简体中文' not in p, 'en 不应出现简体中文'
print('PASS: notes has output_language (en)')

p = build_polish_prompt('测试', target_language='zh-CN')
assert '<output_language>' in p, 'Polish prompt 应有 output_language 节'
assert '简体中文' in p
print('PASS: polish has output_language (zh-CN)')

p = build_excel_summary_prompt('| A |\n|---|\n| 1 |', target_language='zh-CN')
assert '<output_language>' in p, 'Excel prompt 应有 output_language 节'
assert '字段名' in p and '保留原文' in p, 'Excel 应保留字段名原文'
print('PASS: excel has output_language with field-name rule')

# ── output_language 位置（在 instruction_priority 之后, hard_constraints 之前）──
p = build_notes_prompt('测试', formats=['toc'])
ipos = p.find('<instruction_priority>')
opos = p.find('<output_language>')
hpos = p.find('<hard_constraints>')
assert ipos < opos < hpos, (
    f'output_language 应在 instruction_priority 和 hard_constraints 之间: '
    f'inst={ipos}, out={opos}, hard={hpos}')
print('PASS: output_language positioned correctly')

# ── 防旧语言污染：4 个 role 不含硬编码语言 ──
from app.generation.prompt import _POLISH_ROLE, _NOTES_ROLE, _DOC_POLISH_ROLE, _DOC_NOTES_ROLE
ROLES = [_POLISH_ROLE, _NOTES_ROLE, _DOC_POLISH_ROLE, _DOC_NOTES_ROLE]
for role in ROLES:
    assert '必须使用中文' not in role, f'role 不应含硬编码语言: {role[:40]}...'
    assert '与原文保持一致' not in role, f'role 不应含硬编码语言: {role[:40]}...'
    assert '语言要求' not in role, f'role 不应含语言要求: {role[:40]}...'
print('PASS: all roles free of hardcoded language')

# ── _INSTRUCTION_PRIORITY 含 output_language 优先级 ──
p = build_notes_prompt('测试')
assert 'output_language' in p, 'INSTRUCTION_PRIORITY 应提及 output_language'
assert '不能改变输出语言' in p, 'INSTRUCTION_PRIORITY 应声明语言不可被覆盖'
print('PASS: instruction_priority includes output_language')

# ── 术语策略关键词 ──
p = build_notes_prompt('测试', target_language='zh-CN')
assert '行业通用写法' in p, '术语策略应要求保留行业通用写法'
assert '不强制翻译' in p, '术语策略应声明不强制翻译'
print('PASS: terminology strategy present')

# ── translate 已移除 ──
p = build_notes_prompt('测试', source_type='pdf', formats=['toc', 'summary'])
assert '翻译为' not in p, 'translate 选项应已移除'
assert '全文内容翻译' not in p, 'translate 内容不应出现'
print('PASS: translate format removed')

print('\nAll prompt tests PASS')
