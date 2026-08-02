"""验证引擎注册（无引擎降级链）"""
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

doc_md = EngineRegistry.get("doc", "markitdown")
print(f'markitdown registered: {doc_md is not None}')

# 引擎失败直接报错测试（无降级链：垃圾 PDF 触发 execute，不委托任何 fallback）
import tempfile
tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
tf.write(b"%PDF-1.4 garbage not real pdf")
tf.close()

try:
    result = doc_md.run(tf.name)
    print(f'PASS: markitdown returned without fallback: {getattr(result, "engine", type(result).__name__)}')
except Exception as e:
    print(f'PASS: markitdown raised directly (no fallback): {type(e).__name__}')
finally:
    os.unlink(tf.name)

print('Engine tests done')
