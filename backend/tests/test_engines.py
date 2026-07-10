"""验证引擎注册 + 降级链"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.registry import EngineRegistry
from app.engines import init_engines

init_engines()

# 检查注册
for cat in ["asr", "doc"]:
    engines = EngineRegistry.list_category(cat)
    print(f'{cat}: {engines}')

# 降级链
asr_small = EngineRegistry.get("asr", "faster-whisper-small")
asr_medium = EngineRegistry.get("asr", "faster-whisper-medium")
print(f'asr-medium fallback: {asr_medium.fallback.name if asr_medium.fallback else "None"}')
print(f'asr-small fallback: {asr_small.fallback.name if asr_small.fallback else "None"}')

doc_md = EngineRegistry.get("doc", "markitdown")
doc_pdf = EngineRegistry.get("doc", "pymupdf")
print(f'markitdown fallback: {doc_md.fallback.name if doc_md.fallback else "None"}')

# 降级链触发测试（用不存在的文件）
import tempfile
tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
tf.write(b"%PDF-1.4 garbage not real pdf")
tf.close()

try:
    result = doc_md.run(tf.name)
    print(f'MarkItDown failed, fell back: {result.engine}')
except Exception as e:
    print(f'Both engines failed: {e}')
finally:
    os.unlink(tf.name)

print('Engine tests done')
