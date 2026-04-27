"""
Canonical cluster evidence for GEO recommendations and dashboard views.

This module is intentionally deterministic. It turns prompt monitoring,
LLM citations, Search Console, and GA4 data into one evidence object per
cluster so recommendation priority/type is explainable.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app import models
from app.visibility import DEFAULT_OWNED_DOMAINS, domain_matches_owned, domain_of, is_run_prompt, is_visible_prompt, split_csv


SUBSTITUTE_TERMS = {
    "carbon black",
    "conductive carbon black",
    "mwcnt",
    "multi-walled carbon nanotube",
    "multi wall carbon nanotube",
    "graphene nanoplatelet",
    "graphene",
    "carbon fiber",
    "metal filler",
}

TECHNICAL_SOURCE_HINTS = (
    "science",
    "nature",
    "doi.org",
    "echa",
    "osha",
    "epa",
    "cdc",
    "wiley",
    "springer",
    "sciencedirect",
    "iso.org",
    "astm",
)


@dataclass
class EvidenceWeights:
    gap_severity: int
    competitor_pressure: int
    search_demand: int
    existing_page_leverage: int
    business_priority: int
    confidence: int

    @property
    def priority_score(self) -> int:
        return min(100, round(
            self.gap_severity * 0.30
            + self.competitor_pressure * 0.20
            + self.search_demand * 0.20
            + self.existing_page_leverage * 0.15
            + self.business_priority * 0.10
            + self.confidence * 0.05
        ))


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


def owned_domains(db: Session) -> list[str]:
    raw = ",".join([
        ",".join(DEFAULT_OWNED_DOMAINS),
        _setting(db, "owned_domains", ""),
    ])
    out: list[str] = []
    for item in split_csv(raw):
        d = domain_of(item)
        if d and d not in out:
            out.append(d)
    return out


def _norm_words(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(w) > 2}


def _prompt_terms(prompts: Iterable[models.Prompt]) -> set[str]:
    terms: set[str] = set()
    for prompt in prompts:
        terms |= _norm_words(prompt.prompt_text)
        terms |= _norm_words(prompt.topic_cluster)
    return terms


def _matches_terms(text: str, terms: set[str]) -> bool:
    if not terms:
        return False
    words = _norm_words(text)
    return len(words & terms) >= 2


def _top_dict(counter: Counter, limit: int = 8) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _best_existing_page(gsc_rows: list[models.GoogleSearchMetric], ga4_rows: list[models.GoogleAnalyticsMetric]) -> dict | None:
    pages: dict[str, dict] = {}
    for row in gsc_rows:
        if not row.page:
            continue
        p = pages.setdefault(row.page, {
            "url": row.page,
            "title": "",
            "gsc_impressions": 0,
            "gsc_clicks": 0,
            "gsc_avg_position": 0,
            "ga4_sessions": 0,
            "ga4_users": 0,
            "_pos_weight": 0,
        })
        p["gsc_impressions"] += row.impressions or 0
        p["gsc_clicks"] += row.clicks or 0
        weight = max(row.impressions or 0, 1)
        p["gsc_avg_position"] += (row.avg_position or 0) * weight
        p["_pos_weight"] += weight
    for row in ga4_rows:
        key = row.page_path or row.page_title or row.metric_id
        p = pages.setdefault(key, {
            "url": row.page_path,
            "title": row.page_title,
            "gsc_impressions": 0,
            "gsc_clicks": 0,
            "gsc_avg_position": 0,
            "ga4_sessions": 0,
            "ga4_users": 0,
            "_pos_weight": 0,
        })
        p["title"] = p.get("title") or row.page_title
        p["ga4_sessions"] += row.sessions or 0
        p["ga4_users"] += row.active_users or 0
    if not pages:
        return None
    for p in pages.values():
        if p["_pos_weight"]:
            p["gsc_avg_position"] = round(p["gsc_avg_position"] / p["_pos_weight"], 1)
        p.pop("_pos_weight", None)
    return max(pages.values(), key=lambda p: (p["gsc_impressions"] >= 100 and 5 <= (p["gsc_avg_position"] or 99) <= 20, p["ga4_sessions"], p["gsc_impressions"]))


def _search_demand_score(impressions: int, clicks: int, avg_position: float) -> int:
    impression_score = min(70, round(impressions / 250))
    click_score = min(15, round(clicks / 10))
    position_score = 15 if 5 <= avg_position <= 20 else (8 if 1 <= avg_position < 5 else 0)
    return min(100, impression_score + click_score + position_score)


def _page_leverage_score(best_page: dict | None) -> int:
    if not best_page:
        return 0
    score = 0
    if best_page.get("gsc_impressions", 0) >= 100:
        score += 35
    if best_page.get("gsc_clicks", 0) > 0:
        score += 15
    if 5 <= (best_page.get("gsc_avg_position") or 99) <= 20:
        score += 25
    if best_page.get("ga4_sessions", 0) >= 100:
        score += 25
    return min(100, score)


def _opportunity_type(evidence: dict) -> str:
    total = max(evidence["run_count"], 1)
    competitor_rate = evidence["competitor_pressure_rate"]
    owned_rate = evidence["owned_citation_rate"]
    best_page = evidence.get("best_existing_page")
    has_leverage = bool(best_page and (best_page.get("ga4_sessions", 0) >= 100 or best_page.get("gsc_impressions", 0) >= 100))
    query_text = " ".join(q["query"] for q in evidence.get("top_gsc_queries", []))
    prompts_text = " ".join(evidence.get("linked_prompt_texts", []))
    cited_domains = " ".join(d["domain"] for d in evidence.get("top_external_sources", []))

    if any(term in (query_text + " " + prompts_text).lower() for term in SUBSTITUTE_TERMS):
        return "Defend Substitute Positioning"
    if competitor_rate >= 35:
        return "Add Comparison Section"
    if has_leverage:
        return "Upgrade Existing Page"
    if "?" in prompts_text or any(w in query_text.lower() for w in ("best", "how", "what", "which", "compare")):
        return "Add FAQ / Buyer Questions"
    if owned_rate == 0 and any(hint in cited_domains.lower() for hint in TECHNICAL_SOURCE_HINTS):
        return "Add Citation Proof"
    if best_page and owned_rate < 35:
        return "Improve Internal Linking"
    if total >= 2:
        return "Create Source Page"
    return "Add Citation Proof"


def _opportunity_title(opportunity_type: str, cluster: str, best_page: dict | None) -> str:
    if opportunity_type == "Upgrade Existing Page" and best_page:
        label = best_page.get("title") or best_page.get("url") or cluster
        return f"Upgrade existing {cluster} page for AI citations: {label[:70]}"
    if opportunity_type == "Add Comparison Section":
        return f"Add competitor comparison content for {cluster}"
    if opportunity_type == "Add FAQ / Buyer Questions":
        return f"Add buyer-question FAQ coverage for {cluster}"
    if opportunity_type == "Add Citation Proof":
        return f"Make OCSiAl/TUBALL citable for {cluster}"
    if opportunity_type == "Improve Internal Linking":
        return f"Strengthen internal authority links for {cluster}"
    if opportunity_type == "Defend Substitute Positioning":
        return f"Defend TUBALL against substitute materials in {cluster}"
    return f"Create source page for {cluster}"


def build_cluster_evidence(db: Session) -> list[dict]:
    prompts = db.query(models.Prompt).all()
    run_prompts = [p for p in prompts if is_run_prompt(p)]
    by_cluster: dict[str, list[models.Prompt]] = defaultdict(list)
    for prompt in run_prompts:
        by_cluster[prompt.topic_cluster or "Uncategorized"].append(prompt)

    gsc_all = db.query(models.GoogleSearchMetric).all()
    ga4_all = db.query(models.GoogleAnalyticsMetric).all()
    owned = owned_domains(db)
    out: list[dict] = []

    for cluster, plist in by_cluster.items():
        terms = _prompt_terms(plist)
        good = [p for p in plist if p.monitor_status == "Good"]
        risk = [p for p in plist if p.monitor_status == "Risk"]
        gap = [p for p in plist if p.monitor_status == "Gap"]
        covered = [p for p in plist if is_visible_prompt(p)]
        brand = [p for p in plist if p.brand_mentioned or p.product_mentioned]
        owned_cited = [p for p in plist if p.domain_cited]
        comp_prompts = [p for p in plist if p.competitors_mentioned]
        competitor_counts = Counter(c for p in plist for c in (p.competitors_mentioned or []))
        external_sources = Counter()
        owned_sources = Counter()
        for p in plist:
            for src in p.cited_sources or []:
                d = domain_of(src)
                if not d:
                    continue
                if domain_matches_owned(d, owned):
                    owned_sources[d] += 1
                else:
                    external_sources[d] += 1

        gsc_rows = [row for row in gsc_all if _matches_terms(row.query + " " + row.page, terms)]
        ga4_rows = [row for row in ga4_all if _matches_terms((row.page_path or "") + " " + (row.page_title or ""), terms)]
        gsc_impressions = sum(r.impressions or 0 for r in gsc_rows)
        gsc_clicks = sum(r.clicks or 0 for r in gsc_rows)
        pos_weight = sum(max(r.impressions or 0, 1) for r in gsc_rows)
        gsc_avg_position = round(sum((r.avg_position or 0) * max(r.impressions or 0, 1) for r in gsc_rows) / pos_weight, 1) if pos_weight else 0
        ga4_sessions = sum(r.sessions or 0 for r in ga4_rows)
        ga4_users = sum(r.active_users or 0 for r in ga4_rows)
        best_page = _best_existing_page(gsc_rows, ga4_rows)
        total = len(plist)
        max_priority = max((p.business_priority or 1 for p in plist), default=1)

        gap_severity = round(((len(gap) * 1.0 + len(risk) * 0.7) / total) * 100) if total else 0
        comp_pressure = round(len(comp_prompts) / total * 100) if total else 0
        search_score = _search_demand_score(gsc_impressions, gsc_clicks, gsc_avg_position)
        page_score = _page_leverage_score(best_page)
        business_score = min(100, max_priority * 20)
        confidence = min(95, 40 + min(total, 10) * 4 + (15 if gsc_rows else 0) + (15 if ga4_rows else 0))
        weights = EvidenceWeights(gap_severity, comp_pressure, search_score, page_score, business_score, confidence)

        evidence = {
            "cluster": cluster,
            "prompt_count": total,
            "run_count": total,
            "good_count": len(good),
            "risk_count": len(risk),
            "gap_count": len(gap),
            "coverage_rate": round(len(covered) / total * 100) if total else 0,
            "brand_mention_rate": round(len(brand) / total * 100) if total else 0,
            "owned_citation_rate": round(len(owned_cited) / total * 100) if total else 0,
            "competitor_pressure_rate": comp_pressure,
            "top_competitors": _top_dict(competitor_counts),
            "top_external_sources": [{"domain": k, "count": v} for k, v in external_sources.most_common(8)],
            "top_owned_sources": [{"domain": k, "count": v} for k, v in owned_sources.most_common(8)],
            "gsc_impressions": gsc_impressions,
            "gsc_clicks": gsc_clicks,
            "gsc_avg_position": gsc_avg_position,
            "ga4_sessions": ga4_sessions,
            "ga4_users": ga4_users,
            "top_gsc_queries": [
                {"metric_id": r.metric_id, "query": r.query, "page": r.page, "impressions": r.impressions, "clicks": r.clicks, "avg_position": r.avg_position}
                for r in sorted(gsc_rows, key=lambda r: r.impressions or 0, reverse=True)[:8]
            ],
            "top_ga4_pages": [
                {"metric_id": r.metric_id, "page_path": r.page_path, "page_title": r.page_title, "sessions": r.sessions, "active_users": r.active_users}
                for r in sorted(ga4_rows, key=lambda r: r.sessions or 0, reverse=True)[:8]
            ],
            "best_existing_page": best_page,
            "linked_prompt_ids": [p.prompt_id for p in plist],
            "linked_prompt_texts": [p.prompt_text for p in plist[:8]],
            "priority_components": weights.__dict__ | {"priority_score": weights.priority_score},
        }
        evidence["opportunity_type"] = _opportunity_type(evidence)
        evidence["opportunity_title"] = _opportunity_title(evidence["opportunity_type"], cluster, best_page)
        out.append(evidence)

    return sorted(out, key=lambda e: e["priority_components"]["priority_score"], reverse=True)


def get_cluster_evidence(db: Session, cluster: str) -> dict | None:
    for item in build_cluster_evidence(db):
        if item["cluster"] == cluster:
            return item
    return None
