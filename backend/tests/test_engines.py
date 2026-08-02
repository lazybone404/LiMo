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

# 引擎注册验证（无降级链：绑定只取第一个，失败直接报错）
asr_bcut = EngineRegistry.get("asr", "bcut-asr")
print(f'bcut-asr registered: {asr_bcut is not None}')
print(f'bcut-asr fallback: {asr_bcut.fallback.name if asr_bcut and asr_bcut.fallback else "None"}')

doc_md = EngineRegistry.get("doc", "markitdown")
print(f'markitdown fallback: {doc_md.fallback.name if doc_md and doc_md.fallback else "None"}')

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
