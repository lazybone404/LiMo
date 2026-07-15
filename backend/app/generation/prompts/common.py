"""
PromptComposer — 模块化 Prompt 构建器

所有 LLM prompt 通过分节标签组装，而非扁平拼接。
标签语义：
  <role>              角色定义 + 语言要求
  <task>              任务目标 + 核心原则
  <source_content>    待处理内容
  <instruction_priority>  规则优先级声明
  <hard_constraints>  硬约束（必须遵守）
  <style_preference>  软偏好（仅 Notes 有，Polish 无）
  <output_format>     输出格式要求
  <user_request>      用户备注
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
