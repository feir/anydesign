"""Numeric clustering for spacing / rounded tokens.

Two passes against the extractor payload:
1. `parse_lengths(payload, fields)` flattens spacing/rounded fields into a
   bag of px floats with provenance.
2. `cluster_lengths(values, k_max=5)` runs 1D k-means over the bag, yielding
   up to `k_max` representative values ranked by cluster size.

1D k-means converges fast and gives stable centroid orderings — far simpler
than 2D++; jenks-natural-breaks would also work but adds an external dep
(jenkspy) for negligible quality gain on this small data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Fields whose values are 1+ px lengths (CSS shorthand allowed).
# `gap` is intentionally separate from `padding` — they represent different
# token categories (gutter vs internal box) and mixing them blurs clusters.
DEFAULT_SPACING_FIELDS: tuple[str, ...] = ("padding",)
DEFAULT_ROUNDED_FIELDS: tuple[str, ...] = ("border-radius",)
DEFAULT_GAP_FIELDS: tuple[str, ...] = ("gap",)

_PX_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*px\b", re.IGNORECASE)


@dataclass(frozen=True)
class LengthSample:
    value_px: float
    field: str           # e.g. "padding"
    selector: str        # e.g. "h1"
    sample_index: int    # which sample within the selector group


@dataclass(frozen=True)
class LengthCluster:
    representative_px: float
    frequency: int
    members: tuple[float, ...] = field(default_factory=tuple)


def parse_lengths(
    payload: dict,
    *,
    fields: Iterable[str],
    drop_zero: bool = True,
    prefer_histogram: bool = True,
) -> list[LengthSample]:
    """Flatten requested CSS fields into px samples.

    Source priority:
    1. `payload['length_histogram']` (whole-DOM scan) when present and
       contains the field — gives full spacing scale for clustering.
    2. `payload['computed_styles']` (≤5 per selector × 7 selectors) — the
       narrow representative sweep.

    Multi-value shorthands (e.g. `padding: 16px 8px`) yield multiple samples.
    Non-px / unparseable values are dropped silently.
    """
    out: list[LengthSample] = []
    histogram = payload.get("length_histogram") or {}

    fields_tuple = tuple(fields)
    histogram_fields = {f for f in fields_tuple if prefer_histogram and f in histogram}
    fallback_fields = tuple(f for f in fields_tuple if f not in histogram_fields)

    # Histogram path: provenance is the whole-DOM scan (selector="*" sentinel).
    for f in histogram_fields:
        for px in histogram.get(f, []):
            px_f = float(px)
            if drop_zero and px_f == 0:
                continue
            out.append(LengthSample(
                value_px=px_f, field=f, selector="*", sample_index=-1,
            ))

    # Fallback path for fields the histogram doesn't cover.
    for sample in payload.get("computed_styles", []):
        sel = sample.get("selector", "")
        idx = int(sample.get("sample_index", 0))
        for f in fallback_fields:
            raw = sample.get(f) or ""
            for m in _PX_RE.finditer(raw):
                px = float(m.group(1))
                if drop_zero and px == 0:
                    continue
                out.append(LengthSample(
                    value_px=px, field=f, selector=sel, sample_index=idx,
                ))
    return out


def _initial_centroids(sorted_values: list[float], k: int) -> list[float]:
    # Use evenly-spaced quantile picks for reproducibility (no random seed).
    if k <= 1:
        return [sum(sorted_values) / len(sorted_values)]
    n = len(sorted_values)
    return [sorted_values[int((i + 0.5) * n / k)] for i in range(k)]


def _kmeans_1d(
    values: list[float],
    k: int,
    *,
    max_iter: int = 50,
    tol_px: float = 0.5,
) -> list[list[float]]:
    """Run 1D Lloyd's k-means; return list of clusters (each a list of values)."""
    sorted_vals = sorted(values)
    centroids = _initial_centroids(sorted_vals, k)
    for _ in range(max_iter):
        clusters: list[list[float]] = [[] for _ in range(k)]
        for v in sorted_vals:
            # Assign to nearest centroid; ties go to lowest index (stable).
            best_i, best_d = 0, abs(v - centroids[0])
            for i in range(1, k):
                d = abs(v - centroids[i])
                if d < best_d:
                    best_i, best_d = i, d
            clusters[best_i].append(v)
        new_centroids = [
            sum(c) / len(c) if c else centroids[i]
            for i, c in enumerate(clusters)
        ]
        max_shift = max(
            abs(new_centroids[i] - centroids[i]) for i in range(k)
        )
        centroids = new_centroids
        if max_shift < tol_px:
            break
    return [c for c in clusters if c]


def cluster_lengths(
    values: list[float],
    *,
    k_max: int = 5,
) -> list[LengthCluster]:
    """Cluster 1D lengths into up to k_max bins; return frequency-ranked clusters.

    `representative_px` is the cluster median (more robust than mean against
    outliers from layout-specific overrides).
    """
    if not values:
        return []
    unique = sorted(set(values))
    k = min(len(unique), max(1, k_max))
    clusters = _kmeans_1d(list(values), k)

    out: list[LengthCluster] = []
    for c in clusters:
        c_sorted = sorted(c)
        mid = c_sorted[len(c_sorted) // 2]  # median (lower middle for even n)
        out.append(LengthCluster(
            representative_px=mid,
            frequency=len(c),
            members=tuple(c_sorted),
        ))
    out.sort(key=lambda cl: (-cl.frequency, cl.representative_px))
    return out


def aggregate_spacing_and_rounded(
    payload: dict,
    *,
    spacing_fields: Iterable[str] = DEFAULT_SPACING_FIELDS,
    rounded_fields: Iterable[str] = DEFAULT_ROUNDED_FIELDS,
    k_max: int = 5,
) -> dict:
    """Convenience wrapper: run both spacing and rounded clustering on a payload."""
    spacing_samples = parse_lengths(payload, fields=spacing_fields)
    rounded_samples = parse_lengths(payload, fields=rounded_fields)
    return {
        "spacing": [
            {
                "representative_px": c.representative_px,
                "frequency": c.frequency,
                "members": list(c.members),
            }
            for c in cluster_lengths(
                [s.value_px for s in spacing_samples], k_max=k_max,
            )
        ],
        "rounded": [
            {
                "representative_px": c.representative_px,
                "frequency": c.frequency,
                "members": list(c.members),
            }
            for c in cluster_lengths(
                [s.value_px for s in rounded_samples], k_max=k_max,
            )
        ],
    }
