"""
配置持久化 — 统一的 config.yaml 读写
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def save_config(cfg: dict) -> None:
    """写回 YAML 文件（原子写入：先写临时文件再替换，防止中断损坏）"""
    import yaml

    from pathlib import Path
    config_path = os.environ.get(
        "MYBIGWORK_CONFIG",
        str(Path.home() / ".mybigwork" / "config.yaml"),
    )
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


def merge_sensitive(new_val: str, old_val: str) -> str:
    """若前端传了脱敏值（含***），保留旧值；否则用新值"""
    if new_val and is_masked(new_val) and old_val:
        return old_val
    return new_val
