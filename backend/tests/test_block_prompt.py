"""产物语义标记 `limo-block` 的 **prompt 侧门控** —— 谁该有、谁不该有。

⚠️ 这份规范**没有勾选项、无条件注入 notes**（理由：标记的价值全在「模型自主判断
重点」，要求用户先勾选就退回了「用户自己知道哪里有重点」的旧问题）。于是「谁该有、
谁不该有」不再由 UI 表达，**只能靠断言钉住** —— 这就是本文件存在的理由。

要防的失败（全是静默的）：

  A. 顺手把它塞进 `_ALL_FORMAT_FNS` / `AVAILABLE_OPTIONS` → 变成「用户勾了才有」，
     功能等于没做（而且界面上看不出少了什么）。
  B. 漏进 composite —— 合成产物有自己的骨架（五形态之一 + 各篇速查），
     多一条「写出重点块」的指令会与那个骨架正面打架。⚠️ 2026-10-18 起合成
     **有自己的** `evidence` 块（`SYNTHESIS_BLOCK_TYPES`，与产物侧集合不相交），
     故这条判据是「产物侧十二种**不**出现」，而不是「合成里不许有 limo-block」。
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
from app.core.artifact_blocks import (
    ARTIFACT_BLOCK_TYPES,
    CHAT_BLOCK_TYPES,
    SECTION_CARD_TYPE,
)
from app.generation.prompt import (
    AVAILABLE_OPTIONS, _ALL_FORMAT_FNS, build_excel_summary_prompt,
    build_notes_prompt, build_polish_prompt, get_markdown_block_format,
    get_summary_format,
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
    产物侧标记规范若加在那拦截**之前**，合成产物会同时收到两套互相矛盾的写法要求。

    ⚠️ 判据是「产物侧十二种**类型**一个都不出现」，**不是**「不含 `limo-block`」
    （2026-10-18 起合成有自己的 `evidence` 块，裸串判定会把正确的证据规范
    报成越界）。两个判据的区别正是本文件要守的边界：**协议共享、类型不共享**。
    """
    p = build_notes_prompt('## 来源文档\n材料正文', '标题', 'plain', 'composite')
    for t in ARTIFACT_BLOCK_TYPES:
        assert f'type="{t}"' not in p, f'合成产物收到了产物侧的 {t} 块规范'
    # 整段规范也不该被拼进来（`type=` 判定抓的是示例；这份抓的是条款正文）
    assert get_markdown_block_format() not in p


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


def test_规范写了触发判据与数量参照():
    """钉住防滥用**真正依赖**的两样东西（2026-10-11 取向调整后）：

    · **触发判据**（「读者跳过就会损失关键信息」）—— 这是模型判断该不该标的**唯一**
      依据。防滥用靠判据写清楚，不靠反复叮嘱「不用也行」；
    · **数量参照**（一般 1–3 处）—— 强调块的视觉强度决定了它一多就不再是强调。

    ⚠️ 原判据还钉着一句逃生口（「不加不减分」）。2026-10-11 用户明确「卡片是附加在
    MD 上的一种显示语法，不用过于克制」，那类反复劝退的措辞已从两侧规范中删除 ——
    本判据随之改为「判据 + 参照」，而**不是**把整条守卫删掉。
    """
    spec = get_markdown_block_format()
    assert '读者跳过就会损失关键信息' in spec
    assert '1–3' in spec or '1-3' in spec


def test_产物规范列出了白名单里的每一个类型():
    """⚠️ **集合驱动**：加一个产物类型却忘了写进规范 = 白名单认得它、渲染器画得出来、
    `blocks.css` 有规则，唯独**模型永远不知道有这个 type** → 零报错、零使用。

    判据从 `ARTIFACT_BLOCK_TYPES` 派生而不是手写四个名字（模式 #54：手写名单是同一件
    事实的第二份副本，下次加 type 时它会绿着失去意义）。
    """
    spec = get_markdown_block_format()
    missing = sorted(t for t in ARTIFACT_BLOCK_TYPES if t not in spec)
    assert not missing, f"产物规范里没有这些类型：{missing}"


def test_规范教了新块的结构约定():
    """`sequence` / `quadrant`（2026-10-20）的**形态差异全在 CSS**，而 CSS 靠块内
    列表类型与项数认它们 —— 故「怎么写」必须在规范里点名，否则模型写错形态时
    渲染侧只能退化（零残留、但白标了一块，用户看到的是一个普通列表）。

    · `sequence`：**有序列表** + 每项 `**发送方 → 接收方**：内容`（方向由文字承担，
      这是它不用结构分派的唯一原因）；
    · `quadrant`：**恰好 4 项** + 顺序固定「左上 / 右上 / 左下 / 右下」（与
      `chart_layout` 的 `quadrant` 算子同序，CSS 才能直接排 2×2）。
    """
    spec = get_markdown_block_format()
    assert '有序列表' in spec, '这几族块都靠 `<ol>` 认形态，必须写明'
    assert '发送方 → 接收方' in spec, 'sequence 的方向写法必须点名'
    assert '恰好 4 项' in spec, 'quadrant 的项数是它的形状前提'
    assert '左上、右上、左下、右下' in spec, 'quadrant 的项序必须与算子同序'


def test_规范教了节级修饰符_card():
    """⚠️ 这一条**不在**上面那条的派生抽检范围内 —— `card` 刻意不进
    `ARTIFACT_BLOCK_TYPES`（它是节修饰符不是块，进了会被当块开、把标题吞掉）。
    也就是说，它是**唯一**没有集合守卫盯着的类型，只能手写。

    手写的失效方式正是本项目最怕的那种静默：**模型永远不知道有 `card` 这个 type**
    ⇒ 骨架轴开着也一张卡都不会出现，零报错、零警告、看起来像「模型觉得这篇没重点」。

    故除了「名字在场」，还要断**写法差异**那两句：「写在标题之下」与「没有闭合标记」
    —— 模型按包裹式写（其余十一种都是那样）会退化成「不成卡，但结构与内容一字不少」
    （有宽容兜底，不是灾难），但那等于这个功能白做。
    """
    spec = get_markdown_block_format()
    assert SECTION_CARD_TYPE in spec
    assert '下面一行' in spec          # 位置：标题之下
    assert '没有闭合标记' in spec      # 形态：不是包裹式
    assert '1–3' in spec or '1-3' in spec


def test_格式选项自己说出它是什么块():
    """`summary` 是**唯一**能直接对上 `card` 节修饰符的格式选项（2026-10-18）。

    要防的失败：`get_summary_format()` 退回成「末尾加一个 ## AI 总结 段落」——
    那样产物里 AI 总结与普通章节**长得一模一样**，读者得靠标题文字猜这一节的性质，
    勾选项对产物的**形态**零影响。

    ⚠️ 三条都要断：名字（`type="card"`）、位置（写在标题之下）、形态（没有闭合标记）
    —— 只断名字的话，模型按包裹式写会退化成「不成卡，但内容一字不少」（有宽容兜底，
    不是灾难，但等于这个功能白做）。与 `test_规范教了节级修饰符_card` 同一个理由。
    """
    s = get_summary_format()
    assert 'type="card"' in s
    assert '下面一行' in s
    assert '没有闭合标记' in s


def test_格式选项的卡片指示不泄漏进整理版():
    """`summary` 不在 `_POLISH_FORMAT_FNS` 里 —— 整理版是「忠实整理」，不该凭空
    多出一节 AI 总结（更不该多出一张卡）。这条同时钉住「summary 的 card 指示
    不会因为函数被两侧共用而漏到 polished」。

    ⚠️ 传 `formats=['summary']` 是**故意**的：调用方确实可能这样传（前端在
    「只生成整理版」模式下仍会带上已勾选项），由 `_POLISH_FORMAT_FNS` 静默过滤。
    """
    p = build_polish_prompt('正文内容', '标题', 'plain', 'video', formats=['summary'])
    assert 'AI 总结' not in p
    assert 'type="card"' not in p


def test_自己生成的图表不要再包_image_块():
    """`image` 块与 `chart` 选项会同时在场（标记规范无条件注入、图表勾选后注入）。

    失效形态：模型把 ```fig 围栏包进 `image` 块里 → 后处理把围栏换成 PNG 之后，
    得到「图片块外壳 + 图表」，而块内的说明文字又是模型自己编的（不是材料原文）。
    规范里那句反向指路是这个问题唯一的防线。
    """
    spec = get_markdown_block_format()
    assert '已经自成一块' in spec


def test_取向_两侧规范都不再有反复劝退的措辞():
    """⚠️ **反向断言**（`not in`），与文件里其它几条方向相反 —— 它防的是「哪天有人
    觉得模型用得太多，又把劝退话术加回来」。

    2026-10-11 的产品取向：卡片是**常规呈现手段**（用户原话「附加在 MD 上的一种显示
    语法，不用过于克制」）。真有「用得太多」的问题时，正确的修法是**收紧触发判据**，
    而不是重新堆叮嘱 —— 叮嘱压的是使用率，判据调的是使用质量。
    """
    for spec in (get_markdown_block_format(), PromptLoader().formats):
        assert '不减分' not in spec
        assert '不要为了用' not in spec
        assert '一处都不用' not in spec


# ═══════════════════════════════════════════════════════════════
# 对话侧（`agent/prompts/formats.md`）—— 卡片组 / 追问
# ═══════════════════════════════════════════════════════════════

def test_对话侧规范写了就要拼进_build():
    """⚠️ 只读不拼是最静默的一种：文件在、内容对、`__init__` 里也赋值了，
    唯独 `build()` 的 parts 里没有它 → 模型永远不知道有这套协议，**零报错**。"""
    p = PromptLoader().build()
    assert 'limo-block:v1' in p
    for t in CHAT_BLOCK_TYPES:
        assert f'type="{t}"' in p, f"对话 prompt 里没有 {t} 的示例"


def test_对话侧规范列出了白名单里的每一个类型():
    """同产物侧那条：集合驱动，加 type 忘了写规范时自动红。"""
    spec = PromptLoader().formats
    missing = sorted(t for t in CHAT_BLOCK_TYPES if t not in spec)
    assert not missing, f"对话规范里没有这些类型：{missing}"


def test_对话侧有总量闸门():
    """四种块同时可用时滥用是**实数**风险（`card-group` 一个就够、`info` 又长得像
    普通段落）。逐条判据管的是「这一块该不该用」，总量闸门管的是「一次回答用几块」——
    两者挡的不是同一件事，缺了后者只剩前者时，模型可以每一块都「判据成立」。"""
    assert '不超过两块' in PromptLoader().formats


def test_对话侧规范排在文档上下文之前():
    """`formats` 讲的是「回答怎么呈现」，属于角色/约束那一层；排在
    `--- 当前文档 ---` 之后读起来像「当前文档的一部分」。"""
    p = PromptLoader().build(doc_context='文档内容占位')
    assert p.index('limo-block:v1') < p.index('--- 当前文档 ---')


def test_两侧规范不串味():
    """产物规范只教产物类型、对话规范只教对话类型 —— 各自教各自的，
    模型在哪条管道里就只看到那条管道的写法。

    串味的后果不是报错，而是**模型学到另一侧的约定写进来**：产物里冒出一个
    对话卡片组（导出侧不认识它，只会当普通内容渲染），或者对话里冒出一个强调块。

    ⚠️ 判据由两侧的**集合常量**派生，不手写名单 —— 加 type 时手写名单会静默过期
    （与 `test_产物规范列出了白名单里的每一个类型` 同一条理由）。

    ⚠️ **反串味那条用 `type="xxx"` 而不是裸 type 名**（2026-10-11 收紧）：产物侧新增
    的 `table` / `code` / `image` 都是英文常用词 —— 用裸子串判「对话规范里有没有产物
    类型」，会因为 `formats.md` 里随手写一句英文（如 "code block"）而**假红**，而那
    并不是串味。串味的真实判据是「教了另一侧的**协议形态**」，故贴着协议形态判。
    反面那条（「必须在场」）仍用裸名：产物规范里九个 type 只有 `key-takeaway` 带完整
    示例行，对它用 `type="…"` 会**假红**，而「提到了这个名字」正是它要的证据。
    """
    product_spec = get_markdown_block_format()
    chat_spec = PromptLoader().formats
    for t in sorted(CHAT_BLOCK_TYPES):
        assert f'type="{t}"' not in product_spec, f"产物规范里出现了对话侧类型：{t}"
    for t in sorted(ARTIFACT_BLOCK_TYPES):
        assert f'type="{t}"' not in chat_spec, f"对话规范里出现了产物侧类型：{t}"
    # ⚠️ 节级修饰符 `card` 是**产物侧独有**的（对话正文没有「节」这个概念，它有
    # 自己的 card-group）—— 它既不在 ARTIFACT 也不在 CHAT（刻意如此），故上面那两条
    # 循环覆盖不到它，得单独断一次。漏断的后果不是报错，是模型在对话里写出一行
    # 毫无作用的标记（对话侧解析器认得它、但没有任何东西消费它）。
    assert f'type="{SECTION_CARD_TYPE}"' not in chat_spec
    # 自证有区分力：两侧各自确实提到了自己的类型（否则上面两条可能因规范为空而恒真）
    assert all(t in product_spec for t in ARTIFACT_BLOCK_TYPES)
    assert all(t in chat_spec for t in CHAT_BLOCK_TYPES)


def test_对话侧规范不教外观():
    """同 E 条：模型管**性质**，CSS（`chat-blocks.css`）管长相。"""
    spec = PromptLoader().formats
    for word in ('颜色', '色值', '边框', '圆角', '投影', '阴影', '背景色', 'background'):
        assert word not in spec, f"对话侧规范里不该出现外观描述：{word}"


def test_对话侧规范写了触发判据与数量上限():
    """同产物侧：防滥用靠**判据在场**，不靠反复劝退（2026-10-11 取向调整）。

    · 卡片组的判据是「**同级 + 同维度**」—— 两个词都要在。只写「并列比较」不够：
      模型会把松散并列（各说各的）也拆成卡，而那正是「读者看到噪音」的来源；
    · 追问的判据是「从读者视角出发的下一步问题」；
    · 数量（2～4）保留 —— 十几张卡读者只会看到噪音。
    """
    spec = PromptLoader().formats
    assert '同级' in spec and '同维度' in spec
    assert '从读者视角出发的下一步问题' in spec
    assert '2～4' in spec or '2-4' in spec
    # 2026-10-20：判据从「稀缺式」改成**可判定**的触发条件（用户明确「带图标的
    # 卡片挺喜欢，应该在什么地方用可以提倡使用一下」）。旧判据里信息卡要求
    # 「之后可能单独回来查」、摘要卡要求「有好几层结构」—— 模型没法判定自己满足没有，
    # 保守策略就是一律不用（结果是七种块里用得最少的正是带图标的那几个）。
    # 现在钉的是**可判定性**：速查表给出「写什么 → 用哪个块」的直接映射，
    # 摘要卡给的是可数的量（三段 / 5 条），信息卡给的是一个能自问的问题。
    assert '按内容形态找块' in spec, '缺少「写什么 → 用哪个块」的速查表'
    assert '超过三段' in spec, '摘要卡的触发条件应可数，不应该是「有好几层结构」'
    assert '它是谁' in spec, '信息卡的判据应能自问，不应该是「可能单独回来查」'


def test_对话侧规范挡了过渡语滥用():
    """模型在工具调用前后的客套话（「我先帮你查一下」）**不是最终回答** ——
    在那里加卡片组，用户会看到一段还没说完的话被切成几张卡。"""
    spec = PromptLoader().formats
    assert '过渡语' in spec
    assert '最终回答' in spec
