"""
会话管理 — 参照 Pi 的按项目隔离 session 模式

从 chat.py 中抽出对话 CRUD 逻辑，提供统一的会话生命周期管理。
"""

import uuid
import logging
from datetime import datetime

from app.core.types import ChatMessage, Conversation

logger = logging.getLogger(__name__)

# 历史消息数量上限
MAX_HISTORY_MESSAGES = 40


class SessionManager:
    """对话会话管理。封装 store 的 conversation CRUD 操作。"""

    def __init__(self, store: object):
        self._store = store

    def load_or_create(
        self,
        conversation_id: str,
        question: str,
        model_id: int | None = None,
        doc_ids: list[str] | None = None,
    ) -> dict:
        """加载已有对话或创建新对话。返回 conversation dict。"""
        conv = None
        if conversation_id:
            conv = self._store.get_conversation(conversation_id)

        if not conv:
            conv_data = Conversation(
                id=f"conv_{uuid.uuid4().hex[:12]}",
                title=question[:60] + ("..." if len(question) > 60 else ""),
                model_id=model_id,
                doc_ids=doc_ids or [],
            )
            self._store.save_conversation(conv_data)
            conv = conv_data.to_dict()

        return conv

    def get_history(self, conv: dict) -> list[dict]:
        """从对话记录中提取历史上下文（最近 N 条消息）"""
        messages = conv.get("messages", [])[-MAX_HISTORY_MESSAGES:]
        return [{"role": m["role"], "content": m["content"]} for m in messages]

    def set_clear_context(self, conv_id: str, index: int) -> bool:
        """清除上下文边界（C4 clearContext）— 该索引前的消息不再携带进上下文、不参与压缩。
        同时把压缩基准 last_compress_index 重置到 clear 点（避免摘要残留被清空的旧上下文）。"""
        conv = self._store.get_conversation(conv_id)
        if not conv:
            return False
        conv["clear_context_at"] = index
        if conv.get("last_compress_index", 0) < index:
            conv["last_compress_index"] = index
        conv["updated_at"] = datetime.now().isoformat()
        self._store.save_conversation(conv)
        return True

    def append_user_message(
        self,
        conv_id: str,
        question: str,
        doc_refs: list[dict] | None = None,
    ) -> None:
        """追加用户消息到对话（立即保存，刷新不丢）。doc_refs = 本条 @ 的文档"""
        conv = self._store.get_conversation(conv_id)
        if not conv:
            return
        msg = ChatMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            role="user",
            content=question,
            doc_refs=doc_refs,
        )
        conv.setdefault("messages", []).append(msg.to_dict())
        conv["updated_at"] = datetime.now().isoformat()

        # 自动更新标题（首次对话）
        title_prefix = question[:60] + ("..." if len(question) > 60 else "")
        if conv["title"] == title_prefix or conv["title"] == "新对话":
            conv["title"] = title_prefix

        self._store.save_conversation(conv)

    def append_assistant_message(
        self,
        conv_id: str,
        answer: str,
        doc_refs: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        thinking: str | None = None,
        follow_ups: list[str] | None = None,
        server_tool_calls: list | None = None,
        annotations: list | None = None,
    ) -> None:
        """追加 AI 回复到对话。扩展字段：doc_refs（引用文档）/ tool_calls（工具气泡）/
        thinking（思考段）/ follow_ups（追问建议，独立第二次调用的产物）/
        server_tool_calls + annotations（深度联网的原始协议字段，只保管不解析）"""
        conv = self._store.get_conversation(conv_id)
        if not conv:
            return
        msg = ChatMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            role="assistant",
            content=answer,
            doc_refs=doc_refs,
            tool_calls=tool_calls,
            thinking=thinking,
            follow_ups=follow_ups,
            server_tool_calls=server_tool_calls,
            annotations=annotations,
        )
        conv.setdefault("messages", []).append(msg.to_dict())
        conv["updated_at"] = datetime.now().isoformat()
        self._store.save_conversation(conv)

    def set_title_if_default(self, conv_id: str, title: str, question: str) -> bool:
        """用模型给的短标题替换**自动生成的**标题；用户手改过的标题不动。

        返回 `True` = 真的改了（调用方据此决定要不要推 `title` 事件）。
        ⚠️ **判据留在这里一处**：前端只负责「收到事件就改本地状态」，不自己判断
        「这个标题该不该覆盖」—— 那份判断要读会话的当前标题，前端手上那份可能是
        上一轮同步下来的旧值，两边各判一次就会分叉（谁赢取决于时序）。
        """
        title = (title or "").strip()
        if not title:
            return False
        conv = self._store.get_conversation(conv_id)
        if not conv:
            return False
        # ⚠️ 与 `append_user_message` 生成默认标题的表达式**逐字同源**（含省略号那段）。
        # 两处任一改动都会让「识别哪条标题是自动生成的」失效 —— 而失效方向是
        # **用户的标题被模型覆盖**，界面上看起来只是「标题自己变了」。
        default_title = question[:60] + ("..." if len(question) > 60 else "")
        if conv.get("title") not in (default_title, "新对话"):
            return False
        conv["title"] = title[:80]
        conv["updated_at"] = datetime.now().isoformat()
        self._store.save_conversation(conv)
        return True
