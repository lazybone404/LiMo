"""
帧质量评估 — 图像统计指标，纯 PIL + numpy（零新依赖）

三个指标加权评分：
- 拉普拉斯方差（0.4）：检测模糊。PIL Kernel 卷积 → numpy var()
- 灰度熵（0.3）：检测信息量。numpy histogram → Shannon
- Sobel 边缘密度（0.3）：检测结构复杂度。PIL Kernel X/Y → numpy 梯度幅值

参考：KonVid-1k NR-VQA 帧级特征体系
"""

import logging
import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# ---- 卷积核 ----
_LAPLACIAN_KERNEL = [-1, -1, -1, -1, 8, -1, -1, -1, -1]
_SOBEL_X_KERNEL = [-1, 0, 1, -2, 0, 2, -1, 0, 1]
_SOBEL_Y_KERNEL = [-1, -2, -1, 0, 0, 0, 1, 2, 1]
_KERNEL_SIZE = (3, 3)

# ---- 归一化阈值（经验值，可调） ----
# PIL Kernel 的 scale=1 offset=128，方差值偏大
LAP_VAR_MIN, LAP_VAR_MAX = 100.0, 2000.0
# 灰度熵（8-bit，理论最大 ~7.99）
ENTROPY_MIN, ENTROPY_MAX = 2.5, 7.5
# 边缘密度（梯度幅值 >50 的像素占比）
EDGE_MIN, EDGE_MAX = 0.02, 0.30
# 边缘检测阈值（梯度幅值）
EDGE_THRESHOLD = 50.0

# 权重
W_LAP, W_ENT, W_EDGE = 0.4, 0.3, 0.3


def _load_gray(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("L"), dtype=np.float64)


def _laplacian_variance(gray: np.ndarray) -> float:
    """拉普拉斯方差 — 值越大越锐利"""
    img = Image.fromarray(gray.astype(np.uint8))
    lap = img.filter(ImageFilter.Kernel(_KERNEL_SIZE, _LAPLACIAN_KERNEL, scale=1, offset=128))
    return float(np.array(lap, dtype=np.float64).var())


def _entropy(gray: np.ndarray) -> float:
    """Shannon 灰度熵 — 纯色帧低，内容丰富帧高"""
    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    hist = hist.astype(np.float64) / hist.sum()
    hist = hist[hist > 0]
    return float(-np.sum(hist * np.log2(hist)))


def _edge_density(gray: np.ndarray) -> float:
    """Sobel 边缘密度 — PIL Kernel X/Y → 梯度幅值 → 边缘像素占比"""
    img = Image.fromarray(gray.astype(np.uint8))
    gx = np.array(
        img.filter(ImageFilter.Kernel(_KERNEL_SIZE, _SOBEL_X_KERNEL, scale=1, offset=128)),
        dtype=np.float64,
    )
    gy = np.array(
        img.filter(ImageFilter.Kernel(_KERNEL_SIZE, _SOBEL_Y_KERNEL, scale=1, offset=128)),
        dtype=np.float64,
    )
    # 还原偏移：PIL Kernel 加了 offset=128，梯度应该减去 128
    gx -= 128.0
    gy -= 128.0
    magnitude = np.sqrt(gx ** 2 + gy ** 2)
    return float(np.sum(magnitude > EDGE_THRESHOLD) / magnitude.size)


def _normalize(value: float, vmin: float, vmax: float) -> float:
    return max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))


def compute_quality_score(image_path: str) -> float:
    """计算帧质量综合评分 0.0 – 1.0。评估失败返回 1.0（宁过勿杀）。"""
    try:
        gray = _load_gray(image_path)
        lap = _laplacian_variance(gray)
        ent = _entropy(gray)
        edge = _edge_density(gray)

        score = (
            W_LAP * _normalize(lap, LAP_VAR_MIN, LAP_VAR_MAX)
            + W_ENT * _normalize(ent, ENTROPY_MIN, ENTROPY_MAX)
            + W_EDGE * _normalize(edge, EDGE_MIN, EDGE_MAX)
        )
        return score
    except Exception:
        logger.warning("帧质量评估失败: %s", image_path, exc_info=True)
        return 1.0


def filter_quality_frames(
    frame_paths: list[str], threshold: float = 0.15
) -> list[str]:
    """过滤低质量帧，返回保留的帧路径列表。threshold <= 0 则不过滤。"""
    if not frame_paths or threshold <= 0.0:
        return frame_paths

    kept: list[str] = []
    dropped = 0
    for path in frame_paths:
        score = compute_quality_score(path)
        if score >= threshold:
            kept.append(path)
        else:
            dropped += 1
            logger.debug("丢弃低质量帧 score=%.3f: %s", score, path)

    if dropped > 0:
        logger.info("帧质量过滤: 丢弃 %d/%d 帧 (threshold=%.2f)",
                    dropped, len(frame_paths), threshold)
    return kept
