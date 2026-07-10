"""验证适配器注册 + 路由"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.types import PipelineInput, InputType
from app.core.registry import AdapterRegistry
from app.adapters import init_adapters

init_adapters()
print(f'Adapters registered: {len(AdapterRegistry.all())}')

# 路由测试
tests = [
    (PipelineInput(type=InputType.TEXT, text="hello"), "text"),
    (PipelineInput(type=InputType.URL, url="https://example.com"), "webpage"),
    (PipelineInput(type=InputType.FILE, file_path="test.pdf"), "document"),
    (PipelineInput(type=InputType.FILE, file_path="test.docx"), "document"),
    (PipelineInput(type=InputType.FILE, file_path="test.mp4"), "video"),  # 视频文件适配器
]
for inp, expected in tests:
    found = AdapterRegistry.route(inp)
    name = found.name if found else None
    status = "OK" if name == expected else f"FAIL (expected={expected}, got={name})"
    print(f'  {status}: {inp.type} {inp.url or inp.file_path or inp.text[:20]}')

print('Adapter routing tests done')
