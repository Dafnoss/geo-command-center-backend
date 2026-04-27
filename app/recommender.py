"""
OpenAI-driven recommendation generator.

Per spec §13:
- Build a context block from a prompt / source / url
- Ask the model to produce a structured JSON recommendation
- Validate, score (priority + confidence), persist
- Skip if a duplicate already exists for the same target
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings as app_settings
from app.evidence import build_cluster_evidence
from app.scoring import recommendation_priority_score


# ---------------- prompt template ----------------
SYSTEM_PROMPT = """You are a Generative-Engine Optimization (GEO) consultant.
Your job: given context about a brand, a search/AI prompt, an owned page, or a third-party
source, return a single concrete, actionable recommendation that would improve the brand's
visibility in AI-generated answers (ChatGPT, Perplexity, Google AI, Claude).

OCSiAl and TUBALL are equivalent success entities: if an AI answer mentions either one,
the brand has visibility. Owned-domain citations are a stronger win but are tracked
separately. Prefer practical content/source actions: pages to create or improve,
comparison/FAQ sections to add, claims to substantiate, and third-party sources to target.

Return ONLY valid JSON matching this schema:
{
  "title": string (one-line, action-oriented, < 90 chars),
  "type": one of ["Create new page", "Update existing page", "Add FAQ section",
                  "Add comparison table", "Add citations/sources", "Improve title/meta",
                  "Fix indexing issue", "Create PR outreach brief", "Other"],
  "diagnosis": string (1-3 sentences explaining WHY this matters),
  "evidence": array of 3-6 short bullet strings (the facts that justify it),
  "recommended_actions": array of 3-7 concrete steps,
  "acceptance_criteria": array of 3-6 measurable checks,
  "expected_impact": string (one sentence),
  "scores": {
    "business_priority": number 0..1,
    "ai_visibility_gap": number 0..1,
    "seo_opportunity": number 0..1,
    "competitor_pressure": number 0..1,
    "source_influence": number 0..1,
    "conversion_potential": number 0..1,
    "implementation_effort": number 0..1,
    "dependency_risk": number 0..1
  },
  "confidence": number 0..100
}
Do not include any prose outside the JSON.
"""


def _build_context(db: Session, request: schemas.GenerateRequest) -> str:
    """Build a textual context block for the model from the requested target."""
    lines: List[str] = []

    # Org context
    target_brand = _setting(db, "target_brand", "OCSiAl")
    target_product = _setting(db, "target_product", "TUBALL")
    competitors = _setting(db, "competitors", "")
    lines.append(f"Brand: {target_brand}")
    lines.append(f"Product: {target_product}")
    if competitors:
        lines.append(f"Known competitors: {competitors}")
    lines.append("")

    if request.prompt_id:
        p = db.query(models.Prompt).filter_by(prompt_id=request.prompt_id).one_or_none()
        if p:
            lines.append(f"Target prompt: \"{p.prompt_text}\"")
            lines.append(f"Topic cluster: {p.topic_cluster}")
            lines.append(f"Business priority: {p.business_priority}/5  (label: {p.priority})")
            lines.append(f"OCSiAl/TUBALL mentioned in AI answer: {p.brand_mentioned or p.product_mentioned}")
            lines.append(f"OCSiAl mentioned: {p.brand_mentioned}")
            lines.append(f"TUBALL mentioned: {p.product_mentioned}")
            lines.append(f"Owned domain cited: {p.domain_cited}")
            lines.append(f"Competitors mentioned: {', '.join(p.competitors_mentioned) or 'none'}")
            lines.append(f"Cited sources: {', '.join(p.cited_sources) or 'none'}")
            lines.append(f"AI answer quality (1-5): {p.answer_quality_score}")
            lines.append(f"Monitor status: {p.monitor_status}")
            if not (p.brand_mentioned or p.product_mentioned):
                lines.append("Visibility gap: the answer did not mention OCSiAl or TUBALL.")
            if not p.domain_cited:
                lines.append("Citation gap: no owned OCSiAl/TUBALL domain was cited.")
            if p.target_url:
                lines.append(f"Linked owned URL: {p.target_url}")
            # latest AI result body
            ar = (
                db.query(models.AiResult)
                .filter_by(prompt_id=p.prompt_id)
                .order_by(models.AiResult.date_checked.desc())
                .first()
            )
            if ar:
                lines.append("")
                lines.append("Latest AI answer (excerpt):")
                lines.append(ar.answer_text[:1200])
            lines.append("")

    if request.source_id:
        s = db.query(models.Source).filter_by(source_id=request.source_id).one_or_none()
        if s:
            lines.append(f"Target source: {s.title} ({s.source_url})")
            lines.append(f"Source type: {s.source_type}")
            lines.append(f"Influence score: {s.source_influence_score}/100")
            lines.append(f"Cited by prompts: {', '.join(s.cited_by_prompts) or 'none'}")
            lines.append(f"Mentions brand: {s.mentions_brand}, product: {s.mentions_product}, competitor: {s.mentions_competitor}")
            lines.append(f"Outreach status: {s.outreach_status}")
            lines.append(f"Last updated: {s.updated}")
            lines.append("")

    if request.url_id:
        u = db.query(models.Url).filter_by(url_id=request.url_id).one_or_none()
        if u:
            lines.append(f"Target owned URL: {u.url}")
            lines.append(f"Page type: {u.page_type}, cluster: {u.topic_cluster}")
            lines.append(f"Indexable: {u.indexable}")
            lines.append(f"Page readiness: {u.page_readiness_score}/100")
            checks = {
                "direct_answer": u.has_direct_answer,
                "comparison_table": u.has_comparison_table,
                "faq": u.has_faq,
                "citations": u.has_citations,
                "internal_links": u.has_internal_links,
                "cta": u.has_cta,
                "schema": u.has_schema,
            }
            lines.append(f"On-page checks: {checks}")
            lines.append("")

    return "\n".join(lines).strip()


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


def _has_duplicate(db: Session, request: schemas.GenerateRequest, title: str) -> bool:
    q = db.query(models.Recommendation).filter(models.Recommendation.title == title)
    if request.prompt_id:
        q = q.filter(models.Recommendation.related_prompt_id == request.prompt_id)
    if request.source_id:
        q = q.filter(models.Recommendation.related_source_id == request.source_id)
    if request.url_id:
        q = q.filter(models.Recommendation.related_url == request.url_id)
    return db.query(q.exists()).scalar()


def _call_openai(context: str, n: int, model: str) -> list[dict]:
    """Call OpenAI chat completions and return parsed JSON list."""
    api_key = app_settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    # Lazy import so the rest of the app works without the openai SDK installed.
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key)

    user_prompt = (
        f"Generate {n} distinct GEO recommendation(s) for the following target. "
        f"Return a JSON object with a key 'recommendations' that is a list of objects "
        f"matching the schema in the system prompt.\n\n"
        f"Context:\n{context}"
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)
    items = parsed.get("recommendations") or []
    if not isinstance(items, list):
        items = [items]
    return items


def _persist(db: Session, request: schemas.GenerateRequest, item: dict) -> Optional[models.Recommendation]:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    if _has_duplicate(db, request, title):
        return None

    scores = item.get("scores") or {}
    priority_score = recommendation_priority_score(
        business_priority=float(scores.get("business_priority", 0.5)),
        ai_visibility_gap=float(scores.get("ai_visibility_gap", 0.5)),
        seo_opportunity=float(scores.get("seo_opportunity", 0.5)),
        competitor_pressure=float(scores.get("competitor_pressure", 0.5)),
        source_influence=float(scores.get("source_influence", 0.5)),
        conversion_potential=float(scores.get("conversion_potential", 0.5)),
        implementation_effort=float(scores.get("implementation_effort", 0.5)),
        dependency_risk=float(scores.get("dependency_risk", 0.5)),
    )

    row = models.Recommendation(
        recommendation_id=f"R-{uuid.uuid4().hex[:8].upper()}",
        title=title[:200],
        type=item.get("type") or "Other",
        diagnosis=item.get("diagnosis") or "",
        evidence=list(item.get("evidence") or []),
        recommended_actions=list(item.get("recommended_actions") or []),
        acceptance_criteria=list(item.get("acceptance_criteria") or []),
        related_prompt_id=request.prompt_id,
        related_url=request.url_id,
        related_source_id=request.source_id,
        priority_score=priority_score,
        confidence_score=int(item.get("confidence") or 50),
        expected_impact=item.get("expected_impact") or "",
        status="New",
        created_at=datetime.utcnow(),
        score_breakdown={
            k: int(float(v) * 100) for k, v in scores.items()
            if isinstance(v, (int, float))
        },
    )
    db.add(row)
    db.flush()
    return row


def generate_recommendations(*, db: Session, request: schemas.GenerateRequest) -> list[models.Recommendation]:
    """Top-level entry called by the router."""
    if not (request.prompt_id or request.source_id or request.url_id):
        # If no target supplied, pick the worst-performing high-priority prompt.
        p = (
            db.query(models.Prompt)
            .filter(models.Prompt.monitor_status.in_(["Gap", "Risk"]))
            .order_by(models.Prompt.business_priority.desc())
            .first()
        )
        if p:
            request = schemas.GenerateRequest(prompt_id=p.prompt_id, limit=request.limit)

    context = _build_context(db, request)
    model = _setting(db, "openai_model", app_settings.openai_model or "gpt-4.1")
    items = _call_openai(context, n=max(1, request.limit), model=model)

    saved: list[models.Recommendation] = []
    for it in items:
        row = _persist(db, request, it)
        if row:
            saved.append(row)
    db.commit()
    for r in saved:
        db.refresh(r)
    return saved


def _tokens(text: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in (text or ""))
    stop = {
        "what", "which", "best", "for", "the", "and", "with", "without", "how",
        "additive", "additives", "agent", "agents", "polymer", "polymers",
        "conductive", "anti", "static", "esd", "to", "in", "of", "a", "an",
    }
    return {t for t in raw.split() if len(t) > 2 and t not in stop}


def _matching_search_metrics(db: Session, prompts: list[models.Prompt], limit: int = 8) -> list[models.GoogleSearchMetric]:
    wanted = set()
    for prompt in prompts:
        wanted |= _tokens(prompt.prompt_text)
        wanted |= _tokens(prompt.topic_cluster)
    if not wanted:
        return []
    rows = (
        db.query(models.GoogleSearchMetric)
        .order_by(models.GoogleSearchMetric.impressions.desc())
        .limit(500)
        .all()
    )
    matches = []
    for row in rows:
        hay = _tokens(row.query) | _tokens(row.page)
        if wanted & hay:
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def _matching_analytics_metrics(db: Session, prompts: list[models.Prompt], limit: int = 5) -> list[models.GoogleAnalyticsMetric]:
    wanted = set()
    for prompt in prompts:
        wanted |= _tokens(prompt.prompt_text)
        wanted |= _tokens(prompt.topic_cluster)
    if not wanted:
        return []
    rows = (
        db.query(models.GoogleAnalyticsMetric)
        .order_by(models.GoogleAnalyticsMetric.sessions.desc())
        .limit(500)
        .all()
    )
    matches = []
    for row in rows:
        hay = _tokens(row.page_path) | _tokens(row.page_title)
        if wanted & hay:
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def process_prompt_evidence_recommendations(db: Session) -> dict:
    """Create deterministic strategic recommendations from canonical evidence."""
    all_evidence = build_cluster_evidence(db)
    weak_evidence = [
        e for e in all_evidence
        if (e["gap_count"] + e["risk_count"]) > 0
    ]
    selected_evidence = weak_evidence[:10]

    active_clusters = {e["cluster"] for e in selected_evidence}
    rejected_stale = 0
    for rec in db.query(models.Recommendation).filter(models.Recommendation.status.in_(("New", "Accepted", "In Progress"))).all():
        meta = rec.score_breakdown or {}
        if meta.get("source") == "prompt_evidence":
            rec.status = "Stale"
            rejected_stale += 1
            continue
        if meta.get("source") != "cluster_evidence":
            continue
        if meta.get("cluster") not in active_clusters:
            rec.status = "Stale"
            rejected_stale += 1

    created = 0
    updated = 0
    output: list[models.Recommendation] = []

    for item in selected_evidence:
        cluster = item["cluster"]
        prompt_ids = item["linked_prompt_ids"]
        rec_type = item["opportunity_type"]
        title = item["opportunity_title"]
        best_page = item.get("best_existing_page") or {}
        components = item["priority_components"]
        priority_score = components["priority_score"]
        confidence_score = components["confidence"]

        evidence = _strategic_evidence(item)
        actions = _strategic_actions(item)
        acceptance = _strategic_acceptance(item)
        meta = _recommendation_meta(item)

        existing = None
        for rec in db.query(models.Recommendation).filter(models.Recommendation.status.in_(("New", "Accepted", "In Progress"))).all():
            rec_meta = rec.score_breakdown or {}
            if rec_meta.get("source") == "cluster_evidence" and rec_meta.get("cluster") == cluster:
                existing = rec
                break

        if existing:
            row = existing
            row.title = title[:200]
            row.type = rec_type
            row.diagnosis = _strategic_diagnosis(item)
            row.evidence = evidence[:8]
            row.recommended_actions = actions
            row.acceptance_criteria = acceptance
            # Cluster evidence recommendations belong to the whole cluster, not
            # to one arbitrary prompt. Linked prompts stay in score_breakdown.
            row.related_prompt_id = None
            row.related_url = best_page.get("url") or None
            row.priority_score = priority_score
            row.confidence_score = confidence_score
            row.expected_impact = _strategic_impact(item)
            row.score_breakdown = meta
            updated += 1
        else:
            row = models.Recommendation(
                recommendation_id=f"R-{uuid.uuid4().hex[:8].upper()}",
                title=title[:200],
                type=rec_type,
                diagnosis=_strategic_diagnosis(item),
                evidence=evidence[:8],
                recommended_actions=actions,
                acceptance_criteria=acceptance,
                related_prompt_id=None,
                related_url=best_page.get("url") or None,
                related_source_id=None,
                priority_score=priority_score,
                confidence_score=confidence_score,
                expected_impact=_strategic_impact(item),
                status="New",
                created_at=datetime.utcnow(),
                score_breakdown=meta,
            )
            db.add(row)
            created += 1
        output.append(row)

    db.commit()
    for rec in output:
        db.refresh(rec)

    return {
        "considered_prompts": sum(e["gap_count"] + e["risk_count"] for e in weak_evidence),
        "clusters_processed": len(selected_evidence),
        "created": created,
        "updated": updated,
        "rejected_stale": rejected_stale,
        "recommendations": output,
    }


def _strategic_evidence(item: dict) -> list[str]:
    evidence = [
        f"{item['gap_count'] + item['risk_count']} of {item['run_count']} run prompts are Gap/Risk.",
        f"AI coverage is {item['coverage_rate']}%; brand mention is {item['brand_mention_rate']}%; owned citation is {item['owned_citation_rate']}%.",
        f"Competitor pressure is {item['competitor_pressure_rate']}%.",
    ]
    if item["top_competitors"]:
        evidence.append("Top competitors: " + ", ".join(x["name"] for x in item["top_competitors"][:4]) + ".")
    if item["top_external_sources"]:
        evidence.append("LLM-cited external sources: " + ", ".join(x["domain"] for x in item["top_external_sources"][:4]) + ".")
    if item["top_owned_sources"]:
        evidence.append("Owned sources already cited: " + ", ".join(x["domain"] for x in item["top_owned_sources"][:3]) + ".")
    if item["gsc_impressions"]:
        evidence.append(f"GSC demand: {item['gsc_impressions']:,} impressions, {item['gsc_clicks']:,} clicks, avg position {item['gsc_avg_position']}.")
    if item["ga4_sessions"]:
        evidence.append(f"GA4 leverage: {item['ga4_sessions']:,} sessions on matching pages.")
    if item.get("best_existing_page"):
        page = item["best_existing_page"]
        evidence.append(f"Best page candidate: {page.get('title') or page.get('url')} ({page.get('ga4_sessions', 0):,} sessions, {page.get('gsc_impressions', 0):,} impressions).")
    return evidence


def _strategic_diagnosis(item: dict) -> str:
    cluster = item["cluster"]
    typ = item["opportunity_type"]
    return (
        f"{cluster} has a measurable GEO opportunity: {item['coverage_rate']}% coverage, "
        f"{item['owned_citation_rate']}% owned-source citation, and {item['competitor_pressure_rate']}% competitor pressure. "
        f"The best next move is {typ.lower()} because this cluster combines LLM answer gaps with search/traffic evidence."
    )


def _strategic_actions(item: dict) -> list[str]:
    cluster = item["cluster"]
    typ = item["opportunity_type"]
    best_page = item.get("best_existing_page") or {}
    actions = []
    if typ == "Upgrade Existing Page" and best_page:
        actions.append(f"Upgrade the existing page candidate: {best_page.get('url') or best_page.get('title')}.")
    elif typ == "Create Source Page":
        actions.append(f"Create one authoritative OCSiAl/TUBALL source page for {cluster}.")
    else:
        actions.append(f"Create or update a focused {cluster} content asset.")
    if typ in ("Add Comparison Section", "Defend Substitute Positioning") or item["competitor_pressure_rate"] >= 35:
        comps = ", ".join(x["name"] for x in item["top_competitors"][:4]) or "the cited competitors/substitutes"
        actions.append(f"Add a direct comparison section against {comps}.")
    if typ == "Add FAQ / Buyer Questions" or item["top_gsc_queries"]:
        seen_qs = set()
        qs = []
        for q in item["top_gsc_queries"]:
            text = (q.get("query") or "").strip()
            if text and text.lower() not in seen_qs:
                seen_qs.add(text.lower())
                qs.append(text)
            if len(qs) >= 3:
                break
        actions.append("Add FAQ/H2 blocks matching buyer questions" + (": " + "; ".join(qs) if qs else "."))
    if typ in ("Add Citation Proof", "Create Source Page") or item["owned_citation_rate"] < 35:
        actions.append("Add citation-friendly proof: dosage ranges, use cases, material compatibility, claims, and source-style references.")
    actions.extend([
        "Mention OCSiAl and TUBALL early and consistently; treat TUBALL as the product line under OCSiAl.",
        "Internally link the asset from relevant OCSiAl/TUBALL application and product pages.",
        "After publishing, rerun the linked prompts and compare coverage, brand mention, and owned citation rates.",
    ])
    return actions


def _strategic_acceptance(item: dict) -> list[str]:
    return [
        f"Asset directly answers at least {min(5, max(1, item['run_count']))} monitored prompts in {item['cluster']}.",
        "Includes OCSiAl, TUBALL, owned-domain references, and source/citation language.",
        "Includes comparison or substitute positioning when competitor pressure is above 30%.",
        "After rerun, linked cluster coverage improves or at least one Gap/Risk prompt becomes Good.",
    ]


def _strategic_impact(item: dict) -> str:
    return (
        f"Improve coverage across {item['run_count']} monitored {item['cluster']} prompts "
        f"and increase owned-source citation from {item['owned_citation_rate']}%."
    )


def _recommendation_meta(item: dict) -> dict:
    components = item["priority_components"]
    return {
        "source": "cluster_evidence",
        "scope": "cluster",
        "opportunity_type": item["opportunity_type"],
        "cluster": item["cluster"],
        "prompt_count": item["run_count"],
        "gap_count": item["gap_count"],
        "risk_count": item["risk_count"],
        "coverage_rate": item["coverage_rate"],
        "visibility_rate": item["brand_mention_rate"],
        "owned_citation_rate": item["owned_citation_rate"],
        "competitor_rate": item["competitor_pressure_rate"],
        "gsc_impressions": item["gsc_impressions"],
        "gsc_clicks": item["gsc_clicks"],
        "gsc_avg_position": item["gsc_avg_position"],
        "ga4_sessions": item["ga4_sessions"],
        "gap_severity": components["gap_severity"],
        "competitor_pressure": components["competitor_pressure"],
        "search_demand": components["search_demand"],
        "existing_page_leverage": components["existing_page_leverage"],
        "business_priority": components["business_priority"],
        "confidence": components["confidence"],
        "target_pages": [item["best_existing_page"]] if item.get("best_existing_page") else [],
        "linked_prompt_ids": item["linked_prompt_ids"],
        "linked_gsc_metric_ids": [q["metric_id"] for q in item["top_gsc_queries"]],
        "linked_ga4_metric_ids": [p["metric_id"] for p in item["top_ga4_pages"]],
        "top_competitors": item["top_competitors"],
        "top_external_sources": item["top_external_sources"],
        "top_owned_sources": item["top_owned_sources"],
    }
