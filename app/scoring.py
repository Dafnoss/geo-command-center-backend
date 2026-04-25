"""
Scoring formulas — spec §12.

All functions return integers in the range 0–100 unless noted.
Inputs are kept explicit (dicts / primitives) so the module has no ORM dependency.
"""

from __future__ import annotations

from typing import Iterable


# ---------------- 12.1 AI visibility ----------------
def ai_visibility_score(ai_results: list) -> int:
    """
    AI Visibility Score =
        brand_mention_rate * 0.30
      + product_mention_rate * 0.20
      + domain_citation_rate * 0.30
      + answer_quality_rate * 0.20

    `ai_results` is a list of dict-like rows with
    brand_mentioned, product_mentioned, domain_cited, answer_quality_score (1..5).
    """
    if not ai_results:
        return 0
    n = len(ai_results)
    brand = sum(1 for r in ai_results if _get(r, "brand_mentioned")) / n
    product = sum(1 for r in ai_results if _get(r, "product_mentioned")) / n
    domain = sum(1 for r in ai_results if _get(r, "domain_cited")) / n
    # quality is 1..5; normalize to 0..1
    quality_sum = sum(float(_get(r, "answer_quality_score") or 0) for r in ai_results)
    quality = (quality_sum / (n * 5.0)) if n else 0.0

    score = brand * 0.30 + product * 0.20 + domain * 0.30 + quality * 0.20
    return round(score * 100)


# ---------------- 12.2 Competitor pressure ----------------
def competitor_pressure_score(ai_results: list, sources: list) -> int:
    """
    Competitor Pressure Score =
        competitor_mention_rate * 0.60
      + competitor_citation_rate * 0.40
    """
    if not ai_results:
        return 0
    n = len(ai_results)

    mention_rate = sum(
        1 for r in ai_results if _nonempty_list(_get(r, "competitors_mentioned"))
    ) / n

    if sources:
        citation_rate = sum(1 for s in sources if _get(s, "mentions_competitor")) / len(sources)
    else:
        citation_rate = 0.0

    return round((mention_rate * 0.60 + citation_rate * 0.40) * 100)


# ---------------- 12.3 Source influence ----------------
_SOURCE_TYPE_WEIGHT = {
    "Academic": 25,
    "Industry media": 20,
    "Regulatory": 20,
    "Supplier list": 15,
    "Directory": 10,
    "Forum": 5,
    "Unknown": 0,
    "Competitor page": 10,
    "Blog": 5,
}


def source_influence_score(
    *,
    prompts_citing: int,
    platforms_citing: int,
    source_type: str,
    competitor_mention_bonus: int = 0,
    outdated_penalty: int = 0,
) -> int:
    """
    Source Influence Score =
        prompts_citing * 10
      + platforms_citing * 15
      + source_type_weight
      + competitor_mention_bonus
      - outdated_penalty
    Clamped to 0..100.
    """
    raw = (
        prompts_citing * 10
        + platforms_citing * 15
        + _SOURCE_TYPE_WEIGHT.get(source_type, 0)
        + competitor_mention_bonus
        - outdated_penalty
    )
    return max(0, min(100, raw))


# ---------------- 12.4 Page readiness ----------------
def page_readiness_score(url_row) -> int:
    """
    Page Readiness Score =
        indexable * 20
      + has_direct_answer * 15
      + has_comparison_table * 15
      + has_faq * 10
      + has_citations * 15
      + has_internal_links * 10
      + has_cta * 10
      + has_schema * 5
    """
    weights = {
        "indexable": 20,
        "has_direct_answer": 15,
        "has_comparison_table": 15,
        "has_faq": 10,
        "has_citations": 15,
        "has_internal_links": 10,
        "has_cta": 10,
        "has_schema": 5,
    }
    score = sum(w for k, w in weights.items() if bool(_get(url_row, k)))
    return int(score)


# ---------------- 12.5 Recommendation priority ----------------
def recommendation_priority_score(
    *,
    business_priority: float,  # 0..1 (already normalized, e.g. 5-scale/5)
    ai_visibility_gap: float,  # 0..1
    seo_opportunity: float,    # 0..1
    competitor_pressure: float,  # 0..1
    source_influence: float,   # 0..1
    conversion_potential: float,  # 0..1
    implementation_effort: float,  # 0..1  (higher = more effort → subtracted)
    dependency_risk: float,    # 0..1
) -> int:
    """
    Priority Score =
        business_priority * 15
      + ai_visibility_gap * 20
      + seo_opportunity * 15
      + competitor_pressure * 15
      + source_influence * 10
      + conversion_potential * 15
      - implementation_effort * 10
      - dependency_risk * 5

    Inputs are each 0..1; we multiply by the weight to get raw, then normalize to 0..100.
    Max positive contribution = 15+20+15+15+10+15 = 90
    Max negative contribution = 10+5 = 15
    So the raw range is roughly [-15, 90]. We map this to 0..100 linearly.
    """
    raw = (
        business_priority * 15
        + ai_visibility_gap * 20
        + seo_opportunity * 15
        + competitor_pressure * 15
        + source_influence * 10
        + conversion_potential * 15
        - implementation_effort * 10
        - dependency_risk * 5
    )
    # linear map from [-15, 90] to [0, 100]
    normalized = (raw - (-15)) / (90 - (-15)) * 100
    return int(max(0, min(100, round(normalized))))


def priority_label(score: int) -> str:
    """Map 0..100 priority score → High/Medium/Low (spec §12.5)."""
    if score >= 80:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


# ---------------- helpers ----------------
def _get(row, field, default=None):
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def _nonempty_list(v) -> bool:
    if not v:
        return False
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    return True
