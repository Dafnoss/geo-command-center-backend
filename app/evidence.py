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
from urllib.parse import unquote, urlparse

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

GENERIC_TERMS = {
    "additive", "additives", "carbon", "nanotube", "nanotubes", "graphene",
    "material", "materials", "polymer", "polymers", "best", "top",
    "supplier", "suppliers", "application", "applications", "conductive",
    "conductivity", "compound", "compounds", "industrial",
}

DOMAIN_TERMS = {
    "antistatic", "anti-static", "static", "esd", "coating", "coatings",
    "paint", "paints", "rubber", "elastomer", "elastomers", "silicone",
    "tpu", "epdm", "fkm", "plastic", "plastics", "polycarbonate", "abs",
    "polyurethane", "epoxy", "battery", "batteries", "regulation",
    "regulatory", "safety", "toxicity", "reach", "supplier", "vendor",
    "procurement", "masterbatch", "flooring", "transparent", "colored",
    "dosage", "loading", "viscosity",
}

PRODUCT_PATTERNS = (
    ("TUBALL MATRIX", ("tuball matrix", "masterbatch")),
    ("TUBALL / graphene nanotubes", ("tuball", "graphene nanotube", "graphene nanotubes")),
    ("SWCNT additives", ("single-walled carbon nanotube", "single wall carbon nanotube", "swcnt")),
    ("CNT additives", ("carbon nanotube", "carbon nanotubes", "cnt", "nanotube")),
    ("Conductive additives", ("conductive additive", "conductive filler", "conductive agent")),
    ("Anti-static / ESD additives", ("anti-static", "antistatic", "static electricity", "esd")),
    ("Carbon black substitutes", ("carbon black", "conductive carbon black")),
)

APPLICATION_PATTERNS = (
    ("silicone rubber", ("silicone rubber", "silicone")),
    ("rubber and elastomers", ("elastomer", "elastomers", "rubber", "epdm", "fkm")),
    ("plastics and polymers", ("plastic", "plastics", "polymer", "polymers", "pa ", "polyamide", "polycarbonate", "abs", "tpu")),
    ("coatings and paints", ("coating", "coatings", "paint", "paints", "gelcoat", "flooring")),
    ("epoxy and resins", ("epoxy", "resin", "thermoset")),
    ("battery electrodes", ("battery", "batteries", "electrode", "cathode", "anode")),
    ("adhesives and sealants", ("adhesive", "sealant")),
    ("composites", ("composite", "composites", "lightweight")),
    ("masterbatch and compounds", ("masterbatch", "compound", "compounds")),
)

INTENT_PATTERNS = (
    ("supplier/vendor", ("supplier", "suppliers", "vendor", "vendors", "manufacturer", "manufacturers", "companies", "company", "procurement")),
    ("comparison", (" vs ", " versus ", "compare", "comparison", "alternative", "alternatives")),
    ("substitute/alternative", ("substitute", "replacement", "instead of", "alternative", "alternatives")),
    ("safety/regulatory", ("safety", "regulatory", "regulation", "reach", "toxicity", "standard", "standards")),
    ("application/use-case", ("what additive", "which additive", "best additive", "how to make", "application", "use case")),
    ("performance", ("low dosage", "low loading", "color", "transparent", "mechanical", "viscosity", "conductivity", "uniform")),
)

SUBSTITUTE_PATTERNS = (
    ("carbon black", ("carbon black", "conductive carbon black", "ketjenblack")),
    ("MWCNT", ("mwcnt", "multi-walled carbon nanotube", "multi wall carbon nanotube", "multiwalled carbon nanotube")),
    ("graphene", ("graphene nanoplatelet", "graphene nanoplatelets", "graphene")),
    ("carbon fiber", ("carbon fiber", "carbon fibres", "carbon fibres")),
    ("metal fillers", ("metal filler", "metal fillers", "metal powder", "metal powders")),
)

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

NOISY_QUERY_PATTERNS = (
    re.compile(r"^\s*(what\s+(is|are)\s+)?(carbon black|graphene|carbon nanotubes?|cnt)\s+(used for|uses|production|meaning|definition)\??\s*$", re.I),
    re.compile(r"^\s*(ocsial|tuball|tuball matrix|ocsial\.com|tuball\.com)\s*$", re.I),
    re.compile(r"\b(where to order|factory that produced|factory that produces)\b", re.I),
)

UNRELATED_QUERY_TERMS = {
    "career", "careers", "jobs", "vacancy", "salary", "ppe", "glove",
    "gloves", "fabric", "fabrics", "textile", "textiles", "clothing",
    "powder coating gun", "anti static bag", "paa box", "novatic",
}

MIN_RELEVANT_EVIDENCE_SCORE = 35
MIN_TARGET_PAGE_SCORE = 60


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
        raw = round(
            self.gap_severity * 0.30
            + self.competitor_pressure * 0.20
            + self.search_demand * 0.20
            + self.existing_page_leverage * 0.15
            + self.business_priority * 0.10
            + self.confidence * 0.05
        )
        # Stretch mid-range values so priority bands are useful in the UI.
        return min(100, max(0, round((raw - 45) * 1.55 + 45)))


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


def _prompt_terms(prompts: Iterable[models.Prompt]) -> tuple[set[str], set[str]]:
    terms: set[str] = set()
    specific: set[str] = set()
    for prompt in prompts:
        row_terms = _norm_words(prompt.prompt_text) | _norm_words(prompt.topic_cluster)
        terms |= row_terms
        specific |= {t for t in row_terms if t not in GENERIC_TERMS}
    return terms, specific


def _matches_terms(text: str, terms: set[str], specific_terms: set[str]) -> bool:
    return _evidence_match_score(text, "", terms, specific_terms, {}) >= MIN_RELEVANT_EVIDENCE_SCORE


def _query_noise_reason(query: str, classifier: dict) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not q:
        return ""
    for pattern in NOISY_QUERY_PATTERNS:
        if pattern.search(q):
            return "generic_or_navigation_query"
    intent = (classifier.get("buyer_intent") or "").lower()
    application = (classifier.get("application") or "").lower()
    if (any(term in q for term in ("standard", "standards")) or re.search(r"\b(astm|iso|iec|ansi)\b", q)) and intent != "safety/regulatory":
        return "standards_query_outside_regulatory_context"
    for term in UNRELATED_QUERY_TERMS:
        if term in q:
            if term in {"fabric", "fabrics", "textile", "textiles"} and "textile" in application:
                continue
            return "outside_business_scope"
    return ""


def _evidence_match_score(
    text: str,
    query: str,
    terms: set[str],
    specific_terms: set[str],
    classifier: dict,
) -> int:
    if not terms:
        return 0
    noise = _query_noise_reason(query, classifier)
    if noise:
        return 0
    value = (text or "").lower()
    words = _norm_words(value)
    if not words:
        return 0

    product_terms = _norm_words(classifier.get("product_area", ""))
    application_terms = _norm_words(classifier.get("application", ""))
    intent_terms = _norm_words(classifier.get("buyer_intent", ""))
    substitute_terms = _norm_words(classifier.get("substitute_theme", ""))
    specific_overlap = words & {t for t in specific_terms if t not in GENERIC_TERMS}
    domain_overlap = words & DOMAIN_TERMS & terms

    score = 0
    score += min(32, len(specific_overlap) * 14)
    score += min(28, len(words & product_terms) * 12)
    score += min(30, len(words & application_terms) * 18)
    score += min(24, len(words & substitute_terms) * 18)
    score += min(14, len(words & intent_terms) * 7)
    score += min(12, len(domain_overlap) * 6)

    # Phrase matches help owned page URLs/titles score strongly when the words
    # are meaningful but individually generic, e.g. conductive additive pages.
    for label in (
        classifier.get("product_area", ""),
        classifier.get("application", ""),
        classifier.get("substitute_theme", ""),
    ):
        clean = (label or "").lower()
        if clean and clean != "general industrial materials" and clean in value:
            score += 18

    # Avoid letting one broad material term select a target page.
    if len(specific_overlap | domain_overlap | (words & product_terms) | (words & application_terms) | (words & substitute_terms)) < 2:
        return min(score, 28)
    return min(100, score)


def _filter_gsc_rows(
    rows: list[models.GoogleSearchMetric],
    terms: set[str],
    specific_terms: set[str],
    classifier: dict,
) -> tuple[list[models.GoogleSearchMetric], int, list[dict]]:
    accepted: list[models.GoogleSearchMetric] = []
    filtered = 0
    diagnostics: list[dict] = []
    for row in rows:
        text = " ".join([row.query or "", row.page or ""])
        score = _evidence_match_score(text, row.query or "", terms, specific_terms, classifier)
        reason = _query_noise_reason(row.query or "", classifier)
        if score >= MIN_RELEVANT_EVIDENCE_SCORE:
            setattr(row, "_match_score", score)
            accepted.append(row)
        else:
            filtered += 1
            if len(diagnostics) < 10:
                diagnostics.append({
                    "metric_id": row.metric_id,
                    "query": row.query,
                    "page": row.page,
                    "score": score,
                    "reason": reason or "weak_relevance_match",
                    "impressions": row.impressions,
                    "clicks": row.clicks,
                })
    return accepted, filtered, diagnostics


def _filter_ga4_rows(
    rows: list[models.GoogleAnalyticsMetric],
    terms: set[str],
    specific_terms: set[str],
    classifier: dict,
) -> tuple[list[models.GoogleAnalyticsMetric], int, list[dict]]:
    accepted: list[models.GoogleAnalyticsMetric] = []
    filtered = 0
    diagnostics: list[dict] = []
    for row in rows:
        text = " ".join([row.page_path or "", row.page_title or ""])
        score = _evidence_match_score(text, "", terms, specific_terms, classifier)
        if score >= MIN_RELEVANT_EVIDENCE_SCORE:
            setattr(row, "_match_score", score)
            accepted.append(row)
        else:
            filtered += 1
            if len(diagnostics) < 10:
                diagnostics.append({
                    "metric_id": row.metric_id,
                    "page_path": row.page_path,
                    "page_title": row.page_title,
                    "score": score,
                    "reason": "weak_relevance_match",
                    "sessions": row.sessions,
                    "active_users": row.active_users,
                })
    return accepted, filtered, diagnostics


def _top_dict(counter: Counter, limit: int = 8) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _best_existing_page(gsc_rows: list[models.GoogleSearchMetric], ga4_rows: list[models.GoogleAnalyticsMetric]) -> dict | None:
    pages: dict[str, dict] = {}
    for row in gsc_rows:
        if not row.page:
            continue
        key = _page_match_key(row.page)
        p = pages.setdefault(key, {
            "url": row.page,
            "title": "",
            "gsc_impressions": 0,
            "gsc_clicks": 0,
            "gsc_avg_position": 0,
            "ga4_sessions": 0,
            "ga4_users": 0,
            "_pos_weight": 0,
            "_match_scores": [],
        })
        p["_match_scores"].append(getattr(row, "_match_score", MIN_RELEVANT_EVIDENCE_SCORE))
        p["gsc_impressions"] += row.impressions or 0
        p["gsc_clicks"] += row.clicks or 0
        weight = max(row.impressions or 0, 1)
        p["gsc_avg_position"] += (row.avg_position or 0) * weight
        p["_pos_weight"] += weight
    for row in ga4_rows:
        key = _page_match_key(row.page_path) or _page_match_key(row.page_title) or row.metric_id
        p = pages.setdefault(key, {
            "url": row.page_path,
            "title": row.page_title,
            "gsc_impressions": 0,
            "gsc_clicks": 0,
            "gsc_avg_position": 0,
            "ga4_sessions": 0,
            "ga4_users": 0,
            "_pos_weight": 0,
            "_match_scores": [],
        })
        p["_match_scores"].append(getattr(row, "_match_score", MIN_RELEVANT_EVIDENCE_SCORE))
        if not p.get("url"):
            p["url"] = row.page_path
        p["title"] = p.get("title") or row.page_title
        p["ga4_sessions"] += row.sessions or 0
        p["ga4_users"] += row.active_users or 0
    if not pages:
        return None
    for p in pages.values():
        if p["_pos_weight"]:
            p["gsc_avg_position"] = round(p["gsc_avg_position"] / p["_pos_weight"], 1)
        scores = p.pop("_match_scores", []) or [0]
        p["page_match_score"] = round(sum(scores) / len(scores))
        p["target_page_confidence"] = min(100, round(p["page_match_score"] * 0.7 + _page_leverage_score(p) * 0.3))
        p.pop("_pos_weight", None)
    best = max(pages.values(), key=lambda p: (
        p["page_match_score"],
        p["gsc_impressions"] >= 100 and 5 <= (p["gsc_avg_position"] or 99) <= 20,
        p["ga4_sessions"],
        p["gsc_impressions"],
    ))
    if best["page_match_score"] < MIN_TARGET_PAGE_SCORE:
        return None
    return best


def _page_match_key(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme and parsed.netloc else raw
    path = unquote(path).strip().lower()
    path = re.sub(r"[?#].*$", "", path)
    path = re.sub(r"/+", "/", path).rstrip("/")
    return path or "/"


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


def _evidence_quality_score(total: int, gsc_rows: list, ga4_rows: list, filtered_count: int) -> int:
    metric_scores = [getattr(r, "_match_score", MIN_RELEVANT_EVIDENCE_SCORE) for r in (gsc_rows + ga4_rows)]
    metric_quality = round(sum(metric_scores) / len(metric_scores)) if metric_scores else 0
    prompt_quality = min(45, 30 + min(total, 8) * 3)
    evidence_bonus = 0
    if gsc_rows:
        evidence_bonus += 10
    if ga4_rows:
        evidence_bonus += 10
    noise_penalty = min(15, round(filtered_count / 25))
    return max(0, min(100, max(prompt_quality, metric_quality) + evidence_bonus - noise_penalty))


def _success_metric(evidence: dict) -> str:
    prompt_count = evidence.get("run_count", 0)
    coverage = evidence.get("coverage_rate", 0)
    owned = evidence.get("owned_citation_rate", 0)
    return (
        f"After implementation, rerun {prompt_count} linked prompts; target coverage above "
        f"{min(100, max(coverage + 15, 65))}% and owned citation above {min(100, max(owned + 15, 50))}%."
    )


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    value = f" {text.lower()} "
    return any(needle in value for needle in needles)


def classify_opportunity(text: str, cluster: str = "") -> dict:
    hay = re.sub(r"\s+", " ", f"{text or ''} {cluster or ''}".lower())
    product_area = _first_pattern(hay, PRODUCT_PATTERNS) or "Conductive / anti-static additives"
    application = _first_pattern(hay, APPLICATION_PATTERNS) or "general industrial materials"
    buyer_intent = _first_pattern(hay, INTENT_PATTERNS) or "category education"
    substitute_theme = _first_pattern(hay, SUBSTITUTE_PATTERNS) or ""

    if substitute_theme and buyer_intent == "category education":
        buyer_intent = "substitute/alternative"
    if any(term in hay for term in ("best", "which", "what additive", "how to make")) and buyer_intent == "category education":
        buyer_intent = "application/use-case"

    label_parts = []
    if application != "general industrial materials":
        label_parts.append(_human_label(application))
    label_parts.append(_human_label(product_area))
    if buyer_intent not in ("category education", "application/use-case"):
        label_parts.append(_human_label(buyer_intent))
    if substitute_theme:
        label_parts.append("vs " + _human_label(substitute_theme))

    label = " / ".join(dict.fromkeys(label_parts))[:96] or _human_label(cluster or "GEO opportunity")
    key = _slug("|".join([
        product_area,
        application,
        buyer_intent,
        substitute_theme,
    ]))
    return {
        "opportunity_key": key,
        "opportunity_label": label,
        "product_area": product_area,
        "application": application,
        "buyer_intent": buyer_intent,
        "substitute_theme": substitute_theme,
    }


def _first_pattern(text: str, patterns: Iterable[tuple[str, Iterable[str]]]) -> str:
    for label, needles in patterns:
        if _contains_any(text, needles):
            return label
    return ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "general"


def _substitute_hits(evidence: dict) -> int:
    query_text = " ".join(q.get("query", "") for q in evidence.get("top_gsc_queries", []))
    prompts_text = " ".join(evidence.get("linked_prompt_texts", []))
    comp_text = " ".join(c.get("name", "") for c in evidence.get("top_competitors", []))
    hay = f"{query_text} {prompts_text} {comp_text}".lower()
    return sum(1 for term in SUBSTITUTE_TERMS if term in hay)


def _technical_source_cited(evidence: dict) -> bool:
    cited_domains = " ".join(d.get("domain", "") for d in evidence.get("top_external_sources", []))
    return any(hint in cited_domains.lower() for hint in TECHNICAL_SOURCE_HINTS)


def _failure_modes(evidence: dict) -> list[str]:
    modes: list[str] = []
    weak_count = evidence["gap_count"] + evidence["risk_count"]
    best_page = evidence.get("best_existing_page")
    if weak_count:
        if evidence["brand_mention_rate"] < 50:
            modes.append("No Brand Visibility")
        if evidence["owned_citation_rate"] < 35:
            modes.append("No Owned Citation")
        if evidence["competitor_pressure_rate"] >= 35:
            modes.append("Competitor Dominated")
        if evidence.get("substitute_theme") or _substitute_hits(evidence) >= 1:
            modes.append("Substitute Dominated")
        if not best_page:
            modes.append("No Matching Owned Page")
        elif evidence["coverage_rate"] < 70 or evidence["owned_citation_rate"] < 45:
            modes.append("Weak Existing Page")
        if evidence["owned_citation_rate"] < 35 and _technical_source_cited(evidence):
            modes.append("Weak Proof / Citation Asset")
    return list(dict.fromkeys(modes))


def _opportunity_type(evidence: dict) -> str:
    competitor_rate = evidence["competitor_pressure_rate"]
    owned_rate = evidence["owned_citation_rate"]
    best_page = evidence.get("best_existing_page")
    modes = evidence.get("failure_modes") or _failure_modes(evidence)
    has_leverage = bool(best_page and (best_page.get("ga4_sessions", 0) >= 100 or best_page.get("gsc_impressions", 0) >= 100))
    query_text = " ".join(q["query"] for q in evidence.get("top_gsc_queries", []))
    prompts_text = " ".join(evidence.get("linked_prompt_texts", []))
    substitute_hits = _substitute_hits(evidence)
    buyer_intent = evidence.get("buyer_intent", "")
    external_citations = sum(x.get("count", 0) for x in evidence.get("top_external_sources", []))
    owned_citations = sum(x.get("count", 0) for x in evidence.get("top_owned_sources", []))

    if "Substitute Dominated" in modes and (competitor_rate >= 25 or substitute_hits >= 1):
        return "Defend Substitute Positioning"
    if competitor_rate >= 45 or buyer_intent == "comparison":
        return "Add Comparison Section"
    if owned_rate < 25 and external_citations >= max(2, evidence.get("run_count", 0) // 3) and owned_citations == 0:
        return "Improve Source Authority"
    if "Weak Proof / Citation Asset" in modes:
        return "Add Citation Proof"
    if has_leverage and "Weak Existing Page" in modes:
        return "Upgrade Existing Page"
    if "No Matching Owned Page" in modes and evidence["run_count"] >= 2:
        return "Create Source Page"
    if best_page and owned_rate < 35:
        return "Improve Internal Linking"
    if "?" in prompts_text or any(w in query_text.lower() for w in ("best", "how", "what", "which", "compare")):
        return "Add FAQ / Buyer Questions"
    if evidence["run_count"] >= 2:
        return "Create Source Page"
    return "Add Citation Proof"


def _opportunity_title(opportunity_type: str, cluster: str, best_page: dict | None, evidence: dict | None = None) -> str:
    cluster_label = _human_label(cluster)
    evidence = evidence or {}
    app = evidence.get("application")
    product = evidence.get("product_area")
    theme = evidence.get("substitute_theme")
    topic = _human_label(" ".join(x for x in [app if app != "general industrial materials" else "", product or ""] if x).strip() or cluster_label)
    if opportunity_type == "Upgrade Existing Page" and best_page:
        label = _page_label(best_page)
        return f"Upgrade {label} for {topic}"
    if opportunity_type == "Add Comparison Section":
        suffix = f" vs {_human_label(theme)}" if theme else ""
        return f"Add comparison content for {topic}{suffix}"
    if opportunity_type == "Add FAQ / Buyer Questions":
        return f"Add buyer-question FAQ for {topic}"
    if opportunity_type == "Add Citation Proof":
        return f"Create citation proof for {topic}"
    if opportunity_type == "Improve Internal Linking":
        return f"Strengthen links to the {topic} source page"
    if opportunity_type == "Improve Source Authority":
        return f"Make OCSiAl/TUBALL citable for {topic}"
    if opportunity_type == "Defend Substitute Positioning":
        substitute = _human_label(theme or "substitute materials")
        return f"Defend TUBALL vs {substitute} for {topic}"
    return f"Create source page for {topic}"


def _page_label(page: dict) -> str:
    title = (page.get("title") or "").strip()
    if title:
        return _human_label(title)[:72]
    url = (page.get("url") or "").strip().rstrip("/")
    if not url:
        return "existing page"
    slug = unquote(url.split("/")[-1]).replace("-", " ").replace("_", " ")
    return _human_label(slug)[:72] or url


def _human_label(value: str) -> str:
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return raw
    acronyms = {"ai", "geo", "seo", "gsc", "ga4", "esd", "faq", "url", "cnt", "swcnt", "mwcnt", "pa", "pc", "abs", "tpu", "epdm", "fkm"}
    small = {"and", "or", "for", "to", "of", "in", "vs", "with", "without", "the", "a", "an"}
    words = []
    for i, word in enumerate(raw.replace("/", " / ").split()):
        lower = word.lower()
        if word == "/":
            words.append("/")
        elif lower in acronyms:
            words.append(lower.upper())
        elif i > 0 and lower in small:
            words.append(lower)
        else:
            words.append(lower[:1].upper() + lower[1:])
    return " ".join(words).replace(" / ", "/")


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
        terms, specific_terms = _prompt_terms(plist)
        classifier = classify_opportunity(" ".join(p.prompt_text for p in plist[:12]), cluster)
        terms |= _norm_words(" ".join([
            classifier.get("product_area", ""),
            classifier.get("application", ""),
            classifier.get("buyer_intent", ""),
            classifier.get("substitute_theme", ""),
        ]))
        specific_terms |= {t for t in _norm_words(" ".join([
            classifier.get("product_area", ""),
            classifier.get("application", ""),
            classifier.get("substitute_theme", ""),
        ])) if t not in GENERIC_TERMS}
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

        gsc_rows, filtered_gsc_count, filtered_gsc = _filter_gsc_rows(gsc_all, terms, specific_terms, classifier)
        ga4_rows, filtered_ga4_count, filtered_ga4 = _filter_ga4_rows(ga4_all, terms, specific_terms, classifier)
        filtered_out_count = filtered_gsc_count + filtered_ga4_count
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
        evidence_quality = _evidence_quality_score(total, gsc_rows, ga4_rows, filtered_out_count)
        confidence = min(95, 40 + min(total, 10) * 4 + (15 if gsc_rows else 0) + (15 if ga4_rows else 0))
        weights = EvidenceWeights(gap_severity, comp_pressure, search_score, page_score, business_score, confidence)
        evidence = {
            "cluster": cluster,
            "opportunity_key": classifier["opportunity_key"],
            "product_area": classifier["product_area"],
            "application": classifier["application"],
            "buyer_intent": classifier["buyer_intent"],
            "substitute_theme": classifier["substitute_theme"],
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
                {"metric_id": r.metric_id, "query": r.query, "page": r.page, "impressions": r.impressions, "clicks": r.clicks, "avg_position": r.avg_position, "evidence_quality": getattr(r, "_match_score", 0)}
                for r in sorted(gsc_rows, key=lambda r: r.impressions or 0, reverse=True)[:8]
            ],
            "top_ga4_pages": [
                {"metric_id": r.metric_id, "page_path": r.page_path, "page_title": r.page_title, "sessions": r.sessions, "active_users": r.active_users, "evidence_quality": getattr(r, "_match_score", 0)}
                for r in sorted(ga4_rows, key=lambda r: r.sessions or 0, reverse=True)[:8]
            ],
            "best_existing_page": best_page,
            "linked_prompt_ids": [p.prompt_id for p in plist],
            "linked_prompt_texts": [p.prompt_text for p in plist[:8]],
            "prompts_to_rerun": [p.prompt_id for p in plist if p.monitor_status in ("Gap", "Risk")][:12],
            "evidence_quality": evidence_quality,
            "page_match_score": best_page.get("page_match_score") if best_page else 0,
            "target_page_confidence": best_page.get("target_page_confidence") if best_page else 0,
            "filtered_out_evidence_count": filtered_out_count,
            "filtered_out_evidence": (filtered_gsc + filtered_ga4)[:10],
            "priority_components": weights.__dict__ | {"priority_score": weights.priority_score},
        }
        evidence["failure_modes"] = _failure_modes(evidence)
        evidence["opportunity_type"] = _opportunity_type(evidence)
        evidence["opportunity_title"] = _opportunity_title(evidence["opportunity_type"], cluster, best_page, evidence)
        evidence["success_metric"] = _success_metric(evidence)
        out.append(evidence)

    return sorted(out, key=lambda e: e["priority_components"]["priority_score"], reverse=True)


def build_opportunity_evidence(db: Session) -> list[dict]:
    """
    Build recommendation-ready evidence around buyer opportunities, not raw
    dashboard clusters. An opportunity is product/application/intent/substitute
    context, which is the level at which a useful GEO action can be owned.
    """
    prompts = db.query(models.Prompt).all()
    run_prompts = [p for p in prompts if is_run_prompt(p)]
    groups: dict[str, dict] = {}
    for prompt in run_prompts:
        classifier = classify_opportunity(prompt.prompt_text, prompt.topic_cluster)
        key = classifier["opportunity_key"]
        row = groups.setdefault(key, {
            **classifier,
            "prompts": [],
            "source_clusters": [],
        })
        row["prompts"].append(prompt)
        if prompt.topic_cluster and prompt.topic_cluster not in row["source_clusters"]:
            row["source_clusters"].append(prompt.topic_cluster)

    gsc_all = db.query(models.GoogleSearchMetric).all()
    ga4_all = db.query(models.GoogleAnalyticsMetric).all()
    owned = owned_domains(db)
    out: list[dict] = []

    for key, group in groups.items():
        plist = group["prompts"]
        terms, specific_terms = _prompt_terms(plist)
        terms |= _norm_words(" ".join([
            group.get("product_area", ""),
            group.get("application", ""),
            group.get("buyer_intent", ""),
            group.get("substitute_theme", ""),
        ]))
        specific_terms |= {t for t in _norm_words(" ".join([
            group.get("product_area", ""),
            group.get("application", ""),
            group.get("substitute_theme", ""),
        ])) if t not in GENERIC_TERMS}

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

        gsc_rows, filtered_gsc_count, filtered_gsc = _filter_gsc_rows(gsc_all, terms, specific_terms, group)
        ga4_rows, filtered_ga4_count, filtered_ga4 = _filter_ga4_rows(ga4_all, terms, specific_terms, group)
        filtered_out_count = filtered_gsc_count + filtered_ga4_count
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
        evidence_quality = _evidence_quality_score(total, gsc_rows, ga4_rows, filtered_out_count)
        confidence = min(95, 45 + min(total, 8) * 5 + (15 if gsc_rows else 0) + (15 if ga4_rows else 0))
        weights = EvidenceWeights(gap_severity, comp_pressure, search_score, page_score, business_score, confidence)

        label = group.get("opportunity_label") or _human_label(key)
        evidence = {
            "cluster": label,
            "opportunity_key": key,
            "product_area": group.get("product_area"),
            "application": group.get("application"),
            "buyer_intent": group.get("buyer_intent"),
            "substitute_theme": group.get("substitute_theme"),
            "source_clusters": group.get("source_clusters", []),
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
                {"metric_id": r.metric_id, "query": r.query, "page": r.page, "impressions": r.impressions, "clicks": r.clicks, "avg_position": r.avg_position, "evidence_quality": getattr(r, "_match_score", 0)}
                for r in sorted(gsc_rows, key=lambda r: r.impressions or 0, reverse=True)[:10]
            ],
            "top_ga4_pages": [
                {"metric_id": r.metric_id, "page_path": r.page_path, "page_title": r.page_title, "sessions": r.sessions, "active_users": r.active_users, "evidence_quality": getattr(r, "_match_score", 0)}
                for r in sorted(ga4_rows, key=lambda r: r.sessions or 0, reverse=True)[:10]
            ],
            "best_existing_page": best_page,
            "linked_prompt_ids": [p.prompt_id for p in plist],
            "linked_prompt_texts": [p.prompt_text for p in plist],
            "prompts_to_rerun": [p.prompt_id for p in plist if p.monitor_status in ("Gap", "Risk")][:12],
            "evidence_quality": evidence_quality,
            "page_match_score": best_page.get("page_match_score") if best_page else 0,
            "target_page_confidence": best_page.get("target_page_confidence") if best_page else 0,
            "filtered_out_evidence_count": filtered_out_count,
            "filtered_out_evidence": (filtered_gsc + filtered_ga4)[:10],
            "priority_components": weights.__dict__ | {"priority_score": weights.priority_score},
        }
        evidence["failure_modes"] = _failure_modes(evidence)
        evidence["opportunity_type"] = _opportunity_type(evidence)
        evidence["opportunity_title"] = _opportunity_title(evidence["opportunity_type"], label, best_page, evidence)
        evidence["success_metric"] = _success_metric(evidence)
        out.append(evidence)

    return sorted(out, key=lambda e: e["priority_components"]["priority_score"], reverse=True)


def get_cluster_evidence(db: Session, cluster: str) -> dict | None:
    for item in build_cluster_evidence(db):
        if item["cluster"] == cluster:
            return item
    return None
