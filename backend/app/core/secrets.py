"""
DPAPI 凭据保护 —— Windows 数据保护 API 的零依赖封装（ctypes 直调 crypt32.dll）。

**定位：卫生，不是安全边界。** DPAPI 以「当前登录用户」为边界 —— 同一账户下的任何
进程都能解开它（《密钥与凭据存储约定》§5）。它挡的是「配置文件被复制走 / 同步进网盘 /
被打进反馈附件」这类**被动泄漏**，挡不住同账户的主动恶意进程。**勿当安全边界宣传。**

零新依赖：`pyproject.toml` 里没有 keyring / pywin32（CHANGELOG 记「已移除 pywin32」），
故此处直接 ctypes 调 `crypt32.dll`。

非 Windows 平台**显式抛 `SecretsUnavailable`**，不静默降级为明文 —— 见 CLAUDE.md
「失败显式化：No silent degradation」。

落盘位置：`%LOCALAPPDATA%\\LiMo\\secrets.bin`（可用环境变量 `LIMO_SECRETS` 覆盖，测试用）。
**刻意不放在 `~/.limo/` 内** —— 那里是用户可见的数据目录（会被同步、备份、整个打包
分享给别人），凭据与业务数据分开存放以缩小误传播面。
"""

import ctypes
import json
import os
import sys
from pathlib import Path

#: 写进 config.yaml 的引用值 —— 真实凭据在别处（DPAPI 加密文件），YAML 里只留这个标记。
SECRETS_REF = "dpapi:limo"

#: 附加熵。⚠️ **不是安全边界** —— 它是源码里的公开常量，只是让密文与「本应用 + 本版本」
#: 绑定，防止同账户下别的程序「顺手」解开。改它 = 旧密文全部解不开（需重写一次）。
_ENTROPY = b"LiMo.secrets.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01

_IS_WINDOWS = sys.platform == "win32"
_DPAPI_READY = False
_DPAPI_REASON = f"DPAPI 仅 Windows 可用（当前平台 {sys.platform}）"


class SecretsUnavailable(RuntimeError):
    """DPAPI 不可用：非 Windows 平台，或 crypt32 调用失败。"""


if _IS_WINDOWS:
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    try:
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        _crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        _crypt32.CryptProtectData.restype = wintypes.BOOL
        _crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        _crypt32.CryptUnprotectData.restype = wintypes.BOOL
        _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        _kernel32.LocalFree.restype = ctypes.c_void_p

        _DPAPI_READY = True
    except OSError as e:  # pragma: no cover - Windows 上 crypt32 必然存在
        _DPAPI_REASON = f"crypt32/kernel32 加载失败：{e}"

    def _blob(data: bytes):
        """bytes → (DATA_BLOB, 缓冲区)。

        ⚠️ **必须把缓冲区一起返回**：只留 DATA_BLOB、让 `create_string_buffer` 的对象被
        GC 回收，`pbData` 就指向已释放内存 —— 典型的「跑起来大多正常、偶尔解出垃圾」缺陷，
        失败模式随机，最难查。
        """
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def is_available() -> bool:
    """DPAPI 是否可用。**不做往返测试** —— 那要真调一次加密，无必要。"""
    return _DPAPI_READY


def unavailable_reason() -> str:
    return _DPAPI_REASON


def protect(plaintext: str) -> bytes:
    """明文 → DPAPI 密文（当前用户 + 本应用熵绑定）。失败抛 `SecretsUnavailable`。"""
    if not _DPAPI_READY:
        raise SecretsUnavailable(_DPAPI_REASON)
    in_blob, _keep_in = _blob(plaintext.encode("utf-8"))
    entropy_blob, _keep_ent = _blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob), "LiMo secrets", ctypes.byref(entropy_blob),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretsUnavailable(f"CryptProtectData 失败（WinError {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def unprotect(ciphertext: bytes) -> str:
    """DPAPI 密文 → 明文。失败抛 `SecretsUnavailable`（含换账户 / entropy 变过）。"""
    if not _DPAPI_READY:
        raise SecretsUnavailable(_DPAPI_REASON)
    if not ciphertext:
        return ""
    in_blob, _keep_in = _blob(ciphertext)
    entropy_blob, _keep_ent = _blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, ctypes.byref(entropy_blob),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretsUnavailable(f"CryptUnprotectData 失败（WinError {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def secrets_path() -> Path:
    """密钥库落点。`LIMO_SECRETS` 可覆盖（测试用，与 `LIMO_CONFIG` 同款约定）。"""
    override = os.environ.get("LIMO_SECRETS")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LiMo" / "secrets.bin"


def load_secrets() -> dict:
    """读并解密密钥库。

    文件不存在 → 空 dict（**不是错误**：新用户，或尚未迁移）。
    文件在但解不开（换账户 / entropy 改过 / 文件损坏）→ **抛错**：这是真错误，
    静默返回空 dict 会让用户的 Key 无声消失（失败显式化）。
    """
    path = secrets_path()
    if not path.exists():
        return {}
    raw = path.read_bytes()
    try:
        data = json.loads(unprotect(raw))
    except json.JSONDecodeError as e:
        raise SecretsUnavailable(f"密钥库解密后不是合法 JSON（{path}）：{e}") from e
    if not isinstance(data, dict):
        raise SecretsUnavailable(f"密钥库结构异常（期望 dict，实为 {type(data).__name__}）")
    return data


def save_secrets(data: dict) -> None:
    """加密并原子写入。

    ⚠️ **空 dict 删除文件**，不留下一个「能解出 `{}`」的密文 —— 否则 `load_secrets`
    会认为「已迁移、只是没配 key」，而真相是「根本没有凭据」，两者在自检里该报不同的话。
    """
    path = secrets_path()
    if not data:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    blob = protect(json.dumps(data, ensure_ascii=False, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".bin.tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def selftest() -> tuple[bool, str]:
    """往返自检：加密一段哨兵串再解开比对。供启动自检与测试共用。

    返回 `(ok, 说明)`，**不抛异常** —— 调用方（启动自检）要的是「能不能用」这个判断。
    """
    if not _DPAPI_READY:
        return False, _DPAPI_REASON
    sentinel = "limo-selftest-0x5f3a"
    try:
        if unprotect(protect(sentinel)) != sentinel:
            return False, "DPAPI 往返结果不一致"
    except SecretsUnavailable as e:
        return False, str(e)
    return True, "ok"
