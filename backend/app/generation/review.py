"""
ReviewEngine — AI 报告审核引擎（替代旧 FactCheckEngine）

四阶段管道：
  ① Static Review（不联网）→ 提取 Claim + 分类 + 发现问题 + 风险评分
  ② Verification Planning（一次 LLM）→ 决定每条的验证策略
  ③ Evidence Collection + Review → 搜索/抓取/评估
  ④ AI Review Report（流式）→ 红黄绿报告 + 可信度

依赖：LLMService + agent/tools.py 的 search_web / fetch_url
"""

import asyncio
import json
import logging
import re
import uuid
from difflib import SequenceMatcher

from app.agent.tools import search_web, fetch_url
from app.core.text_utils import split_into_chunks
from app.agent.evidence import (
    lookup_evidence, store_evidence, is_url_fetched, mark_url_fetched,
    format_evidence_from_search, format_evidence,
)
from app.llm.service import LLMService
from app.generation.prompt import (
    STATIC_REVIEW_PROMPT,
    VERIFICATION_PLANNER_PROMPT,
    EVIDENCE_REVIEWER_PROMPT,
    AI_REVIEW_REPORT_PROMPT,
)

logger = logging.getLogger(__name__)

# ── 常量 ──
MAX_CHUNK_CHARS = 8000      # 每 chunk 最大字符数
MAX_CLAIMS = 20             # 最多提取断言数（static review 后截断）
MAX_SEARCH_RESULTS = 5      # 每条 claim 最多搜索结果
MAX_SOURCES_PER_CLAIM = 3   # 每条 claim 最多抓取来源数
FETCH_MAX_CHARS = 6000      # 每个来源抓取字符数
FETCH_CONCURRENCY = 5       # 并发抓取数
VERIFY_CONCURRENCY = 3      # 并发验证 claim 数


class ReviewEngine:
    """AI 报告审核引擎。"""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service

    # ═══════════════════════════════════════════════════════════
    # Phase 1: Static Review
    # ═══════════════════════════════════════════════════════════

    async def static_review(
        self, content: str, cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """不联网的静态审核：提取 Claim + 分类 + Issues + 风险评分。

        短文本（≤MAX_CHUNK_CHARS）直接单次提取。
        长文本切分为重叠块，并行提取后合并去重。

        返回: {has_content, claims: [...], issues: [...]}
        """
        chunks = split_into_chunks(content, MAX_CHUNK_CHARS, overlap=200)
        max_chunks = 32  # 安全上限，防止失控
        if len(chunks) > max_chunks:
            logger.warning("内容过长 (%d chars, %d chunks), 截断为 %d chunks",
                          len(content), len(chunks), max_chunks)
            chunks = chunks[:max_chunks]

        if len(chunks) == 1:
            # 短文本 — 单次提取
            prompt = STATIC_REVIEW_PROMPT.replace("{content}", chunks[0])
            raw = await self._llm.call(
                prompt, max_tokens=4096, temperature=0.3,
                max_continuations=0, cancel_event=cancel_event,
            )
            result = self._parse_json_object(raw)
        else:
            # 长文本 — 分片并行提取
            logger.info("Static review: %d chars → %d chunks, parallel extraction",
                       len(content), len(chunks))

            async def extract_one(i: int, chunk: str) -> dict:
                if cancel_event and cancel_event.is_set():
                    return {"has_content": False, "claims": [], "issues": []}
                prompt = STATIC_REVIEW_PROMPT.replace("{content}", chunk)
                raw = await self._llm.call(
                    prompt, max_tokens=4096, temperature=0.3,
                    max_continuations=0, cancel_event=cancel_event,
                )
                r = self._parse_json_object(raw)
                # 标注 chunk 索引用于调试
                for c in r.get("claims", []):
                    c["_chunk"] = i
                return r

            tasks = [extract_one(i, chunk) for i, chunk in enumerate(chunks)]
            chunk_results = await asyncio.gather(*tasks)

            # 合并 claims — 去重
            all_claims = []
            all_issues = []
            has_content = False
            for r in chunk_results:
                if r.get("has_content"):
                    has_content = True
                all_claims.extend(r.get("claims", []))
                all_issues.extend(r.get("issues", []))

            # 去重：精确匹配 + 文本相似度 > 0.85
            deduped = []
            for claim in all_claims:
                claim_text = (claim.get("claim") or "").strip().lower()
                if not claim_text:
                    continue
                is_dup = False
                for existing in deduped:
                    existing_text = (existing.get("claim") or "").strip().lower()
                    if claim_text == existing_text:
                        is_dup = True
                        break
                    if len(claim_text) > 20 and len(existing_text) > 20:
                        ratio = SequenceMatcher(None, claim_text, existing_text).ratio()
                        if ratio > 0.85:
                            is_dup = True
                            break
                if not is_dup:
                    deduped.append(claim)

            # 截断到 MAX_CLAIMS
            claims = deduped[:MAX_CLAIMS]
            # 清理内部 _chunk 标记
            for c in claims:
                c.pop("_chunk", None)
            result = {"has_content": has_content, "claims": claims, "issues": all_issues}

        if not result.get("has_content"):
            return {"has_content": False, "claims": [], "issues": []}

        claims = result.get("claims", [])[:MAX_CLAIMS]
        issues = result.get("issues", [])

        # 确保每条 claim 有 id
        for i, c in enumerate(claims):
            if not c.get("id"):
                c["id"] = f"c{i+1}"

        return {"has_content": True, "claims": claims, "issues": issues}

    # ═══════════════════════════════════════════════════════════
    # Phase 2: Verification Planning
    # ═══════════════════════════════════════════════════════════

    async def plan_verification(
        self, claims: list[dict], cancel_event: asyncio.Event | None = None,
    ) -> list[dict]:
        """一次 LLM 调用：为每条 claim 决定验证策略 + 生成 search query。

        返回: [{id, strategy, confidence, reasoning, queries: [{query, intent, domain}]}]
        """
        if not claims:
            return []

        prompt = VERIFICATION_PLANNER_PROMPT.replace(
            "{claims_json}",
            json.dumps(claims, ensure_ascii=False, indent=2),
        )
        raw = await self._llm.call(
            prompt, max_tokens=4096, temperature=0.3,
            max_continuations=0, cancel_event=cancel_event,
        )
        return self._parse_json_array(raw)

    # ═══════════════════════════════════════════════════════════
    # Phase 3: Evidence Collection + Review
    # ═══════════════════════════════════════════════════════════

    async def collect_and_review(
        self, claims: list[dict], plans: list[dict],
        cancel_event: asyncio.Event | None = None,
    ) -> list[dict]:
        """为 need_web 的 claim 收集证据 + 全部 claim 评估。

        返回: [{id, verdict, evidence_strength, ...}]
        """
        # 建立 plan 索引
        plan_map = {p["id"]: p for p in plans}

        # 分离 need_web 和其他
        web_claims = []
        other_claims = []
        for c in claims:
            plan = plan_map.get(c["id"], {})
            if plan.get("strategy") == "need_web":
                web_claims.append((c, plan))
            else:
                other_claims.append((c, plan))

        # 并行处理 need_web claims: 搜索 → 抓取 → LLM 评估
        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)

        async def _review_one(claim: dict, plan: dict) -> dict:
            async with sem:
                if cancel_event and cancel_event.is_set():
                    return self._empty_review(claim)
                return await self._review_with_web(claim, plan, cancel_event)

        async def _review_llm(claim: dict, plan: dict) -> dict:
            if cancel_event and cancel_event.is_set():
                return self._empty_review(claim)
            return await self._review_llm_only(claim, plan, cancel_event)

        # 并行执行所有 review
        tasks = []
        for c, p in web_claims:
            tasks.append(_review_one(c, p))
        for c, p in other_claims:
            tasks.append(_review_llm(c, p))

        results = await asyncio.gather(*tasks)
        return list(results)

    async def _review_with_web(
        self, claim: dict, plan: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """联网验证单条 claim：搜索 → 抓取 → LLM 评估"""
        claim_text = claim.get("text", "")

        # 检查缓存
        cached = lookup_evidence(claim_text)
        if cached:
            logger.info("Evidence cache hit for: %s", claim_text[:60])
            evidence_items = cached
        else:
            evidence_items = []

            for q in plan.get("queries", [])[:2]:  # 最多 2 个 query
                if cancel_event and cancel_event.is_set():
                    break
                query_text = q.get("query", "")
                intent = q.get("intent", "general")
                domain = q.get("domain", "")

                # 搜索
                try:
                    if domain and intent in ("official", "product"):
                        full_query = f"site:{domain} {query_text}"
                    else:
                        full_query = query_text

                    search_result = await search_web(full_query, max_results=MAX_SEARCH_RESULTS)
                    search_data = json.loads(search_result) if isinstance(search_result, str) else search_result
                    results = search_data.get("results", [])

                    # 规则过滤：优先官方/权威域名
                    filtered = self._filter_sources(results, intent)
                    to_fetch = filtered[:MAX_SOURCES_PER_CLAIM]

                    # 并行抓取
                    fetch_tasks = [self._fetch_one(r) for r in to_fetch]
                    fetched = await asyncio.gather(*fetch_tasks)
                    for f in fetched:
                        if f:
                            evidence_items.append(f)
                except Exception as e:
                    logger.warning("搜索 claim '%s' 失败: %s", claim_text[:60], e)

            # 缓存证据（24h TTL）
            if evidence_items:
                store_evidence(claim_text, evidence_items)

        # LLM 评估
        claim_copy = {k: claim.get(k) for k in ("id", "text", "category", "risk", "priority")}
        evidence_json = json.dumps(evidence_items, ensure_ascii=False, indent=2)
        prompt = EVIDENCE_REVIEWER_PROMPT.replace(
            "{claim_json}", json.dumps(claim_copy, ensure_ascii=False, indent=2),
        ).replace("{evidence_json}", evidence_json if evidence_items else "[]")

        try:
            raw = await self._llm.call(
                prompt, max_tokens=2048, temperature=0.3,
                max_continuations=0, cancel_event=cancel_event,
            )
            review = self._parse_json_object(raw)
        except Exception as e:
            logger.warning("LLM 评估 claim '%s' 失败: %s", claim.get("text", "")[:60], e)
            review = self._empty_review(claim)

        review["id"] = claim.get("id", "")
        review["text"] = claim.get("text", "")
        review["strategy"] = "need_web"
        review["evidence_items"] = evidence_items
        return review

    async def _review_llm_only(
        self, claim: dict, plan: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """LLM 知识评估（不联网）"""
        strategy = plan.get("strategy", "llm_known")
        claim_copy = {k: claim.get(k) for k in ("id", "text", "category", "risk", "priority")}

        # 使用 Evidence Reviewer prompt，证据为空
        prompt = EVIDENCE_REVIEWER_PROMPT.replace(
            "{claim_json}", json.dumps(claim_copy, ensure_ascii=False, indent=2),
        ).replace("{evidence_json}", json.dumps([
            {"source": "LLM 训练知识", "domain": "internal",
             "note": f"策略为 {strategy}，基于模型训练知识评估"}
        ], ensure_ascii=False))

        try:
            raw = await self._llm.call(
                prompt, max_tokens=2048, temperature=0.3,
                max_continuations=0, cancel_event=cancel_event,
            )
            review = self._parse_json_object(raw)
        except Exception as e:
            logger.warning("LLM 评估 claim '%s' 失败: %s", claim.get("text", "")[:60], e)
            review = self._empty_review(claim)

        review["id"] = claim.get("id", "")
        review["text"] = claim.get("text", "")
        review["strategy"] = strategy
        review["evidence_items"] = []
        return review

    async def _fetch_one(self, r: dict) -> dict | None:
        """抓取单个来源（URL 去重 + 统一证据格式）"""
        url = r.get("url", "")
        if not url:
            return None
        # 去重
        if is_url_fetched(url):
            return None
        mark_url_fetched(url)
        try:
            fetched = await fetch_url(url)
            if isinstance(fetched, str):
                try:
                    parsed = json.loads(fetched)
                    if isinstance(parsed, dict) and "error" in parsed:
                        return None
                except (json.JSONDecodeError, TypeError):
                    pass
                domain = self._extract_domain(url)
                return format_evidence(
                    title=r.get("title", ""),
                    url=url,
                    snippet=r.get("snippet", ""),
                    content=fetched[:FETCH_MAX_CHARS],
                    domain=domain,
                    source="web",
                )
        except Exception:
            pass
        return None

    @staticmethod
    def _filter_sources(results: list[dict], intent: str) -> list[dict]:
        """规则过滤搜索结果：优先官方/权威域名"""
        HIGH_TRUST_DOMAINS = {".gov", ".edu", ".org", "github.com", "gitlab.com",
                              "wikipedia.org", "pypi.org", "npmjs.com"}
        OFFICIAL_LIKE = {"docs.", "developer.", "dev.", "api.", "www."}

        scored = []
        for r in results:
            url = r.get("url", "")
            domain = ReviewEngine._extract_domain(url)
            score = 0
            if any(d in domain for d in HIGH_TRUST_DOMAINS):
                score += 10
            if intent in ("official", "product") and any(p in domain for p in OFFICIAL_LIKE):
                score += 5
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 提取域名"""
        import re as _re
        m = _re.search(r'https?://([^/]+)', url)
        return m.group(1) if m else ""

    @staticmethod
    def _empty_review(claim: dict) -> dict:
        return {
            "id": claim.get("id", ""), "text": claim.get("text", ""),
            "verdict": "证据不足", "evidence_strength": "None",
            "strategy": "error", "evidence_items": [],
            "action": None, "summary": "验证过程异常，无法完成评估",
        }

    # ═══════════════════════════════════════════════════════════
    # Phase 4: Report 生成
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _build_references_section(review_results: list[dict]) -> str:
        """从验证结果中提取所有证据 URL，构建参考来源 section。

        程序化注入，不依赖 LLM 是否按格式输出 URL。
        去重 + 按域名分组，方便用户点击查看原始来源。
        """
        seen: set[str] = set()
        items: list[dict] = []
        for r in review_results:
            for ev in r.get("evidence_items", []) or []:
                url = ev.get("url", "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append(ev)

        if not items:
            return ""

        lines = ["\n---\n\n## 📎 参考来源\n"]
        for i, ev in enumerate(items, 1):
            title = ev.get("title", "").strip() or "未命名来源"
            url = ev.get("url", "").strip()
            domain = ev.get("domain", "").strip()
            domain_str = f" ({domain})" if domain else ""
            lines.append(f"{i}. [{title}]({url}){domain_str}")

        return "\n".join(lines) + "\n"

    async def generate_report(
        self, static_result: dict, review_results: list[dict],
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """非流式生成审核报告"""
        # 确定验证方式描述
        strategies = set(r.get("strategy", "") for r in review_results)
        verify_methods = []
        if "need_web" in strategies:
            verify_methods.append("联网搜索验证")
        if "llm_known" in strategies:
            verify_methods.append("模型知识评估")
        if not verify_methods:
            verify_methods.append("模型知识评估")

        prompt = AI_REVIEW_REPORT_PROMPT.replace(
            "{static_review_json}",
            json.dumps(static_result, ensure_ascii=False, indent=2),
        ).replace(
            "{verification_results_json}",
            json.dumps(review_results, ensure_ascii=False, indent=2),
        ).replace("{verify_methods}", "、".join(verify_methods))

        try:
            report = await self._llm.call(
                prompt, max_tokens=4096, temperature=0.5,
                max_continuations=0, cancel_event=cancel_event,
            )
            # 后处理注入参考来源（不依赖 LLM 是否按格式输出 URL）
            refs = self._build_references_section(review_results)
            return report + refs
        except Exception as e:
            logger.warning("生成审核报告失败: %s", e)
            return f"# AI 审核报告\n\n报告生成失败: {e}"

    async def generate_report_streaming(
        self, static_result: dict, review_results: list[dict],
        cancel_event: asyncio.Event | None = None,
    ):
        """流式生成审核报告（逐 token yield）"""
        strategies = set(r.get("strategy", "") for r in review_results)
        verify_methods = []
        if "need_web" in strategies:
            verify_methods.append("联网搜索验证")
        if "llm_known" in strategies:
            verify_methods.append("模型知识评估")
        if not verify_methods:
            verify_methods.append("模型知识评估")

        prompt = AI_REVIEW_REPORT_PROMPT.replace(
            "{static_review_json}",
            json.dumps(static_result, ensure_ascii=False, indent=2),
        ).replace(
            "{verification_results_json}",
            json.dumps(review_results, ensure_ascii=False, indent=2),
        ).replace("{verify_methods}", "、".join(verify_methods))

        try:
            async for chunk in self._llm.stream(
                prompt, max_tokens=4096, temperature=0.5,
                max_continuations=0, cancel_event=cancel_event,
            ):
                yield chunk
            # 后处理注入参考来源（不依赖 LLM 是否按格式输出 URL）
            refs = self._build_references_section(review_results)
            if refs:
                yield refs
        except Exception as e:
            logger.warning("生成审核报告失败: %s", e)
            yield f"\n\n报告生成失败: {e}"

    # ═══════════════════════════════════════════════════════════
    # 端到端接口
    # ═══════════════════════════════════════════════════════════

    async def run(
        self, content: str, cancel_event: asyncio.Event | None = None,
        verify_mode: str = "llm",
    ) -> dict:
        """非流式端到端审核。verify_mode: llm（默认）| web（联网）"""
        review_id = f"rv_{uuid.uuid4().hex[:12]}"

        # Phase 1: Static Review
        static_result = await self.static_review(content, cancel_event)
        if not static_result.get("has_content"):
            return {
                "id": review_id, "has_content": False,
                "claims": [], "issues": [], "reviews": [],
                "content": "# AI 审核报告\n\n未发现可审核的事实性内容。",
            }

        # Phase 2: Verification Planning
        plans = await self.plan_verification(static_result["claims"], cancel_event)

        # Phase 3: Evidence Collection + Review
        reviews = await self.collect_and_review(
            static_result["claims"], plans, cancel_event,
        )

        # Phase 4: Report
        report = await self.generate_report(static_result, reviews, cancel_event)

        # 计算可信度
        trustworthiness = self._calc_trustworthiness(reviews, static_result.get("issues", []))

        return {
            "id": review_id, "has_content": True,
            "claims": static_result["claims"],
            "issues": static_result["issues"],
            "reviews": reviews,
            "content": report,
            "trustworthiness": trustworthiness["score"],
            "trustworthiness_label": trustworthiness["label"],
            "deductions": trustworthiness["deductions"],
        }

    async def run_streaming(
        self, content: str, cancel_event: asyncio.Event | None = None,
        verify_mode: str = "llm",
    ):
        """流式端到端审核：yield SSE 事件。verify_mode: llm（默认）| web（联网）"""
        review_id = f"rv_{uuid.uuid4().hex[:12]}"

        # ── Phase 1: Static Review ──
        yield {"mode": "fact_check", "phase": "static_review",
               "text": "正在分析文档，提取断言与问题..."}
        if cancel_event and cancel_event.is_set():
            yield self._done_event(review_id, 0, "")
            return

        static_result = await self.static_review(content, cancel_event)
        if not static_result.get("has_content"):
            yield {"mode": "fact_check", "phase": "static_review",
                   "text": "未发现可审核的事实性内容"}
            yield self._done_event(review_id, 0, "")
            return

        claims = static_result["claims"]
        issues = static_result["issues"]

        yield {
            "mode": "fact_check", "phase": "static_review",
            "summary": {
                "claims_count": len(claims),
                "facts": sum(1 for c in claims if c.get("category") == "fact"),
                "data": sum(1 for c in claims if c.get("category") == "data"),
                "opinion": sum(1 for c in claims if c.get("category") == "opinion"),
            },
            "issues": [
                {"type": i.get("type", ""), "text": i.get("text", ""),
                 "detail": i.get("detail", ""),
                 "action": i.get("action", "")}
                for i in issues
            ],
        }
        yield {"mode": "fact_check", "phase": "static_review", "type": "done"}

        # ── Phase 2: Verification Planning ──
        if cancel_event and cancel_event.is_set():
            yield self._done_event(review_id, 0, "")
            return

        yield {"mode": "fact_check", "phase": "verify",
               "text": "正在规划验证策略..."}

        plans = await self.plan_verification(claims, cancel_event)

        # 分离策略
        web_count = sum(1 for p in plans if p.get("strategy") == "need_web")
        llm_count = sum(1 for p in plans if p.get("strategy") == "llm_known")
        skip_count = sum(1 for p in plans if p.get("strategy") in ("skip_opinion", "cannot_verify"))

        if web_count > 0:
            yield {"mode": "fact_check", "phase": "verify",
                   "strategy": "web",
                   "text": f"联网验证 {web_count} 条高风险断言..."}
        if llm_count > 0:
            yield {"mode": "fact_check", "phase": "verify",
                   "strategy": "llm",
                   "text": f"基于模型知识评估 {llm_count} 条..."}

        # ── Phase 3: Evidence Collection + Review ──
        total = len(claims)
        claim_map = {c["id"]: c for c in claims}
        plan_map = {p["id"]: p for p in plans}

        # 分组处理
        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
        result_queue: asyncio.Queue[tuple[int, dict | None]] = asyncio.Queue()

        async def _verify_item(idx: int) -> None:
            claim = claims[idx]
            plan = plan_map.get(claim["id"], {})
            strategy = plan.get("strategy", "llm_known")
            try:
                async with sem:
                    if cancel_event and cancel_event.is_set():
                        return
                    if strategy == "need_web":
                        result = await self._review_with_web(claim, plan, cancel_event)
                    else:
                        result = await self._review_llm_only(claim, plan, cancel_event)
                    await result_queue.put((idx, result))
            except (Exception, asyncio.CancelledError):
                await result_queue.put((idx, self._empty_review(claim)))

        tasks = [asyncio.create_task(_verify_item(i)) for i in range(total)]
        results_by_idx: dict[int, dict] = {}
        completed = 0

        try:
            while completed < total:
                if cancel_event and cancel_event.is_set():
                    break
                idx, result = await result_queue.get()
                completed += 1
                if result:
                    results_by_idx[idx] = result
                    verdict_icon = {
                        "基本可信": "✅", "存疑": "⚠️",
                        "证据不足": "❓", "证据矛盾": "🚫",
                        "观点不验证": "💬",
                    }.get(result.get("verdict", ""), "?")

                    basis_label = {
                        "need_web": "🌐", "llm_known": "🧠",
                        "skip_opinion": "📏", "cannot_verify": "❓",
                    }.get(result.get("strategy", ""), "?")

                    yield {
                        "mode": "fact_check", "phase": "verify",
                        "claim": completed, "total": total,
                        "claim_id": result["id"],
                        "verdict": result.get("verdict", ""),
                        "evidence_strength": result.get("evidence_strength", ""),
                        "basis": result.get("strategy", ""),
                        "action": result.get("action"),
                        "text": f"{verdict_icon} {basis_label} {result.get('text', '')[:80]}",
                    }
        finally:
            # 取消/异常时清理所有后台任务
            if cancel_event and cancel_event.is_set():
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        reviews = [results_by_idx[i] for i in range(total) if i in results_by_idx]

        # ── Phase 4: Report（流式）──
        if cancel_event and cancel_event.is_set():
            yield self._done_event(review_id, 0, "")
            return

        async for chunk in self.generate_report_streaming(
            static_result, reviews, cancel_event,
        ):
            if cancel_event and cancel_event.is_set():
                break
            yield {"mode": "fact_check", "phase": "review", "text": chunk}

        # ── Done ──
        tw = self._calc_trustworthiness(reviews, issues)
        yield {
            "mode": "fact_check", "type": "done",
            "id": review_id,
            "has_content": True,
            "verified_count": sum(1 for r in reviews if r.get("verdict") == "基本可信"),
            "disputed_count": sum(1 for r in reviews
                                 if r.get("verdict") in ("存疑", "证据矛盾")),
            "unverifiable_count": sum(1 for r in reviews
                                      if r.get("verdict") in ("证据不足", "观点不验证")),
            "trustworthiness": tw["score"],
            "trustworthiness_label": tw["label"],
            "deductions": tw["deductions"],
        }

    @staticmethod
    def _done_event(review_id: str, trustworthiness: float, label: str) -> dict:
        return {
            "mode": "fact_check", "type": "done",
            "id": review_id,
            "has_content": False,
            "verified_count": 0, "disputed_count": 0, "unverifiable_count": 0,
            "trustworthiness": trustworthiness,
            "trustworthiness_label": label,
            "deductions": [],
        }

    @staticmethod
    def _calc_trustworthiness(reviews: list[dict], issues: list[dict]) -> dict:
        """计算报告可信度（0-100）"""
        if not reviews:
            return {"score": 0, "label": "无法评估", "deductions": []}

        base = 85.0
        deductions = []

        # 扣分项
        weak_count = sum(1 for r in reviews
                        if r.get("evidence_strength") in ("Weak", "None"))
        if weak_count > 0:
            d = min(30, weak_count * 8)
            base -= d
            deductions.append(f"证据不足（{weak_count} 条）")

        uncertain_count = sum(1 for r in reviews
                             if r.get("verdict") in ("证据不足", "存疑"))
        if uncertain_count > 0:
            d = min(20, uncertain_count * 5)
            base -= d
            deductions.append(f"存疑或无法确认（{uncertain_count} 条）")

        issue_penalty = min(15, len(issues) * 5)
        if issue_penalty > 0:
            base -= issue_penalty
            deductions.append(f"内容问题（{len(issues)} 处）")

        base = max(15, base)

        if base >= 75:
            label = "较高"
        elif base >= 50:
            label = "中等"
        else:
            label = "偏低"

        return {"score": round(base), "label": label, "deductions": deductions}

    # ═══════════════════════════════════════════════════════════
    # JSON 解析辅助
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse_json_object(raw: str) -> dict:
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
        logger.warning("无法解析 JSON object: %s", raw[:200])
        return {}

    @staticmethod
    def _parse_json_array(raw: str) -> list[dict]:
        raw = raw.strip()
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r'\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\]]*\])*\])*\]', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("无法解析 JSON array: %s", raw[:200])
        return []
