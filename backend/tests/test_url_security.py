"""验证 URL 安全校验 — SSRF 防护"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.utils.url_security import validate_url, is_private_ip

# ── 合法 URL 应通过 ──
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
