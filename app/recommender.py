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
from collections import Counter, defaultdict
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings as app_settings
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


def _call_openai(context: str, n: int) -> list[dict]:
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
        model=app_settings.openai_model,
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
    items = _call_openai(context, n=max(1, request.limit))

    saved: list[models.Recommendation] = []
    for it in items:
        row = _persist(db, request, it)
        if row:
            saved.append(row)
    db.commit()
    for r in saved:
        db.refresh(r)
    return saved


def process_prompt_evidence_recommendations(db: Session) -> dict:
    """Create consolidated recommendations from all monitored prompt evidence.

    This is the strategic recommendation engine for the MVP. It deliberately uses
    prompt evidence only, keeps history by rejecting stale rows instead of
    deleting, and prefers cluster-level actions over one recommendation per prompt.
    """
    run_prompts = (
        db.query(models.Prompt)
        .filter(models.Prompt.monitor_status.in_(("Gap", "Risk")))
        .all()
    )

    groups: dict[str, list[models.Prompt]] = defaultdict(list)
    for prompt in run_prompts:
        groups[prompt.topic_cluster or "Uncategorized"].append(prompt)

    active_clusters = set(groups.keys())
    rejected_stale = 0
    for rec in db.query(models.Recommendation).filter(models.Recommendation.status == "New").all():
        meta = rec.score_breakdown or {}
        if rec.related_prompt_id and meta.get("source") != "prompt_evidence":
            rec.status = "Rejected"
            rejected_stale += 1
            continue
        if meta.get("source") != "prompt_evidence":
            continue
        if meta.get("scope") == "cluster" and meta.get("cluster") not in active_clusters:
            rec.status = "Rejected"
            rejected_stale += 1
        if rec.related_prompt_id:
            prompt = db.query(models.Prompt).filter_by(prompt_id=rec.related_prompt_id).one_or_none()
            if not prompt or prompt.monitor_status == "Good":
                rec.status = "Rejected"
                rejected_stale += 1

    created = 0
    updated = 0
    output: list[models.Recommendation] = []

    sorted_groups = sorted(
        groups.items(),
        key=lambda item: (
            sum(1 for p in item[1] if p.monitor_status == "Gap"),
            sum(1 for p in item[1] if p.monitor_status == "Risk"),
            sum(p.business_priority for p in item[1]),
        ),
        reverse=True,
    )

    for cluster, prompts in sorted_groups[:10]:
        total = len(prompts)
        gaps = [p for p in prompts if p.monitor_status == "Gap"]
        risks = [p for p in prompts if p.monitor_status == "Risk"]
        cited = [p for p in prompts if p.domain_cited]
        visible = [p for p in prompts if p.brand_mentioned or p.product_mentioned]
        top_prompt = sorted(prompts, key=lambda p: (p.business_priority, p.answer_quality_score), reverse=True)[0]
        competitors = Counter(c for p in prompts for c in (p.competitors_mentioned or []))
        sources = Counter(s for p in prompts for s in (p.cited_sources or []))

        visibility_rate = round(len(visible) / total * 100) if total else 0
        citation_rate = round(len(cited) / total * 100) if total else 0
        competitor_rate = round(len(risks) / total * 100) if total else 0
        priority_score = min(95, 45 + len(gaps) * 6 + len(risks) * 4 + max(p.business_priority for p in prompts) * 5)

        if risks and competitors:
            rec_type = "Add comparison table"
            title = (
                "Strengthen comparison content against cited competitors"
                if cluster.strip().lower() == "comparison"
                else f"Add {cluster} competitor comparison content"
            )
        elif citation_rate == 0:
            rec_type = "Add citations/sources"
            title = f"Make OCSiAl/TUBALL citable for {cluster}"
        else:
            rec_type = "Create new page"
            title = f"Create an OCSiAl/TUBALL answer hub for {cluster}"

        evidence = [
            f"{total} run prompts in this cluster are still Gap/Risk.",
            f"{len(gaps)} Gap prompts do not mention OCSiAl/TUBALL.",
            f"{len(risks)} Risk prompts mention competitors without enough OCSiAl/TUBALL visibility.",
            f"Owned domains are cited in {citation_rate}% of these prompts.",
        ]
        if competitors:
            evidence.append("Top competitors appearing: " + ", ".join(c for c, _ in competitors.most_common(4)) + ".")
        if sources:
            evidence.append("LLM-cited sources include: " + ", ".join(s for s, _ in sources.most_common(4)) + ".")

        actions = [
            f"Create or update one authoritative {cluster} page that directly answers the highest-priority prompts.",
            "Name OCSiAl and TUBALL early, then explain why graphene nanotubes/SWCNT matter for the use case.",
            "Add a concise comparison section against the most-cited substitutes and competitors.",
            "Add quotable technical claims, application examples, and source-style references that LLMs can reuse.",
            "Link the page from relevant OCSiAl/TUBALL pages and rerun this cluster after publishing.",
        ]
        acceptance = [
            "Page directly answers at least five monitored prompts from this cluster.",
            "Includes OCSiAl, TUBALL, owned-domain references, and comparison language.",
            "After rerun, at least one prompt in the cluster moves from Gap/Risk to Good.",
        ]
        meta = {
            "source": "prompt_evidence",
            "scope": "cluster",
            "cluster": cluster,
            "prompt_count": total,
            "gap_count": len(gaps),
            "risk_count": len(risks),
            "owned_citation_rate": citation_rate,
            "visibility_rate": visibility_rate,
            "competitor_rate": competitor_rate,
        }

        existing = None
        for rec in db.query(models.Recommendation).filter(models.Recommendation.status == "New").all():
            rec_meta = rec.score_breakdown or {}
            if rec_meta.get("source") == "prompt_evidence" and rec_meta.get("scope") == "cluster" and rec_meta.get("cluster") == cluster:
                existing = rec
                break

        if existing:
            row = existing
            row.title = title[:200]
            row.type = rec_type
            row.diagnosis = (
                f"{cluster} is underperforming in monitored AI answers: visibility is {visibility_rate}% "
                f"and owned citation rate is {citation_rate}%. This is a strategic content/source gap, not just one bad prompt."
            )
            row.evidence = evidence[:6]
            row.recommended_actions = actions
            row.acceptance_criteria = acceptance
            row.related_prompt_id = top_prompt.prompt_id
            row.priority_score = priority_score
            row.confidence_score = 80 if total >= 3 else 65
            row.expected_impact = f"Improve OCSiAl/TUBALL visibility across {total} related monitored prompts."
            row.score_breakdown = meta
            updated += 1
        else:
            row = models.Recommendation(
                recommendation_id=f"R-{uuid.uuid4().hex[:8].upper()}",
                title=title[:200],
                type=rec_type,
                diagnosis=(
                    f"{cluster} is underperforming in monitored AI answers: visibility is {visibility_rate}% "
                    f"and owned citation rate is {citation_rate}%. This is a strategic content/source gap, not just one bad prompt."
                ),
                evidence=evidence[:6],
                recommended_actions=actions,
                acceptance_criteria=acceptance,
                related_prompt_id=top_prompt.prompt_id,
                related_url=None,
                related_source_id=None,
                priority_score=priority_score,
                confidence_score=80 if total >= 3 else 65,
                expected_impact=f"Improve OCSiAl/TUBALL visibility across {total} related monitored prompts.",
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
        "considered_prompts": len(run_prompts),
        "clusters_processed": len(sorted_groups[:10]),
        "created": created,
        "updated": updated,
        "rejected_stale": rejected_stale,
        "recommendations": output,
    }
