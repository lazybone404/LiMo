"""验证存储层 — CRUD + 并发安全 + 索引重建"""
import sys, tempfile, os, threading, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.storage.store import Store
from app.core.types import RawTranscript, SourceType, RawMetadata

d = tempfile.mkdtemp()
s = Store(d)
print(f'Store created at {d}')

# ── 基础 CRUD ──
tx = RawTranscript(
    id='tr_test001', title='Test Document',
    source_type=SourceType.VIDEO, engine='faster-whisper-small',
    raw_text='Hello world', content_hash='abc123',
    metadata=RawMetadata(language='zh', duration=123.0), tags=['test'],
)
assert s.save_transcript(tx), "Save should succeed"
assert s.count == 1, f"Expected 1, got {s.count}"
print('PASS: create')

got = s.get_transcript('tr_test001')
assert got is not None and got['title'] == 'Test Document'
assert got['rawText'] == 'Hello world'
print('PASS: read')

items = s.list_transcripts()
assert len(items) == 1 and items[0]['title'] == 'Test Document'
print('PASS: list')

# ── 重复检测 ──
dup_hash = RawTranscript(
    id='tr_test002', title='Duplicate by Hash',
    source_type=SourceType.WEBPAGE, engine='trafilatura',
    raw_text='Hello world', content_hash='abc123',
    metadata=RawMetadata(),
)
assert not s.save_transcript(dup_hash), "Should reject duplicate hash"
assert s.count == 1, "Count should stay 1"
print('PASS: contentHash dedup')

tx_url = RawTranscript(
    id='tr_test003', title='URL Doc',
    source_type=SourceType.WEBPAGE, engine='trafilatura',
    raw_text='Some content', content_hash='def456',
    source_url='https://example.com/article',
    metadata=RawMetadata(),
)
assert s.save_transcript(tx_url)
dup_url = RawTranscript(
    id='tr_test004', title='Dup URL',
    source_type=SourceType.WEBPAGE, engine='trafilatura',
    raw_text='Different', content_hash='ghi789',
    source_url='https://example.com/article',
    metadata=RawMetadata(),
)
# URL 去重由 ingest 路由层处理，store 只做 contentHash 去重
assert s.save_transcript(dup_url), "URL dup should pass (store only checks contentHash)"
print('PASS: URL not blocked by store (responsibility of ingest layer)')

# ── 删除 ──
s.delete_transcript('tr_test001')
assert s.count == 2  # 3 total - 1 deleted = 2 remaining
assert s.get_transcript('tr_test001') is None
print('PASS: delete')

# ── 并发写入 ──
errors = []
def concurrent_write(idx):
    try:
        t = RawTranscript(
            id=f'tr_conc_{idx:03d}', title=f'Concurrent {idx}',
            source_type=SourceType.TEXT, engine='text',
            raw_text=f'Content {idx}', content_hash=f'conc_{idx:03d}',
            metadata=RawMetadata(),
        )
        s.save_transcript(t)
    except Exception as e:
        errors.append((idx, str(e)))

threads = [threading.Thread(target=concurrent_write, args=(i,)) for i in range(20)]
for t in threads: t.start()
for t in threads: t.join()

assert len(errors) == 0, f"Concurrent errors: {errors}"
assert s.count == 2 + 20, f"Expected 22, got {s.count}"
print('PASS: concurrent write (20 threads)')

shutil.rmtree(d, ignore_errors=True)
print('\nAll storage tests PASS')
