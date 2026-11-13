"""生成用量与费用：从网关的 usage chunk 一路记到产物自证头，再读回界面。

为什么这个文件存在
------------------
「这次生成花了多少」在 0.5.26 之前**完全没有记录**：产物自证头里有模型、提示词
版本、时间，唯独没有 token 数。用户想知道一篇长文烧了多少钱，只能自己去平台报表
里按时间对——而报表是按账号汇总的，两路并发（notes + polished）时连哪一路贵都分不出来。

四个落点各有一条失效形态，且**都不报错**：

1. `llm/service.py` —— usage chunk 的 `choices` 是空数组。若先 `if not chunk.choices:
   continue` 再取 usage，用量永远是零，而流式照常工作、界面照常出字。
2. `generation/generator.py` / `routers/generate.py` —— 两路并发共用一个累加器，
   A 的用量记到 B 头上。两边都「有数字」，比没有更难发现。
3. `generation/provenance.py` —— 空 dict 写成 `usage: {}`，读方以为「问了、是零」。
4. `storage/store.py` —— 读回时从 index 拿而不是从文件头算，index 一删就丢。

判据不是「有 usage 这个键」，而是成对的三条：**回调拿到了正确的数量级 → 头里写的是
同一份数 → 读回之后仍是同一份数**，外加一组「拿不到时必须是空字典、必须是缺键」的
反向断言（否则「永远写 0」这种实现也能全绿）。

对照实验（每条判据配一个「看起来对、其实错」的实现，均已实测判红）：
- 把 usage 取值挪到 `if not chunk.choices: continue` 之后 → 第 4 节红（回调一次都不触发）
- 把 `usage_record` 的「两个单价都齐才算钱」改成「任一存在就算」→ 第 1 节红
- 把 `artifact_meta` 的 `if usage:` 改成 `generated["usage"] = dict(usage or {})` → 第 2 节红
- 把 store 的读回改成取 index 条目上的 `usage` → 第 3 节红（index 里根本没有这个键）
"""
import asyncio
import os
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.pricing import PRICE_UNIT, prices_for, usage_record   # noqa: E402
from app.core.transcript_meta import parse_artifact_meta            # noqa: E402
from app.core.types import (GenerateMode, MarkdownView, RawMetadata,   # noqa: E402
                            RawTranscript, SourceType, Style)
from app.generation.generator import Generator                      # noqa: E402
from app.generation.provenance import artifact_meta, markdown_meta  # noqa: E402
from app.llm.service import LLMService                              # noqa: E402
from app.storage.store import Store                                 # noqa: E402

CFG = {
    "enabled_models": [
        # 价格单位：元 / 百万 token（与 settings 里用户填的口径一致）
        {"id": 1, "provider_id": "dashscope", "model_name": "qwen-max",
         "price_in": 2.4, "price_out": 9.6},
        # 只填了一半：不能据此算钱
        {"id": 2, "provider_id": "deepseek", "model_name": "deepseek-v4-flash",
         "price_in": 0.5},
        # 一分钱都不填：同样是「不知道」
        {"id": 3, "provider_id": "local", "model_name": "ollama-qwen",
         "capabilities": ["text"]},
    ],
}

print('=' * 60)
print('1. usage_record：数量、单位、以及「两个单价都齐才算钱」')
print('=' * 60)

# ── 单价查表：按 (provider, model) 精确匹配，不是只按 model 名 ──
assert prices_for(CFG, "dashscope", "qwen-max") == (2.4, 9.6)
assert prices_for(CFG, "dashscope", "nope") == (None, None), \
    '未知模型必须返回 (None, None)，而不是 (0, 0)——0 会被当成「免费」'
assert prices_for(None, "dashscope", "qwen-max") == (None, None), '无配置时不该崩'
# 同名模型挂在别的 provider 上，不能借用别人的价（曾有过按 model 名单独匹配的写法）
assert prices_for(CFG, "other-vendor", "qwen-max") == (None, None), \
    'provider 不匹配时不得回落到同名模型的价格'
print('PASS: 单价按 (provider, model) 精确匹配；未知一律 (None, None)')

# ── 金额换算：元 / 百万 token ──
rec = usage_record(1_000_000, 0, config=CFG, provider="dashscope", model="qwen-max")
assert rec["input"] == 1_000_000 and rec["output"] == 0
assert rec["cost"] == 2.4, f'100 万输入应恰好 2.4 元，实际 {rec["cost"]}'

rec = usage_record(0, 1_000_000, config=CFG, provider="dashscope", model="qwen-max")
assert rec["cost"] == 9.6, f'100 万输出应恰好 9.6 元，实际 {rec["cost"]}'

# 真实量级：约 1.2 万输入 + 3 千输出
rec = usage_record(12_345, 3_210, config=CFG, provider="dashscope", model="qwen-max")
assert rec == {"input": 12345, "output": 3210,
               "cost": round((12345 * 2.4 + 3210 * 9.6) / PRICE_UNIT, 6)}, rec
assert rec["cost"] > 0, '真实量级的费用必须大于零（精度不够会把它舍成 0）'
print(f'PASS: 金额按「元 / 百万 token」折算，12345/3210 → {rec["cost"]} 元')

# ── 反向：单价缺一半或全缺 → **不给 cost 键**，绝不用 0 冒充「不花钱」 ──
half = usage_record(100, 100, config=CFG, provider="deepseek",
                    model="deepseek-v4-flash")
assert "cost" not in half, f'只填了输入单价时不得算钱，实际 {half}'
none_priced = usage_record(100, 100, config=CFG, provider="local", model="ollama-qwen")
assert "cost" not in none_priced, f'未填价格时不得算钱，实际 {none_priced}'
assert none_priced == {"input": 100, "output": 100}, 'token 数本身仍要记'
print('PASS: 单价缺任一项 → 只记 token、不记金额（0 元是假话）')

# ── 反向：完全拿不到用量 → 空字典（调用方据此不写这个键） ──
assert usage_record(None, None) == {}, '两个都拿不到时必须是空 dict'
assert usage_record(0, 0) == {}, '整数字零与「拿不到」同义——不写零'
assert usage_record(None, 42) == {"input": 0, "output": 42}, '只拿到一半时照实写'
print('PASS: 拿不到用量 → 空 dict；不会写成 {"input": 0, "output": 0}')

print()
print('=' * 60)
print('2. 自证头写入侧：有才写键，空 dict 不写')
print('=' * 60)

base = artifact_meta("notes", doc_id="tr_u1", mode="notes", model="qwen-max")
assert "usage" not in base["generated"], '不传 usage 时不得凭空出现这个键'
assert "usage" not in artifact_meta("notes", doc_id="tr_u1", usage={})["generated"], \
    '空 dict 必须等价于「没传」——写 {} 会让读方以为「问了、是零」'
assert "usage" not in artifact_meta("notes", doc_id="tr_u1", usage=None)["generated"]

with_usage = artifact_meta("notes", doc_id="tr_u1", model="qwen-max",
                           usage={"input": 12, "output": 3, "cost": 0.0001})
assert with_usage["generated"]["usage"] == {"input": 12, "output": 3, "cost": 0.0001}
assert with_usage["generated"]["model"] == "qwen-max", 'usage 不得挤掉 model'
print('PASS: artifact_meta 有才写 usage，空/None 不写键')

# markdown_meta 是取值口径的唯一落点：它必须把 mv.usage 带过去
tx = {"sourceType": "webpage", "sourceUrl": "https://example.com/a"}
mv = MarkdownView(id="md_u1", transcript_id="tr_u1", content="正文",
                  style=Style.DETAILED, mode=GenerateMode.NOTES,
                  generation_model="qwen-max",
                  usage={"input": 999, "output": 111})
m = markdown_meta(mv, tx)
assert m["generated"]["usage"] == {"input": 999, "output": 111}, \
    f'markdown_meta 没把 mv.usage 带进头里：{m["generated"]}'
mv_empty = MarkdownView(id="md_u2", transcript_id="tr_u1", content="正文",
                        style=Style.DETAILED, mode=GenerateMode.NOTES)
assert "usage" not in markdown_meta(mv_empty, tx)["generated"], \
    'MarkdownView.usage 缺省（空 dict）时头里不得有 usage 键'
print('PASS: markdown_meta 透传 mv.usage；缺省时不写键')

print()
print('=' * 60)
print('3. 读回侧：store 从文件头现算，不改 index')
print('=' * 60)

import tempfile   # noqa: E402

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


def _import(st: Store, tx_id: str) -> None:
    st.save_transcript(RawTranscript(
        id=tx_id, title="示例", source_type=SourceType.WEBPAGE, engine="webpage",
        raw_text="x" * 40, content_hash=f"hash_{tx_id}",
        metadata=RawMetadata(), tags=[], source_url="https://example.com/a",
    ))


_st = _new_store()
_import(_st, "tr_ur1")
_mv = MarkdownView(id="md_ur1", transcript_id="tr_ur1", content="# 标题\n\n正文。\n",
                   style=Style.DETAILED, mode=GenerateMode.NOTES,
                   generation_model="qwen-max",
                   usage={"input": 12345, "output": 3210, "cost": 0.060482})
assert _st.save_markdown(_mv, file_meta=markdown_meta(_mv, _st.get_transcript("tr_ur1")))

# 文件头是真的：绕过 Store 直接读磁盘首行
_doc_dir = _st.get_doc_dir("tr_ur1")
_head_text = pathlib.Path(_doc_dir, "notes", "v1.md").read_text(encoding="utf-8")
_head = parse_artifact_meta(_head_text)[0]
assert _head["generated"]["usage"] == {"input": 12345, "output": 3210,
                                       "cost": 0.060482}, _head["generated"]
print('PASS: usage 真的写进了 v1.md 的首行自证头')

# index 是缓存，不是真相源：它里头不该有 usage（否则又是一份会漂移的副本）
_idx_entry = _st._index["markdowns"]["tr_ur1"][0]
assert "usage" not in _idx_entry, \
    f'usage 不该进 index——那是第二真相源，会与文件头静默分叉：{_idx_entry}'

back = _st.get_markdowns_by_transcript("tr_ur1", mode="notes")[0]
assert back["usage"] == {"input": 12345, "output": 3210, "cost": 0.060482}, \
    f'读回值与写入值不一致：{back.get("usage")}'
print('PASS: get_markdowns_by_transcript 从文件头读回同一份 usage；index 里没有它')

# ── 反向：没有 usage 的历史产物 → 读回时**缺键**，而不是 {} ──
_import(_st, "tr_ur2")
_mv2 = MarkdownView(id="md_ur2", transcript_id="tr_ur2", content="# 老产物\n\n正文。\n",
                    style=Style.DETAILED, mode=GenerateMode.NOTES,
                    generation_model="qwen-max")
assert _st.save_markdown(_mv2, file_meta=markdown_meta(_mv2, _st.get_transcript("tr_ur2")))
_back2 = _st.get_markdowns_by_transcript("tr_ur2", mode="notes")[0]
assert "usage" not in _back2, \
    f'历史产物读回必须缺键（空 dict 会让界面显示「问过、是零」）：{_back2.get("usage")}'
print('PASS: 无 usage 的产物读回时缺键（前端据此不显示芯片）')

print()
print('=' * 60)
print('4. LLMService.stream：usage chunk（choices 为空）也得接住')
print('=' * 60)


def _chunk(content=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)] if content or finish else [],
        usage=usage,
    )


class _FakeCompletions:
    """按调用次序回放预设流；`reject_first` 模拟「网关不认 stream_options」。"""

    def __init__(self, streams, reject_first=False):
        self._streams = list(streams)
        self._reject_first = reject_first
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._reject_first and len(self.calls) == 1:
            raise RuntimeError("Unsupported parameter: stream_options")
        chunks = self._streams.pop(0)

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


class _FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


def _svc(completions) -> LLMService:
    svc = LLMService({"llm": {"provider": "dashscope", "api_key": "sk-test",
                              "model": {"dashscope": "qwen-max"}}})
    # 绕过惰性构造：`client` property 只在 _client is None 时真建连接
    svc._client = _FakeClient(completions)
    return svc


async def _drain(svc, **kw) -> str:
    return "".join([c async for c in svc.stream("PROMPT", **kw)])


# ── 单轮：正文 + 末尾一个 choices 为空的 usage chunk ──
comp = _FakeCompletions([[
    _chunk("你好"),
    _chunk("世界", finish="stop"),
    _chunk(usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=340)),
]])
seen: list[dict] = []
out = asyncio.run(_drain(_svc(comp), on_usage=seen.append))
assert out == "你好世界", f'正文不该受 usage chunk 影响：{out!r}'
assert seen == [{"prompt_tokens": 1200, "completion_tokens": 340}], \
    f'usage chunk 没被接住（大概率是被 `if not chunk.choices: continue` 吞了）：{seen}'
assert comp.calls[0].get("stream_options") == {"include_usage": True}, \
    '传了 on_usage 就必须向服务端要 usage'
print('PASS: choices 为空的 usage chunk 被累加，回调收到 (1200, 340)')

# ── 不传 on_usage：请求形状逐字节不变（不冒险发少数网关不认的字段） ──
comp = _FakeCompletions([[_chunk("正文", finish="stop")]])
out = asyncio.run(_drain(_svc(comp)))
assert "stream_options" not in comp.calls[0], \
    '没要用量时不得发送 stream_options——那会让部分兼容网关直接 4xx'
assert out == "正文"
print('PASS: 不传 on_usage → 请求里没有 stream_options，行为零变更')

# ── 两轮续写：回调两次，第二次是累计值（调用方 update 即得总和） ──
comp = _FakeCompletions([
    [_chunk("前半", finish="length"),
     _chunk(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500))],
    [_chunk("后半", finish="stop"),
     _chunk(usage=SimpleNamespace(prompt_tokens=1300, completion_tokens=200))],
])
seen = []
out = asyncio.run(_drain(_svc(comp), on_usage=seen.append))
assert out == "前半后半", out
assert len(seen) == 2, f'每轮结束应各回调一次，实际 {len(seen)} 次：{seen}'
assert seen[0] == {"prompt_tokens": 1000, "completion_tokens": 500}
assert seen[-1] == {"prompt_tokens": 2300, "completion_tokens": 700}, \
    f'续写轮必须是累计值（否则调用方 update 只拿到最后一轮）：{seen[-1]}'
print(f'PASS: 多轮续写累计正确 —— 两轮 1000+1300 / 500+200 → {seen[-1]}')

# ── 网关拒收 stream_options：去掉后重试一次，生成不能因此失败 ──
comp = _FakeCompletions([[_chunk("正文", finish="stop"),
                          _chunk(usage=SimpleNamespace(prompt_tokens=10,
                                                       completion_tokens=2))]],
                        reject_first=True)
seen = []
out = asyncio.run(_drain(_svc(comp), on_usage=seen.append))
assert out == "正文", f'去掉 stream_options 之后必须能正常生成：{out!r}'
assert len(comp.calls) == 2, f'应恰好重试一次，实际 {len(comp.calls)} 次'
assert comp.calls[0].get("stream_options") == {"include_usage": True}
assert "stream_options" not in comp.calls[1], '重试那次必须去掉该字段'
assert seen and seen[-1] == {"prompt_tokens": 10, "completion_tokens": 2}
print('PASS: 网关拒收 stream_options → 去掉重试，正文与后续用量都不受影响')

# ── 服务端压根不回 usage：一次都不回调（「没问到」不等于「问了是零」） ──
comp = _FakeCompletions([[_chunk("正文", finish="stop")]])
seen = []
asyncio.run(_drain(_svc(comp), on_usage=seen.append))
assert seen == [], f'服务端没给 usage 时不得回调任何数：{seen}'
print('PASS: 无 usage chunk → 不回调（上层据此不写键）')

print()
print('=' * 60)
print('5. Generator._usage_of：token 数 → 自证头字段（含金额与诚实缺省）')
print('=' * 60)


class _StubLLM:
    provider = "dashscope"
    model = "qwen-max"


gen = Generator(_StubLLM(), None, None, CFG)
assert gen._usage_of({"prompt_tokens": 1_000_000, "completion_tokens": 0}) == \
    {"input": 1_000_000, "output": 0, "cost": 2.4}
assert gen._usage_of({}) == {}, '空累加器（拿不到用量）→ 空 dict'
# 价格查不到时仍然记 token
gen2 = Generator(_StubLLM(), None, None, {})
assert gen2._usage_of({"prompt_tokens": 5, "completion_tokens": 6}) == \
    {"input": 5, "output": 6}, '无配置时只记 token，不编造金额'
print('PASS: Generator._usage_of 换算正确，缺价格时只记 token')


async def _main() -> None:
    pass


if __name__ == "__main__":
    asyncio.run(_main())
    print('\nAll usage-record tests PASS')
