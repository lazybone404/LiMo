"""
FactCheckEngine — 端到端事实验证引擎

流程：提取关键事实 → 逐条搜索验证 → LLM 交叉核查 → 结构化报告
复用 agent/tools.py 的 search_web / fetch_url，不经过 AgentRunner
"""

import asyncio
import json
import logging
import re
import uuid

from app.agent.tools import search_web, fetch_url
from app.llm.service import LLMService
from app.generation.prompt import (
    CLAIM_EXTRACTION_PROMPT,
    VERIFICATION_PROMPT,
    FACT_CHECK_REPORT_PROMPT,
)

logger = logging.getLogger(__name__)

# ── 常量 ──
MAX_CLAIMS = 5
MAX_SOURCES_PER_CLAIM = 5
FETCH_CONCURRENCY = 5
FETCH_MAX_CHARS = 8000  # 每个来源抓取的字符数


class FactCheckEngine:
    """依赖 LLMService + 现有 Agent 工具（search_web / fetch_url）"""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service

    # ── Phase 1: 提取事实断言 ──

    async def extract_claims(
        self, content: str, cancel_event: asyncio.Event | None = None,
    ) -> list[dict]:
        """LLM 从内容中提取最多 MAX_CLAIMS 条关键事实断言"""
        prompt = CLAIM_EXTRACTION_PROMPT.replace(
            "{content_placeholder}",
            f"\n## 原文\n```\n{content[:8000]}\n```",
        )
        try:
            raw = await self._llm.call(
                prompt, max_tokens=2048, temperature=0.3,
                max_continuations=0, cancel_event=cancel_event,
            )
            return self._parse_json_array(raw)[:MAX_CLAIMS]
        except Exception as e:
            logger.warning(f"提取事实断言失败: {e}")
            return []

    # ── Phase 2: 交叉验证 ──

    async def verify_claim(
        self, claim: dict, cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """验证单条断言：search → fetch → LLM 交叉验证"""
        claim_text = claim.get("text", "")
        claim_id = claim.get("id", "")
        base = {"id": claim_id, "text": claim_text,
                "category": claim.get("category", "general")}

        try:
            # Step 1: 提取关键词并搜索
            keywords = self._extract_keywords(claim_text)
            search_query = keywords if keywords else claim_text[:200]
            try:
                search_result = await search_web(search_query, max_results=MAX_SOURCES_PER_CLAIM)
                search_data = json.loads(search_result) if isinstance(search_result, str) else search_result
                results = search_data.get("results", [])[:MAX_SOURCES_PER_CLAIM]
                search_source = search_data.get("source", "unknown")
            except Exception as e:
                logger.warning("验证 claim '%s': 搜索失败 (%s)", claim_text[:60], e)
                results = []
                search_source = "error"

            if not results:
                logger.info("验证 claim '%s': 搜索无结果 (provider=%s, query=%s)",
                           claim_text[:60], search_source, search_query[:80])

            # Step 2: 并发抓取来源内容
            async def _fetch_one(r: dict) -> dict | None:
                url = r.get("url", "")
                if not url:
                    return None
                try:
                    fetched = await fetch_url(url)
                    if isinstance(fetched, str):
                        # 尝试 JSON 解析判断是否为错误响应
                        try:
                            parsed = json.loads(fetched)
                            if isinstance(parsed, dict) and "error" in parsed:
                                return None
                        except (json.JSONDecodeError, TypeError):
                            pass
                        return {
                            "title": r.get("title", ""),
                            "url": url,
                            "snippet": r.get("snippet", ""),
                            "content": fetched[:FETCH_MAX_CHARS],
                        }
                except Exception:
                    logger.warning("Failed to fetch URL for fact verification", exc_info=True)
                return None

            fetch_tasks = [_fetch_one(r) for r in results]
            fetched_sources = await asyncio.gather(*fetch_tasks)
            sources = [s for s in fetched_sources if s is not None]

            # Step 3: LLM 交叉验证
            if sources:
                sources_text = "\n---\n".join(
                    f"来源 {i+1}: {s['title']}\nURL: {s['url']}\n内容: {s['content']}"
                    for i, s in enumerate(sources)
                )
            else:
                sources_text = "（无可用来源 — 请仅根据你的知识判断，默认设为 unverifiable）"

            prompt = VERIFICATION_PROMPT.format(
                claim_text=claim_text,
                sources_text=sources_text,
            )
            try:
                raw = await self._llm.call(
                    prompt, max_tokens=1024, temperature=0.3,
                    max_continuations=0, cancel_event=cancel_event,
                )
                verification = self._parse_json_object(raw)
            except Exception as e:
                logger.warning("验证 claim '%s': LLM 交叉验证失败 (%s)", claim_text[:60], e)
                verification = {"status": "unverifiable", "confidence": 0.0,
                               "analysis": f"LLM 调用失败: {str(e)[:200]}"}

            return {
                **base,
                "status": verification.get("status", "inconclusive"),
                "confidence": verification.get("confidence", 0.0),
                "sources": [{"title": s["title"], "url": s["url"],
                             "snippet": s.get("snippet", "")} for s in sources],
                "analysis": verification.get("analysis", ""),
                "correction": verification.get("correction"),
            }
        except Exception as e:
            logger.warning("验证 claim '%s': 未预期异常 (%s: %s)",
                          claim_text[:60], type(e).__name__, e)
            return {**base, "status": "unverifiable", "confidence": 0.0,
                    "sources": [], "analysis": f"验证异常: {str(e)[:200]}",
                    "correction": None}

    async def verify_all(
        self, claims: list[dict], cancel_event: asyncio.Event | None = None,
    ) -> list[dict]:
        """批量验证：Semaphore(3) 并发，返回结果保持顺序"""
        if not claims:
            return []

        sem = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def _verify(idx: int, claim: dict) -> tuple[int, dict | None]:
            async with sem:
                if cancel_event and cancel_event.is_set():
                    return (idx, None)
                result = await self.verify_claim(claim, cancel_event)
                return (idx, result)

        results = await asyncio.gather(
            *[_verify(i, c) for i, c in enumerate(claims)]
        )
        return [r for _, r in sorted(results, key=lambda x: x[0]) if r is not None]

    # ── Phase 3: 生成报告 ──

    async def build_report(
        self, original_content: str, verified_claims: list[dict],
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """LLM 汇总验证结果 → 结构化 Markdown 报告（非流式）"""
        claims_summary = self._build_claims_summary(verified_claims)
        prompt = FACT_CHECK_REPORT_PROMPT.format(
            original_content=original_content[:2000],
            claims_json=json.dumps(claims_summary, ensure_ascii=False, indent=2),
        )
        try:
            return await self._llm.call(
                prompt, max_tokens=4096, temperature=0.5,
                max_continuations=0, cancel_event=cancel_event,
            )
        except Exception as e:
            logger.warning(f"生成验证报告失败: {e}")
            return f"# 事实验证报告\n\n报告生成失败: {e}"

    async def build_report_streaming(
        self, original_content: str, verified_claims: list[dict],
        cancel_event: asyncio.Event | None = None,
    ):
        """LLM 汇总验证结果 → 流式 yield 报告 chunk（逐 token）"""
        claims_summary = self._build_claims_summary(verified_claims)
        prompt = FACT_CHECK_REPORT_PROMPT.format(
            original_content=original_content[:2000],
            claims_json=json.dumps(claims_summary, ensure_ascii=False, indent=2),
        )
        try:
            async for chunk in self._llm.stream(
                prompt, max_tokens=4096, temperature=0.5,
                max_continuations=0, cancel_event=cancel_event,
            ):
                yield chunk
        except Exception as e:
            logger.warning(f"生成验证报告失败: {e}")
            yield f"# 事实验证报告\n\n报告生成失败: {e}"

    @staticmethod
    def _build_claims_summary(verified_claims: list[dict]) -> list[dict]:
        return [
            {k: c.get(k) for k in ("id", "text", "status", "confidence",
                                    "analysis", "correction", "sources")}
            for c in verified_claims
        ]

    # ── 端到端接口 ──

    async def run(
        self, content: str, cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """非流式端到端：extract → verify → report → 返回 FactCheckResult dict"""
        fc_id = f"fc_{uuid.uuid4().hex[:12]}"

        claims = await self.extract_claims(content, cancel_event)
        verified_claims = await self.verify_all(claims, cancel_event)
        report = await self.build_report(content, verified_claims, cancel_event)

        verified_count = sum(1 for c in verified_claims if c["status"] == "verified")
        disputed_count = sum(1 for c in verified_claims if c["status"] == "disputed")
        unverifiable_count = sum(1 for c in verified_claims
                                 if c["status"] in ("unverifiable", "inconclusive"))

        total = len(verified_claims) or 1
        overall_score = round((verified_count * 1.0 + disputed_count * 0.3) / total, 2)

        return {
            "id": fc_id, "transcript_id": "", "source_content_id": "", "source_mode": "",
            "verified_count": verified_count, "disputed_count": disputed_count,
            "unverifiable_count": unverifiable_count, "overall_score": overall_score,
            "claims": verified_claims, "summary": "", "content": report,
        }

    async def run_streaming(
        self, content: str, cancel_event: asyncio.Event | None = None,
    ):
        """流式端到端：yield 进度事件，最终 yield done 事件
        事件格式见 plan 中的 SSE 事件序列定义
        """
        fc_id = f"fc_{uuid.uuid4().hex[:12]}"

        # ── Phase 1: Extract ──
        yield {"mode": "fact_check", "phase": "extract",
               "text": "正在分析内容，提取关键事实断言..."}
        if cancel_event and cancel_event.is_set():
            yield {"mode": "fact_check", "type": "done", "id": fc_id,
                   "verified_count": 0, "disputed_count": 0,
                   "unverifiable_count": 0, "overall_score": 0}
            return

        claims = await self.extract_claims(content, cancel_event)
        if not claims:
            yield {"mode": "fact_check", "phase": "extract",
                   "text": "未找到可验证的事实断言"}
            yield {"mode": "fact_check", "type": "done", "id": fc_id,
                   "verified_count": 0, "disputed_count": 0,
                   "unverifiable_count": 0, "overall_score": 0}
            return

        yield {"mode": "fact_check", "phase": "extract", "type": "done",
               "claims_count": len(claims)}

        # ── Phase 2: Verify（并发 + 逐条 yield 结果）──
        yield {"mode": "fact_check", "phase": "verify",
               "text": f"正在交叉验证 {len(claims)} 条事实断言..."}

        total = len(claims)
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        result_queue: asyncio.Queue[tuple[int, dict | None]] = asyncio.Queue()

        async def _verify(idx: int, claim: dict) -> None:
            result: dict | None = None
            try:
                async with sem:
                    if cancel_event and cancel_event.is_set():
                        return
                    result = await self.verify_claim(claim, cancel_event)
            finally:
                await result_queue.put((idx, result))

        tasks = [asyncio.create_task(_verify(i, c)) for i, c in enumerate(claims)]

        results_by_idx: dict[int, dict] = {}
        completed = 0
        while completed < total:
            if cancel_event and cancel_event.is_set():
                break
            idx, result = await result_queue.get()
            completed += 1
            if result:
                results_by_idx[idx] = result
                icon = {"verified": "✅", "disputed": "⚠️",
                        "unverifiable": "❌", "inconclusive": "❓"}.get(
                    result.get("status", ""), "?"
                )
                yield {
                    "mode": "fact_check", "phase": "verify",
                    "claim": completed, "total": total,
                    "claim_id": result["id"],
                    "status": result["status"],
                    "text": f"{icon} {result.get('text', '')[:80]}",
                }

        # 按原始顺序排列
        verified_claims = [results_by_idx[i] for i in range(total) if i in results_by_idx]

        # ── Phase 3: Report（逐 token 流式输出，和生成笔记一样实时展示）──
        if cancel_event and cancel_event.is_set():
            yield {"mode": "fact_check", "type": "done", "id": fc_id,
                   "verified_count": 0, "disputed_count": 0,
                   "unverifiable_count": 0, "overall_score": 0}
            return
        async for chunk in self.build_report_streaming(content, verified_claims, cancel_event):
            if cancel_event and cancel_event.is_set():
                break
            yield {"mode": "fact_check", "phase": "report", "text": chunk}

        # ── Done ──
        verified_count = sum(1 for c in verified_claims if c["status"] == "verified")
        disputed_count = sum(1 for c in verified_claims if c["status"] == "disputed")
        unverifiable_count = sum(1 for c in verified_claims
                                 if c["status"] in ("unverifiable", "inconclusive"))
        total_claims = len(verified_claims) or 1
        overall_score = round(
            (verified_count * 1.0 + disputed_count * 0.3) / total_claims, 2)

        yield {
            "mode": "fact_check", "type": "done",
            "id": fc_id,
            "verified_count": verified_count,
            "disputed_count": disputed_count,
            "unverifiable_count": unverifiable_count,
            "overall_score": overall_score,
        }

    # ── 关键词提取 ──

    @staticmethod
    def _extract_keywords(text: str) -> str:
        """从断言中提取搜索关键词。简单启发式：保留英文短语、数字、中文专有名词。"""
        import re
        parts: list[str] = []
        # 提取英文/数字组合（如 "OpenAI GPT-5 2026"）+ 单个大写开头的专有名词
        eng = re.findall(r'[A-Za-z0-9]+(?:[-._][A-Za-z0-9]+)+|[A-Z][a-z]+(?:\s[A-Z][a-z]+)+|(?:[A-Z][a-z]{2,}|[A-Z]{2,})', text)
        if eng:
            parts.extend(eng)
        # 提取数字+单位（如 "5000亿美元" "2026年"）
        nums = re.findall(r'\d+(?:\.\d+)?[万亿千百%年美元欧元]?[亿万千百美元欧元]?', text)
        if nums:
            parts.extend(nums[:3])
        # 提取中文名词（2-4 字，不含常见虚词）
        zh_words = re.findall(r'[一-鿿]{2,4}', text)
        stopwords = {'这是一个', '这是', '这个', '那个', '一些', '以及', '或者', '但是', '因为', '所以',
                     '可以', '应该', '需要', '已经', '没有', '他们', '我们', '什么', '怎么', '如何',
                     '进行', '使用', '通过', '根据', '关于', '对于', '其中', '其他', '这些', '那些'}
        for w in zh_words:
            if w not in stopwords and len(w) >= 2:
                parts.append(w)
        # 去重并限制长度
        seen = set()
        result = []
        for p in parts:
            if p.lower() not in seen:
                seen.add(p.lower())
                result.append(p)
        query = ' '.join(result[:8])  # 最多 8 个关键词
        return query if query else text[:200]

    # ── JSON 解析辅助 ──

    @staticmethod
    def _parse_json_array(raw: str) -> list[dict]:
        """从 LLM 响应中提取 JSON 数组"""
        raw = raw.strip()
        # 直接解析
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "claims" in result:
                return result["claims"]
        except json.JSONDecodeError:
            pass
        # 从 markdown 代码块提取
        m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 从任意位置提取 JSON 数组（贪婪匹配最外层 []）
        m = re.search(r'\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\]]*\])*\])*\]', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"无法解析 claims JSON: {raw[:200]}")
        return []

    @staticmethod
    def _parse_json_object(raw: str) -> dict:
        """从 LLM 响应中提取 JSON 对象"""
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r'\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"status": "inconclusive", "confidence": 0.0,
                "analysis": f"无法解析: {raw[:200]}", "correction": None}
