"""DPAPI 凭据封装（`core/secrets.py`）—— 往返保真 / 对照实验 / 文件 IO 边界。

**本文件的断言冲着「静默失败」去**，不是「跑通就算」：

| 断言 | 若实现写错会怎样 |
|---|---|
| 换 entropy / 篡改密文 → **抛错** | 解出乱码或垃圾，调用方拿它当 key 用，报错发生在别处 |
| 损坏文件 → **抛错**（而非空 dict） | 用户的 Key **无声消失**，而自检报成「尚未迁移」 |
| 空 dict → 删除文件 | 留下一个「能解出 `{}`」的密文 → 与「已迁移、只是没配 key」不可区分 |
| 密文里不含明文 | 加密形同虚设（这条是哨兵，不是密码学证明） |

⚠️ **DPAPI 仅 Windows 可用**。非 Windows 上整个文件跳过 —— 而 `core/secrets.py`
本身对非 Windows 是**显式抛 `SecretsUnavailable`**（不静默降级为明文，见 CLAUDE.md
「失败显式化」），那条行为由 `test_config_io.py` 的 `is_available()` 断言侧覆盖。

跑法：`pytest tests/test_secrets.py` 或 `python tests/test_secrets.py`。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_TMP = tempfile.mkdtemp(prefix="limo_secrets_test_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
# ⚠️ 必须在 import secrets **之前**设好路径 —— `secrets_path()` 是每次调用读 env 的，
#    技术上导入后再设也行，但测试里「先定路径再导入」是唯一不容易写错的顺序。
os.environ["LIMO_SECRETS"] = str(Path(_TMP) / "secrets.bin")

from app.core import secrets as S  # noqa: E402

pytestmark = pytest.mark.skipif(not S.is_available(), reason=S.unavailable_reason())

KIND = "sk-super-secret-0xdeadbeef"


def _raises(fn, *a, **kw) -> str | None:
    """返回错误消息；没抛错则返回 None。"""
    try:
        fn(*a, **kw)
        return None
    except S.SecretsUnavailable as e:
        return str(e)


# ── 1. 环境 ────────────────────────────────────────────────────────

def test_dpapi_available():
    ok, why = S.selftest()
    assert ok, why


# ── 2. 往返保真 ────────────────────────────────────────────────────

@pytest.mark.parametrize("sentinel", [
    "hello",
    "line1\nline2\ttab",
    "中文密钥",
    "🔐key🎈",
    "a" * 8000,
])
def test_roundtrip(sentinel):
    assert S.unprotect(S.protect(sentinel)) == sentinel


def test_ciphertext_does_not_contain_plaintext():
    assert KIND.encode() not in S.protect(KIND)


# ── 3. 对照实验：失败必须显式 ───────────────────────────────────────

def test_wrong_entropy_raises(monkeypatch):
    """换 entropy ⇒ 必须抛错，**不能**静默解出乱码。

    （解出乱码的实现会让「换过账户/entropy」表现为「Key 变成了乱码」，而报错点
    离现场十万八千里。）
    """
    blob = S.protect("x")
    monkeypatch.setattr(S, "_ENTROPY", b"LiMo.secrets.WRONG")
    assert _raises(S.unprotect, blob) is not None


def test_tampered_ciphertext_raises():
    """DPAPI 自带完整性校验 —— 篡改必须被发现，而不是解出半截数据。"""
    bad = bytearray(S.protect("tamper-me"))
    bad[len(bad) // 2] ^= 0xFF
    assert _raises(S.unprotect, bytes(bad)) is not None


def test_empty_ciphertext_is_empty_string():
    """空密文 → 空串（**不是抛错**）：它是「这个字段没值」，不是「解不开」。"""
    assert S.unprotect(b"") == ""


# ── 4. 文件 IO ─────────────────────────────────────────────────────

def test_file_roundtrip():
    payload = {
        "v": 1,
        "fields": {"llm.api_key": "sk-中文含 emoji 🔑"},
        "providers": {"deepseek": "sk-a", "dashscope": "sk-b"},
        "engine_providers": {"dashscope": "sk-c"},
    }
    S.save_secrets(payload)
    assert S.load_secrets() == payload


def test_empty_dict_deletes_file():
    """空 dict 删文件而不是写一个「能解出 `{}`」的密文 —— 否则
    `load_secrets` 无法区分「已迁移、只是没配 key」与「根本没有凭据」。"""
    S.save_secrets({"v": 1, "providers": {"x": "y"}})
    assert S.secrets_path().exists()
    S.save_secrets({})
    assert not S.secrets_path().exists()


def test_missing_file_is_empty_dict():
    S.secrets_path().unlink(missing_ok=True)
    assert S.load_secrets() == {}


def test_corrupt_file_raises():
    """损坏文件 ⇒ 抛错而非空 dict（静默返回 `{}` 会让 Key 无声消失）。"""
    S.save_secrets({"v": 1, "providers": {"x": "y"}})
    S.secrets_path().write_bytes(b"\x00\x01\x02not-a-dpapi-blob")
    assert _raises(S.load_secrets) is not None


def test_wrong_structure_raises():
    """解密成功但内容结构不对 —— 同样必须抛错。"""
    S.secrets_path().write_bytes(S.protect("[1,2,3]"))
    assert _raises(S.load_secrets) is not None
    S.secrets_path().write_bytes(S.protect("not json at all"))
    assert _raises(S.load_secrets) is not None


def test_keystore_lives_outside_data_dir():
    """密钥库**刻意不放 `~/.limo/`** —— 那是会被同步/备份/整个打包分享的数据目录。

    这条是「位置也是设计的一部分」的守卫：把它挪进数据目录不会有任何测试变红，
    但二期「缩小误传播面」的前提就没了。
    """
    home_limo = Path.home() / ".limo"
    assert home_limo not in S.secrets_path().parents


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
