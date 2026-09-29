"""人口出入数据库时的守恒取整；空间计算仍由 GPU 执行。"""
from __future__ import annotations

import numpy as np


def apportion_population(weights: np.ndarray, total: int) -> np.ndarray:
    """按现有非零分布分配整数人口，最大余数法保证地块和等于总数。"""
    result = np.zeros(weights.shape, dtype=np.int64)
    total = max(0, int(total))
    flat = np.maximum(np.asarray(weights, dtype=np.longdouble).ravel(), 0)
    indices = np.flatnonzero(np.isfinite(flat) & (flat > 0))
    if total == 0 or indices.size == 0:
        return result
    shares = flat[indices] / flat[indices].sum() * total
    counts = np.floor(shares).astype(np.int64)
    remainder = total - int(counts.sum())
    if remainder > 0:
        order = np.argsort(-(shares - counts), kind="stable")
        counts[order[:remainder]] += 1
    elif remainder < 0:
        order = np.argsort(shares - counts, kind="stable")
        eligible = order[counts[order] > 0]
        counts[eligible[:-remainder]] -= 1
    result.ravel()[indices] = counts
    return result
