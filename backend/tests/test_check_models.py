"""模型预设核对脚本（scripts/check-models.py）的单测。

运行：uv run python tests/test_check_models.py

覆盖：PROVIDER_MAP 完整性守卫 / 墓碑剔除 / since 截断 / B 类跳过聚合网关 /
      C 类同值不误报 / D 类只在字段有值时才收

⚠️ 每个用例都配了**对照实验**——同一份数据下把参数去掉，该条**确实会出现**，
   以此证明判据有区分力，而不是「测试通过」与「测试根本没在检查」无法区分
   （见 key-patterns #38/#39）。

⚠️ 本脚本是**开发期工具**，不进打包、不被后端 import；此处的守卫防的是
   「新增了 provider / 下架了模型，却忘了同步这张核对表」——那类失误的症状
   是**报告照常输出、只是从不提及那一整个厂商**，看起来像「那家没有变更」。
"""
import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import DEFAULT_CONFIG, RETIRED_MODEL_IDS

# 脚本文件名带连字符，不能走 import 语句 → 用 importlib 从路径加载
_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check-models.py"
_spec = importlib.util.spec_from_file_location("check_models", _SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


# ── 测试用数据（刻意自成一份，不依赖线上数据源）──

OUR = {
    "dashscope": [
        {"id": "qwen-turbo", "context_window": 131072},
        {"id": "qwen3.8-max", "context_window": 131072},
        {"id": "gone-model", "context_window": 32000, "retired": ""},
    ],
    "opencode": [{"id": "gpt-5", "context_window": 128000}],
}

SOURCE = {
    "alibaba": {"models": {
        "qwen-turbo": {"limit": {"context": 1000000, "output": 8192},
                       "cost": {"input": 0.05, "output": 0.2},
                       "release_date": "2025-01-01"},
        "qwen3.8-max": {"limit": {"context": 131072}, "release_date": "2026-09-01"},
        "qwen9-ultra": {"limit": {"context": 500000}, "release_date": "2026-09-30"},
        "qwen1-old": {"limit": {"context": 8000}, "release_date": "2024-01-01"},
        "qwen-no-date": {"limit": {"context": 999}, "release_date": ""},
    }},
}


# ── 守卫一：PROVIDER_MAP 必须覆盖全部预设 provider ──

def test_every_preset_provider_is_mapped():
    presets = DEFAULT_CONFIG["llm_models"]
    missing = check.unmapped_providers(presets)
    assert missing == set(), (
        f"这些 provider 未在 PROVIDER_MAP 中映射，核对时会**静默跳过**它们：{sorted(missing)}\n"
        f"新增 provider 后请补映射；确认无上游目录则加进 NO_UPSTREAM。")
    print(f"✅ PROVIDER_MAP 覆盖全部 {len(presets)} 个预设 provider")


def test_unmapped_guard_has_discriminating_power():
    """对照实验：造一个含未映射 provider 的预设，守卫必须报出来。"""
    fake = {"brand-new-vendor": [{"id": "x", "context_window": 1000}]}
    assert check.unmapped_providers(fake) == {"brand-new-vendor"}
    # 反向：确认 NO_UPSTREAM 里的真的被放行（否则守卫会永远亮红灯）
    assert check.unmapped_providers({"opencode": []}) == set()
    print("✅ 守卫自证：未映射的报得出、聚合网关（NO_UPSTREAM）放行")


# ── 守卫二：墓碑必须指向「核对得动」的 provider ──

def test_graveyard_is_usable():
    graveyard = check.retired_ids()
    assert graveyard, "retired_ids() 返回空集——接线断了，A 类会重新冒出已知噪声"
    assert ("deepseek", "deepseek-v4-flash") in graveyard
    assert check.orphan_graveyard(graveyard) == set(), (
        f"这些墓碑指向未映射的 provider，永远不会生效："
        f"{sorted(check.orphan_graveyard(graveyard))}")
    # 与 config 的表同源（不另起一套判断）
    assert graveyard == set(RETIRED_MODEL_IDS)
    print(f"✅ 墓碑表可用：{len(graveyard)} 条，全部指向可核对的 provider")


def test_orphan_graveyard_has_discriminating_power():
    """对照实验：指向未映射 provider 的墓碑必须被认出来。"""
    assert check.orphan_graveyard({("opencode", "some-model")}) == {("opencode", "some-model")}
    assert check.orphan_graveyard({("dashscope", "qwen-turbo")}) == set()
    print("✅ 墓碑守卫自证：orphan 抓得到、正常墓碑放行")


# ── A 类：新模型 ──

def test_find_new_filters_and_sorts():
    got = check.find_new(OUR, SOURCE)
    ids = [r["id"] for r in got]
    # qwen-turbo / qwen3.8-max 我们已有 → 不报；其余 3 条报出，按发布日期降序
    # （无日期的那条排最末——空串在降序里垫底，不是「最旧」而是「未知」）
    assert ids == ["qwen9-ultra", "qwen1-old", "qwen-no-date"], ids
    print("✅ A 类：已有条目不报、按发布日期降序")


def test_find_new_respects_provider_subset():
    """A 类必须跟着 `our` 的 provider 集合走。

    实证 bug：原实现遍历**全局** PROVIDER_MAP，于是 `--provider dashscope` 的报告里
    混进了 openai / zhipu 的新模型——过滤只作用于 B/C 类，A 类看起来完全正常。
    对照实验：同一份数据，全集下两家都出、子集下只出被点名的那个。
    """
    src = {
        "alibaba": {"models": {"a": {"release_date": "2026-09-02"}}},
        "openai": {"models": {"b": {"release_date": "2026-09-01"}}},
    }
    both = check.find_new({"dashscope": [], "openai": []}, src)
    assert [r["id"] for r in both] == ["a", "b"], "全集下两家都该出现"

    only = check.find_new({"dashscope": []}, src)
    assert [r["id"] for r in only] == ["a"], f"子集下不该出现别家：{only}"
    print("✅ A 类：provider 过滤生效（子集下不混入别家）")


def test_find_new_since_truncates():
    """对照：同一份数据不传 since 时 qwen1-old 会出现（见上一用例）。"""
    got = check.find_new(OUR, SOURCE, since=date(2026, 1, 1))
    assert [r["id"] for r in got] == ["qwen9-ultra", "qwen-no-date"]
    print("✅ A 类：since 生效（2024 年的老模型被截断）")


def test_find_new_drops_graveyard():
    """对照：不传 graveyard 时 qwen9-ultra 会出现（见 test_find_new_filters_and_sorts）。"""
    got = check.find_new(OUR, SOURCE, graveyard={("dashscope", "qwen9-ultra")})
    assert "qwen9-ultra" not in [r["id"] for r in got]
    print("✅ A 类：墓碑被剔除")


# ── B 类：可能已下架 ──

def test_find_missing_skips_aggregators():
    got = check.find_missing(OUR, SOURCE)
    ids = [r["id"] for r in got]
    assert ids == ["gone-model"], ids          # opencode 被跳过，其余都有记录
    assert "gpt-5" not in ids, "聚合网关无上游目录，查无属正常，不该报"
    print("✅ B 类：只报真·查无，聚合网关跳过")


# ── C 类：数值不一致 ──

def test_find_mismatch_only_on_difference():
    got = check.find_mismatch(OUR, SOURCE)
    rows = {r["id"]: r for r in got}
    assert set(rows) == {"qwen-turbo"}, set(rows)   # qwen3.8-max 同值 → 不报
    assert rows["qwen-turbo"]["ours"] == 131072 and rows["qwen-turbo"]["theirs"] == 1000000
    print("✅ C 类：同值不误报、差异报出且带双方真值")


def test_find_mismatch_ignores_missing_limit():
    """源里没有 limit.context 时不得误报（否则每条都会变成「不一致」）。"""
    our = {"dashscope": [{"id": "x", "context_window": 1000}]}
    src = {"alibaba": {"models": {"x": {"limit": {}}}}}
    assert check.find_mismatch(our, src) == []
    print("✅ C 类：源缺字段时不误报")


# ── source_entry ──

def test_source_entry_unknown_provider_is_none():
    assert check.source_entry(SOURCE, "opencode", "gpt-5") is None
    assert check.source_entry(SOURCE, "dashscope", "qwen9-ultra")["limit"]["context"] == 500000
    assert check.source_entry(SOURCE, "dashscope", "nope") is None
    print("✅ source_entry：未映射 / 不存在均返回 None")


# ── D 类：候选字段 ──

def test_collect_candidates_only_when_present():
    cand = check.collect_candidates(OUR, SOURCE)
    assert [r["id"] for r in cand["max_tokens"]] == ["qwen-turbo"]   # 只有它标了 output
    assert [r["id"] for r in cand["price"]] == ["qwen-turbo"]
    assert cand["price"][0]["in"] == 0.05
    print("✅ D 类：只在字段真有值时才收")


# ── 报告冒烟：render 不崩，且把墓碑剔除写进说明 ──

def test_render_smoke():
    text = check.render(OUR, SOURCE, "单测数据", None)
    assert "已剔除" in text and "RETIRED_MODEL_IDS" in text
    assert "A. 可能有新模型" in text and "D. 预设缺失的字段" in text
    print("✅ render：四类段落齐全、墓碑说明在列")


if __name__ == "__main__":
    test_every_preset_provider_is_mapped()
    test_unmapped_guard_has_discriminating_power()
    test_graveyard_is_usable()
    test_orphan_graveyard_has_discriminating_power()
    test_find_new_filters_and_sorts()
    test_find_new_respects_provider_subset()
    test_find_new_since_truncates()
    test_find_new_drops_graveyard()
    test_find_missing_skips_aggregators()
    test_find_mismatch_only_on_difference()
    test_find_mismatch_ignores_missing_limit()
    test_source_entry_unknown_provider_is_none()
    test_collect_candidates_only_when_present()
    test_render_smoke()
    print("\n🎉 全部通过")
