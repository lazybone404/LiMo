"""
配置持久化 — 统一的 config.yaml 读写
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def save_config(cfg: dict) -> None:
    """写回 YAML 文件（原子写入：先写临时文件再替换，防止中断损坏）

    防御性校验：拒绝写入明显损坏的配置（关键字段为 None/空）。
    """
    import yaml

    from pathlib import Path
    config_path = (
        os.environ.get("LIMO_CONFIG") or
        os.environ.get("MYBIGWORK_CONFIG") or
        str(Path(cfg.get("data_dir", str(Path.home() / ".limo"))) / "config.yaml")
    )

    # ── 防御性校验：关键字段不得被意外清空 ──
    llm = cfg.get("llm")
    if llm is None:
        logger.error("save_config 拒绝写入：cfg.llm 为 None，可能存在内存污染。跳过保存。")
        return
    if not isinstance(llm, dict):
        logger.error("save_config 拒绝写入：cfg.llm 类型异常 (%s)，跳过保存。", type(llm).__name__)
        return

    # llm_presets / engine_providers 是 list，不得为 None
    for list_key in ("llm_presets", "llm_models", "engine_providers", "engine_models"):
        if cfg.get(list_key) is None:
            logger.warning("save_config：cfg.%s 为 None，已自动修复为 []", list_key)
            cfg[list_key] = []

    # engine_bindings 是 dict，不得为 None
    if cfg.get("engine_bindings") is None:
        logger.warning("save_config：cfg.engine_bindings 为 None，已自动修复为 {}")
        cfg["engine_bindings"] = {}

    # 确保目录存在
    Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        os.replace(tmp_path, config_path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("Failed to clean up tmp config file", exc_info=True)
        raise


def mask_sensitive(value: str) -> str:
    """脱敏：API Key 中间位替换为 ***"""
    if not value:
        return ""
    if len(value) > 8:
        return value[:4] + "***" + value[-4:]
    return "***"


def is_masked(value: str) -> bool:
    """检测是否为脱敏占位值（前端未修改 key，发回了 ***）"""
    return bool(value) and "***" in str(value)


def clean_api_key(value: str) -> str:
    """清洗 API Key：去首尾空白、去 HTML 标签包裹、去引号包裹。

    防止用户从网页复制 Key 时带入了 HTML 标签或多余的引号/空格。
    """
    import re
    v = value.strip().strip("'\"")  # 去首尾空白+引号
    # 检测被 HTML 标签包裹的情况：<div>sk-xxx</div> → sk-xxx
    m = re.search(r'>([^<]+)<', v)
    if m:
        inner = m.group(1).strip().strip("'\"")
        if len(inner) > 4:
            return inner
    return v


def merge_sensitive(new_val: str, old_val: str) -> str:
    """若前端传了脱敏值（含***），保留旧值；否则用新值（自动清洗）"""
    if new_val and is_masked(new_val) and old_val:
        return old_val
    return clean_api_key(new_val)
