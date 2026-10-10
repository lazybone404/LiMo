"""
PromptComposer — 模块化 Prompt 构建器

所有 LLM prompt 通过分节标签组装，而非扁平拼接。

标签语义 —— **与 `docs/prompts-inventory.md` 附录 C 同序同措辞**。这是一份表的两处
呈现（附录 C 还多一列「出现位置」），改动要同步；各自演进的结果是「读 docstring 的
人以为标签只有 8 个」（此处曾漏列 `<output_language>` / `<reference_texts>`，且把
`<role>` 写成「角色定义 + 语言要求」——语言早在 P0-2 就拆成了独立的节）：

  <role>                  纯角色定义（不含语言要求）
  <task>                  任务目标 + 核心原则
  <source_content>        待处理内容
  <instruction_priority>  规则优先级声明（四层：hard_constraints > output_language
                          > user_request > style_preference）
  <output_language>       输出语言策略（动态）
  <hard_constraints>      硬约束（格式选项）
  <style_preference>      软偏好（仅 Notes/Excel 有，Polish 无）
  <output_format>         输出格式要求
  <reference_texts>       参考资料（**仅 notes 注入**）
  <user_request>          用户备注（Polish 无此节）

⚠️ 合成管道另有一套 `_SYNTHESIS_INSTRUCTION_PRIORITY`（**三层**，无
`style_preference` 节）—— 那是独立的一套，不受上表约束。
"""


class PromptComposer:
    """分节组装 prompt，每节用 XML 标签包裹。跳过空节。"""

    @staticmethod
    def section(name: str, text: str) -> str:
        """构建一个 XML 分节。text 为空时返回空字符串。"""
        text = text.strip()
        if not text:
            return ""
        return f"<{name}>\n{text}\n</{name}>"

    @staticmethod
    def compose(sections: list[tuple[str, str]]) -> str:
        """按顺序组装分节，跳过空节，节之间用双换行分隔。"""
        parts = []
        for name, text in sections:
            s = PromptComposer.section(name, text)
            if s:
                parts.append(s)
        return "\n\n".join(parts)
