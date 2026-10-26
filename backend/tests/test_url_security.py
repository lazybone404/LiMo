"""验证 URL 安全校验 — SSRF 防护"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import socket as _socket
from unittest.mock import patch

from app.utils.url_security import validate_url, is_private_ip

# ⚠️ **本文件必须零网络依赖。** `validate_url` 对域名要逐条解析并校验（SSRF 的必需
# 行为），故「公网 URL 应通过 / localhost 应阻止」天然要问 DNS。曾经是**收集期真解析**：
# 2026-10-27 本机 DNS 把 api.openai.com 污染到 `2001::6ca0:aa2c`（Teredo，`ipaddress`
# 判 is_private），于是 `pytest tests/` 在**收集阶段**就 `Interrupted` —— 一个与仓库
# 代码无关的网络状况把整个门禁打成红，且报错位置（collection）指向不了真因。
# mock 之后判据一字不变（解析到公网 IP → 通过；解析到环回 → 阻止），换台机器结果相同。
# ⚠️ 只 mock 解析结果，不 mock `is_private_ip` —— 后者是本文件下半段要直接验证的。
_PUBLIC_DNS = [(_socket.AF_INET, _socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]
_LOOPBACK_DNS = [(_socket.AF_INET, _socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]

# ── 合法 URL 应通过 ──
with patch('app.utils.url_security.socket.getaddrinfo', return_value=_PUBLIC_DNS):
    for url in [
        'https://www.example.com',
        'https://api.openai.com/v1/chat',
        'http://github.com/user/repo',
        'https://arxiv.org/pdf/2301.00001.pdf',
    ]:
        try:
            validate_url(url)
            print(f'PASS: {url}')
        except ValueError as e:
            print(f'FAIL: {url} → {e}')
            raise

# ── 内网/保留地址应阻止 ──
# 只有 `localhost` 走 DNS，其余都是 IP 字面量（直接判，不经解析）。整组统一给环回，
# 让「域名解析到内网」这条路径也被稳定行使到。
with patch('app.utils.url_security.socket.getaddrinfo', return_value=_LOOPBACK_DNS):
    for url in [
        'http://127.0.0.1:8080/admin',
        'http://localhost/test',
        'http://10.0.0.1/api',
        'http://192.168.1.1/',
        'http://172.16.0.5/',
        'http://169.254.169.254/latest/meta-data/',
        'http://0.0.0.0/',
    ]:
        try:
            validate_url(url)
            print(f'SECURITY FAIL: {url} should be blocked!')
            raise AssertionError(f'Should have blocked {url}')
        except ValueError:
            print(f'BLOCKED: {url}')

# ── 错误协议应阻止 ──
for url in ['ftp://evil.com/file', 'file:///etc/passwd', 'gopher://localhost/']:
    try:
        validate_url(url)
        print(f'SECURITY FAIL: {url} should be blocked!')
        raise AssertionError(f'Should have blocked {url}')
    except ValueError:
        print(f'BLOCKED: {url}')

# ── IP 检查 ──
assert is_private_ip('127.0.0.1')
assert is_private_ip('10.0.0.1')
assert is_private_ip('192.168.1.1')
assert not is_private_ip('8.8.8.8')
assert not is_private_ip('1.1.1.1')
print('PASS: IP checks')

print('\nAll URL security tests PASS')
