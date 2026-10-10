"""产物语义标记 `limo-block` 的 **prompt 侧门控** —— 谁该有、谁不该有。

⚠️ 这份规范**没有勾选项、无条件注入 notes**（理由：标记的价值全在「模型自主判断
重点」，要求用户先勾选就退回了「用户自己知道哪里有重点」的旧问题）。于是「谁该有、
谁不该有」不再由 UI 表达，**只能靠断言钉住** —— 这就是本文件存在的理由。

要防的失败（全是静默的）：

  A. 顺手把它塞进 `_ALL_FORMAT_FNS` / `AVAILABLE_OPTIONS` → 变成「用户勾了才有」，
     功能等于没做（而且界面上看不出少了什么）。
  B. 漏进 composite —— 合成产物有自己的骨架（读者问题驱动五节 + 各篇速查），
     多一条「写出重点块」的指令会与那个骨架正面打架。
  C. 漏进 polished —— 「忠实整理」被塞进模型主观的「这是重点」判断，
     而整理版的契约正是「不改写、不评价」。
  D. 漏进 Excel 数据摘要 —— 结构化摘要里出现语义标记毫无意义。
  E. 规范里顺手教了外观（颜色 / 边框 / 圆角）→ 与 `blocks.css` 成了两份真相源，
     且与图表协议「只教语义、坐标由后端算」的纪律背道而驰（模型管「这是什么性质」，
     渲染管「长什么样」）。

文件后半是**对话侧**（`agent/prompts/formats.md`，卡片组 / 追问）的对应门控 ——
两侧的规范文本与类型集合都**刻意不共用**（`artifact_blocks.py` 的
`ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` 不相交），故门控也各测各的。
"""
from __future__ import annotations

from app.agent.prompts import PromptLoader
from app.generation.prompt import (
    AVAILABLE_OPTIONS, _ALL_FORMAT_FNS, build_excel_summary_prompt,
    build_notes_prompt, build_polish_prompt, get_markdown_block_format,
)


def test_notes_各来源类型都注入标记规范():
    """`hard_constraints` 节的内容按 `AVAILABLE_OPTIONS` 拼 —— 标记规范是**独立追加**的，
    所以任何一条 notes 路径都该有它。逐类型跑一遍（分支多，漏一条不会报错）。"""
    for st in ('video', 'audio', 'webpage', 'text', 'pdf', 'docx', 'pptx', 'image', 'epub'):
        p = build_notes_prompt('正文内容', '标题', 'plain', st)
        assert 'limo-block:v1' in p, f"notes/{st} 缺少标记规范"


def test_无格式勾选时也注入():
    """对照：`formats=[]` 时 `hard_constraints` 本该为空 —— 标记规范**不受其影响**，
    否则「用户没勾任何格式」的那批产物永远拿不到重点块（而这恰是默认情形）。"""
    a = build_notes_prompt('正文', '标题', 'plain', 'text')
    b = build_notes_prompt('正文', '标题', 'plain', 'text', formats=['toc'])
    assert 'limo-block:v1' in a
    assert 'limo-block:v1' in b
    assert a != b      # 加了格式选项确实改变了 prompt（防止上面两条恒真）


def test_整理版不注入():
    """`_POLISH_FORMAT_FNS` 里没有它 —— polished 是忠实整理，不该有主观重点判断。"""
    assert 'limo-block' not in build_polish_prompt('正文', '标题', 'plain', 'video')


def test_合成不注入():
    """对照 B：composite 在 `build_notes_prompt` 顶部就被拦下走独立骨架了。
    标记规范若加在那拦截**之前**，合成产物会同时收到两套互相矛盾的写法要求。"""
    assert 'limo-block' not in build_notes_prompt('## 来源文档\n材料正文', '标题', 'plain', 'composite')


def test_Excel_数据摘要不注入():
    assert 'limo-block' not in build_excel_summary_prompt('| X |\n|---|\n| 1 |', 'plain')


def test_没有勾选项():
    """对照 A：一旦进了这两张表，它就变成「用户勾选才有」——而前端根本没有那个勾选框，
    于是功能事实上消失了，且零报错。"""
    assert 'block' not in _ALL_FORMAT_FNS
    for source_type, opts in AVAILABLE_OPTIONS.items():
        assert 'block' not in opts, source_type


def test_规范不教外观():
    """对照 E：模型管**性质**，CSS 管**长相**。规范里出现外观词 = 两处都在描述同一件事，
    改一处就漂移（且模型描述的外观与 CSS 一旦不一致，产物上没人能说清哪个是错的）。"""
    spec = get_markdown_block_format()
    for word in ('颜色', '色值', '边框', '圆角', '投影', '阴影', '背景色', 'background'):
        assert word not in spec, f"标记规范里不该出现外观描述：{word}"


def test_规范写了逃生口与数量上限():
    """两条会被模型「按字面执行」的措辞，掉了的后果各不同：

    · **上限**（至多 1–3 处）没了 → 模型会到处标，「重点」失去区分力；
    · **逃生口**（不加不减分）没了 → 模型为「完成任务」硬凑，无标记的文章从此绝迹。
    """
    spec = get_markdown_block_format()
    assert '1–3' in spec or '1-3' in spec
    assert '不加不减分' in spec


# ═══════════════════════════════════════════════════════════════
# 对话侧（`agent/prompts/formats.md`）—— 卡片组 / 追问
# ═══════════════════════════════════════════════════════════════

def test_对话侧规范写了就要拼进_build():
    """⚠️ 只读不拼是最静默的一种：文件在、内容对、`__init__` 里也赋值了，
    唯独 `build()` 的 parts 里没有它 → 模型永远不知道有这套协议，**零报错**。"""
    p = PromptLoader().build()
    assert 'limo-block:v1' in p
    assert 'type="card-group"' in p
    assert 'type="follow-up"' in p


def test_对话侧规范排在文档上下文之前():
    """`formats` 讲的是「回答怎么呈现」，属于角色/约束那一层；排在
    `--- 当前文档 ---` 之后读起来像「当前文档的一部分」。"""
    p = PromptLoader().build(doc_context='文档内容占位')
    assert p.index('limo-block:v1') < p.index('--- 当前文档 ---')


def test_两侧规范不串味():
    """产物规范只教 `key-takeaway`、对话规范只教两个对话类型 —— 各自教各自的，
    模型在哪条管道里就只看到那条管道的写法。

    串味的后果不是报错，而是**模型学到另一侧的约定写进来**：产物里冒出一个
    对话卡片组（导出侧不认识它，只会当普通内容渲染），或者对话里冒出一个强调块。
    """
    assert 'key-takeaway' not in PromptLoader().formats
    product_spec = get_markdown_block_format()
    assert 'card-group' not in product_spec
    assert 'follow-up' not in product_spec


def test_对话侧规范不教外观():
    """同 E 条：模型管**性质**，CSS（`chat-blocks.css`）管长相。"""
    spec = PromptLoader().formats
    for word in ('颜色', '色值', '边框', '圆角', '投影', '阴影', '背景色', 'background'):
        assert word not in spec, f"对话侧规范里不该出现外观描述：{word}"


def test_对话侧规范写了逃生口与数量上限():
    """两条会被模型「按字面执行」的措辞，与产物侧同源：

    · **上限**（2～4 个）没了 → 模型把一段连贯叙述硬拆成十几张卡，读者看到的是噪音；
    · **逃生口**（不用不减分）没了 → 模型为「用上新功能」硬凑，而「一个块都不用」
      是**最常见的正常情况**。
    """
    spec = PromptLoader().formats
    assert '2～4' in spec or '2-4' in spec
    assert '不用不减分' in spec


def test_对话侧规范挡了过渡语滥用():
    """模型在工具调用前后的客套话（「我先帮你查一下」）**不是最终回答** ——
    在那里加卡片组，用户会看到一段还没说完的话被切成几张卡。"""
    spec = PromptLoader().formats
    assert '过渡语' in spec
    assert '最终回答' in spec
