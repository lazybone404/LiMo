"""
PromptBuilder — prompt 构建与派发

把 Generator 中散落的 prompt 选择逻辑集中到一处：
- 根据 source_type + mode 选择正确的 prompt 函数
- Excel 特殊处理（report + summary 双输出）
- 统一注入 target_language、user_notes、formats 等参数
"""

from .prompt import (
    build_notes_prompt,
    build_polish_prompt,
    build_excel_summary_prompt,
)


class PromptBuilder:
    """Prompt 构建器。无状态——每次 build() 传入所有参数。"""

    def __init__(self, target_language: str = "zh-CN"):
        self.target_language = target_language

    def build(
        self,
        source_type: str,
        mode: str,
        clean_text: str,
        title: str = "",
        style: str = "detailed",
        formats: list[str] | None = None,
        user_notes: str = "",
        reference_texts: list[dict] | None = None,
    ) -> str:
        """构建单个 prompt。

        mode: "notes" | "polished" | "report" | "summary"
        """
        if source_type == "xlsx":
            # Excel 只生成数据摘要（LLM 无法做真正的数值分析）
            return build_excel_summary_prompt(clean_text, style, user_notes=user_notes,
                                               target_language=self.target_language)

        if mode == "polished":
            return build_polish_prompt(
                clean_text, title, style, source_type,
                formats=formats, user_notes=user_notes,
                target_language=self.target_language,
            )
        else:
            return build_notes_prompt(
                clean_text, title, style, source_type,
                formats, user_notes=user_notes,
                target_language=self.target_language,
                reference_texts=reference_texts,
            )

    def build_both(
        self,
        source_type: str,
        clean_text: str,
        title: str = "",
        style: str = "detailed",
        formats: list[str] | None = None,
        user_notes: str = "",
        reference_texts: list[dict] | None = None,
    ) -> tuple[str, str]:
        """构建双产物 prompt：(polished_prompt, notes_prompt)。

        Excel 只生成数据摘要，两个产物内容相同。
        """
        if source_type == "xlsx":
            # Excel 只有数据摘要一个产物（分析报告已移除——LLM 无法做真正的数值分析）
            sp = build_excel_summary_prompt(clean_text, style, user_notes=user_notes,
                                             target_language=self.target_language)
            return sp, sp  # 双产物接口兼容，两份相同

        polished_prompt = build_polish_prompt(
            clean_text, title, style, source_type,
            formats=formats, user_notes=user_notes,
            target_language=self.target_language,
        )
        notes_prompt = build_notes_prompt(
            clean_text, title, style, source_type,
            formats, user_notes=user_notes,
            target_language=self.target_language,
            reference_texts=reference_texts,
        )
        return polished_prompt, notes_prompt
