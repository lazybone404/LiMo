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
    build_excel_report_prompt,
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
    ) -> str:
        """构建单个 prompt。

        mode: "notes" | "polished" | "report" | "summary"
        """
        if source_type == "xlsx":
            if mode == "report":
                return build_excel_report_prompt(clean_text, style, user_notes=user_notes)
            elif mode == "summary":
                return build_excel_summary_prompt(clean_text, style, user_notes=user_notes)
            else:
                # Excel 默认用 summary
                return build_excel_summary_prompt(clean_text, style, user_notes=user_notes)

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
            )

    def build_both(
        self,
        source_type: str,
        clean_text: str,
        title: str = "",
        style: str = "detailed",
        formats: list[str] | None = None,
        user_notes: str = "",
    ) -> tuple[str, str]:
        """构建双产物 prompt：(polished_prompt, notes_prompt)。

        Excel 特殊处理：返回 (report_prompt, summary_prompt)。
        """
        if source_type == "xlsx":
            report_prompt = build_excel_report_prompt(clean_text, style, user_notes=user_notes)
            summary_prompt = build_excel_summary_prompt(clean_text, style, user_notes=user_notes)
            return report_prompt, summary_prompt

        polished_prompt = build_polish_prompt(
            clean_text, title, style, source_type,
            formats=formats, user_notes=user_notes,
            target_language=self.target_language,
        )
        notes_prompt = build_notes_prompt(
            clean_text, title, style, source_type,
            formats, user_notes=user_notes,
            target_language=self.target_language,
        )
        return polished_prompt, notes_prompt
