"""向量化引擎协议（`embedding` 类别）。

与 OCR / ASR / Doc 同属 `EngineRegistry`，但**刻意不继承 `core.protocols.Engine`**：
那个 ABC 的 `execute()` 对本类是「必须实现却永不被调用的空壳」—— 而空壳方法最坏的
不是多写三行，是**下一个人会以为它有用**（比如在那儿补一段 `return self.embed(...)`，
于是同步/异步混用、事件循环被 `asyncio.run` 嵌套）。这里用 `Protocol` 表达「长什么样」，
运行时零成本、注册表也是 duck typing（`EngineRegistry.register` 的类型标注不影响）。

## 契约（三条，全部有测试钉住）

1. **返回顺序与入参一一对应**（`out[i]` 是 `texts[i]` 的向量）。
   ⚠️ 这条必须显式写明：OpenAI 兼容端点的 `data` 数组**带 `index` 字段但不保证有序**，
   按返回顺序直接 append 会让「文本与向量错位」——检索质量下降而**零报错**。
2. **向量已做 L2 归一化**（模长 1）。于是余弦相似度退化成点积，检索层不必再问
   「这个引擎归一化了吗」—— 本地 bge 归一化、云端不归一化，两套判据会在换引擎那天失效。
   全零向量（理论上不该出现）原样返回，不做除零。
3. **空输入返回空列表，且不发起任何调用**。

## 失败语义

不降级、不回退：key 缺失 / 网络失败 / 维度不符一律直接抛。理由与其余引擎一致
（「引擎绑定只取第一个，失败直接报错」），且向量只是**增量收益**——字面检索仍在，
调用方应当把异常翻成「语义检索暂不可用」的可见状态，而不是静默当作「没搜到」。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingEngine(Protocol):
    """向量化引擎。`embed()` 是唯一的对外入口。"""

    #: 展示名，与 `EngineRegistry.register` 的 name 一致
    name: str
    #: 落库用于**失效判定**的模型标识（换模型 = 全量重建，见 search-retrieval.md §4.2）
    model_id: str
    #: 向量维度。必须与 `embed()` 实际返回的长度一致（服务端返回别的维度 → 抛错）
    dimension: int
    #: 引擎能力位（`core/registry.ENGINE_CAPABILITY_LABELS` 里要有对应标签）
    capabilities: list[str]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """把若干段文本编码成向量。语义见模块文档的三条契约。"""
        ...


def l2_normalize(vec: list[float]) -> list[float]:
    """L2 归一化。零向量原样返回（返回 NaN 比返回零向量更难查）。

    放在协议模块而不是各引擎里：归一化是**契约的一部分**，两份实现迟早会分叉
    （一份用 `math.hypot`、一份用 numpy，在极小模长上给出不同结果），
    而分叉的失效形态是「两个引擎的余弦不可比」—— 只有混用时才看得出来。
    """
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]
