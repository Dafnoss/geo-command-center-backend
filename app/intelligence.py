"""
Market intelligence engine for generating a reviewable prompt portfolio.

The engine researches OCSiAl/TUBALL context with OpenAI Responses + web search,
validates the model output, stores a draft batch, and imports only approved
non-duplicate drafts into the live prompt table.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings as app_settings


INTENT_TYPES = {
    "category education",
    "comparison",
    "supplier/vendor",
    "application/use-case",
    "safety/regulatory",
    "substitute/alternative",
}

REQUIRED_INTENTS = [
    "category education",
    "comparison",
    "supplier/vendor",
    "application/use-case",
    "safety/regulatory",
    "substitute/alternative",
]

FALLBACK_QUERIES = [
    ("What are graphene nanotubes?", "Category education", "category education", 4, "Core educational question for buyers learning the category."),
    ("Graphene nanotubes vs carbon black", "Comparison", "comparison", 5, "High-intent comparison against the most common conductive filler."),
    ("Single-wall carbon nanotubes vs multi-wall carbon nanotubes", "Comparison", "comparison", 4, "Clarifies where SWCNTs differ from MWCNT alternatives."),
    ("Best conductive additive for epoxy", "Coatings and resins", "application/use-case", 5, "Epoxy is a practical application where additive choice matters."),
    ("Best conductive additive for silicone", "Elastomers", "application/use-case", 4, "Silicone use cases map to TUBALL MATRIX positioning."),
    ("Conductive additive for non-black plastics", "Thermoplastics", "application/use-case", 5, "Tests whether AI recognizes low-loading color-preserving additives."),
    ("SWCNT for lithium-ion batteries", "Batteries", "application/use-case", 5, "Battery additives are a strategic TUBALL BATT use case."),
    ("Graphene nanotubes for EV batteries", "Batteries", "application/use-case", 5, "Captures EV-battery discovery language."),
    ("Are carbon nanotubes safe?", "Safety and regulation", "safety/regulatory", 5, "Safety questions often shape buyer confidence."),
    ("REACH registered single-wall carbon nanotubes", "Safety and regulation", "safety/regulatory", 5, "Regulatory status is a differentiator for industrial buyers."),
    ("Carbon black alternative for conductive polymers", "Substitutes", "substitute/alternative", 5, "Targets replacement demand for traditional fillers."),
    ("Conductive additive for transparent coatings", "Coatings and resins", "application/use-case", 4, "Transparent coatings require low-loading conductive additives."),
    ("Anti-static additive for rubber", "Elastomers", "application/use-case", 4, "Rubber antistatic use cases are commercially relevant."),
    ("Conductive additive for SMC composites", "Composites", "application/use-case", 3, "SMC composites represent technical buyer searches."),
    ("Best suppliers of single-wall carbon nanotubes", "Suppliers", "supplier/vendor", 5, "Supplier prompts expose vendor visibility and competitors."),
    ("TUBALL vs carbon black", "Comparison", "comparison", 5, "Direct product-vs-substitute comparison."),
    ("OCSiAl graphene nanotubes", "Brand and product", "category education", 5, "Direct brand/product discovery prompt."),
    ("TUBALL conductive additive", "Brand and product", "category education", 5, "Direct product discovery prompt."),
    ("How to reduce conductive filler loading", "Substitutes", "substitute/alternative", 4, "Targets a key benefit claim without naming the product."),
    ("Conductive additive for cleanroom gloves", "Elastomers", "application/use-case", 3, "Specific industrial application for antistatic elastomers."),
    ("Conductive additive for conveyor belts", "Elastomers", "application/use-case", 3, "Specific industrial rubber application."),
    ("Conductive coatings for tanks", "Coatings and resins", "application/use-case", 3, "Industrial coating search with safety/performance intent."),
    ("SWCNT for epoxy coatings", "Coatings and resins", "application/use-case", 4, "Specific SWCNT application prompt."),
    ("Graphene nanotubes for tires", "Tires", "application/use-case", 4, "Tire and rubber applications are visible in market messaging."),
    ("Graphene nanotube concentrate for plastics", "Thermoplastics", "application/use-case", 4, "Captures TUBALL MATRIX-style product language."),
]


def normalize_query(text: str) -> str:
    """Normalize prompt text for duplicate detection."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


def _next_prompt_id(db: Session) -> str:
    ids = [r[0] for r in db.query(models.Prompt.prompt_id).filter(models.Prompt.prompt_id.like("P%")).all()]
    nums = []
    for pid in ids:
        try:
            nums.append(int(pid[1:]))
        except (TypeError, ValueError):
            continue
    return "P" + str((max(nums) if nums else 0) + 1).zfill(3)


def _priority_label(n: int) -> str:
    if n >= 5:
        return "High"
    if n >= 3:
        return "Medium"
    return "Low"


def _existing_query_norms(db: Session) -> set[str]:
    return {
        normalize_query(row[0])
        for row in db.query(models.Prompt.prompt_text).all()
        if normalize_query(row[0])
    }


def _draft_query_norms(db: Session, batch_id: str | None = None) -> set[str]:
    q = db.query(models.PromptDraft.query_text)
    if batch_id:
        q = q.filter(models.PromptDraft.batch_id == batch_id)
    return {normalize_query(row[0]) for row in q.all() if normalize_query(row[0])}


def _coerce_intent(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in INTENT_TYPES:
        return value
    if "supplier" in value or "vendor" in value:
        return "supplier/vendor"
    if "regulat" in value or "safe" in value:
        return "safety/regulatory"
    if "substitute" in value or "alternative" in value:
        return "substitute/alternative"
    if "compare" in value or "vs" in value:
        return "comparison"
    if "application" in value or "use" in value:
        return "application/use-case"
    return "category education"


def _clean_competitors(items: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for item in items or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name[:120],
            "domain": str(item.get("domain") or "").strip()[:200],
            "reason": str(item.get("reason") or "").strip()[:500],
        })
    return out[:20]


def _clean_sources(items: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for item in items or []:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url and not title:
            continue
        key = (url or title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "url": url[:500],
            "title": title[:220],
        })
    return out[:25]


def _validate_payload(payload: dict, count: int, existing_norms: set[str]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("OpenAI returned a non-object payload")

    drafts = payload.get("drafts")
    if not isinstance(drafts, list):
        raise ValueError("OpenAI payload is missing drafts[]")
    if not drafts:
        raise ValueError("OpenAI payload contains no draft queries")

    clean_drafts = []
    seen = set(existing_norms)
    for item in drafts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("query_text") or "").strip()
        norm = normalize_query(text)
        if not text or not norm or norm in seen:
            continue
        intent = _coerce_intent(str(item.get("intent_type") or ""))
        try:
            priority = int(item.get("business_priority") or 3)
        except (TypeError, ValueError):
            priority = 3
        priority = max(1, min(5, priority))
        clean_drafts.append({
            "query_text": text[:300],
            "topic_cluster": str(item.get("topic_cluster") or "Uncategorized").strip()[:120],
            "intent_type": intent,
            "business_priority": priority,
            "reason": str(item.get("reason") or "").strip()[:700],
        })
        seen.add(norm)
        if len(clean_drafts) >= count:
            break

    if not clean_drafts:
        raise ValueError("OpenAI payload contains no valid draft queries")

    for text, cluster, intent, priority, reason in FALLBACK_QUERIES:
        if len(clean_drafts) >= count:
            break
        norm = normalize_query(text)
        if norm in seen:
            continue
        clean_drafts.append({
            "query_text": text,
            "topic_cluster": cluster,
            "intent_type": intent,
            "business_priority": priority,
            "reason": reason,
        })
        seen.add(norm)

    present_intents = {d["intent_type"] for d in clean_drafts}
    missing = [intent for intent in REQUIRED_INTENTS if intent not in present_intents]
    if len(clean_drafts) < count:
        raise ValueError(f"Only {len(clean_drafts)} valid drafts generated; expected {count}")
    if missing:
        raise ValueError("Generated draft set is missing required intent types: " + ", ".join(missing))

    return {
        "market_summary": str(payload.get("market_summary") or "").strip()[:4000],
        "applications": [str(x).strip()[:120] for x in (payload.get("applications") or []) if str(x).strip()][:20],
        "competitor_candidates": _clean_competitors(payload.get("competitor_candidates") or []),
        "sources": _clean_sources(payload.get("sources") or []),
        "drafts": clean_drafts,
    }


def _portfolio_schema() -> dict:
    draft = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query_text": {"type": "string"},
            "topic_cluster": {"type": "string"},
            "intent_type": {
                "type": "string",
                "enum": sorted(INTENT_TYPES),
            },
            "business_priority": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["query_text", "topic_cluster", "intent_type", "business_priority", "reason"],
    }
    competitor = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "domain": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["name", "domain", "reason"],
    }
    source = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["url", "title"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "market_summary": {"type": "string"},
            "applications": {"type": "array", "items": {"type": "string"}},
            "competitor_candidates": {"type": "array", "items": competitor},
            "sources": {"type": "array", "items": source},
            "drafts": {"type": "array", "items": draft},
        },
        "required": ["market_summary", "applications", "competitor_candidates", "sources", "drafts"],
    }


def _build_research_prompt(db: Session, count: int) -> str:
    brand = _setting(db, "target_brand", "OCSiAl")
    product = _setting(db, "target_product", "TUBALL")
    owned = _setting(db, "owned_domains", "ocsial.com,tuball.com")
    competitors = _setting(db, "competitors", "")
    existing = [row[0] for row in db.query(models.Prompt.prompt_text).all()]
    existing_text = "\n".join(f"- {x}" for x in existing[:100]) or "- none"
    return f"""
Generate a reviewable GEO/AI-search prompt portfolio for an industrial materials company.

Brand: {brand}
Product/product family: {product}
Owned domains: {owned}
Known competitors/substitutes from settings: {competitors or "none"}

Use web research to understand:
- what {brand} and {product} are,
- competitor companies and substitute technologies,
- important applications and buyer questions,
- safety/regulatory questions,
- supplier/vendor discovery queries.

Return exactly {count} draft queries. They must be short natural-language prompts a buyer might ask ChatGPT/Perplexity/Google AI.
The set must include every intent type at least once:
{", ".join(REQUIRED_INTENTS)}.

Do not duplicate these existing live queries:
{existing_text}

Return JSON only matching the schema. Keep reasons concise and practical.
"""


def _extract_response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_sources(data: dict) -> list[dict]:
    out = []
    for item in data.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for src in action.get("sources") or []:
                out.append({"url": src.get("url", ""), "title": src.get("title", "")})
        if item.get("type") == "message":
            for content in item.get("content") or []:
                for ann in content.get("annotations") or []:
                    if ann.get("type") == "url_citation":
                        out.append({"url": ann.get("url", ""), "title": ann.get("title", "")})
    return _clean_sources(out)


def _call_responses_api(db: Session, count: int) -> dict:
    api_key = app_settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model = _setting(db, "research_model", "gpt-4.1-mini")
    body = {
        "model": model,
        "instructions": (
            "You are a B2B GEO/search strategy analyst for advanced materials. "
            "Use web search when helpful. Return JSON only."
        ),
        "input": _build_research_prompt(db, count),
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "geo_prompt_portfolio",
                "strict": True,
                "schema": _portfolio_schema(),
            }
        },
    }

    with httpx.Client(timeout=90.0) as client:
        resp = client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI Responses API error {resp.status_code}: {resp.text[:800]}")

    data = resp.json()
    text = _extract_response_text(data)
    if not text:
        raise ValueError("OpenAI returned no text output")
    parsed = json.loads(text)
    extracted_sources = _extract_sources(data)
    if extracted_sources:
        parsed["sources"] = _clean_sources((parsed.get("sources") or []) + extracted_sources)
    return parsed


def generate_drafts(db: Session, count: int = 25) -> schemas.DraftBatchOut:
    count = max(10, min(50, int(count or 25)))
    payload = _call_responses_api(db, count=count)
    clean = _validate_payload(payload, count=count, existing_norms=_existing_query_norms(db))

    batch_id = f"B-{uuid.uuid4().hex[:10].upper()}"
    context = models.MarketContext(
        context_id=f"MC-{uuid.uuid4().hex[:10].upper()}",
        batch_id=batch_id,
        generated_at=datetime.utcnow(),
        brand_summary=clean["market_summary"],
        competitor_candidates=clean["competitor_candidates"],
        application_areas=clean["applications"],
        source_citations=clean["sources"],
        raw_payload=payload,
    )
    db.add(context)

    for item in clean["drafts"]:
        db.add(models.PromptDraft(
            draft_id=f"PD-{uuid.uuid4().hex[:10].upper()}",
            batch_id=batch_id,
            query_text=item["query_text"],
            topic_cluster=item["topic_cluster"],
            intent_type=item["intent_type"],
            business_priority=item["business_priority"],
            reason=item["reason"],
            status="draft",
            created_at=datetime.utcnow(),
        ))
    db.commit()
    return latest_batch(db, batch_id=batch_id)


def latest_batch(db: Session, batch_id: str | None = None) -> schemas.DraftBatchOut:
    context_q = db.query(models.MarketContext)
    if batch_id:
        context_q = context_q.filter(models.MarketContext.batch_id == batch_id)
    context = context_q.order_by(models.MarketContext.generated_at.desc()).first()
    if not context:
        return schemas.DraftBatchOut(batch_id="", context=None, drafts=[], competitor_candidates=[])

    drafts = (
        db.query(models.PromptDraft)
        .filter(models.PromptDraft.batch_id == context.batch_id)
        .order_by(models.PromptDraft.business_priority.desc(), models.PromptDraft.created_at.asc())
        .all()
    )
    competitors = [
        schemas.CompetitorCandidate(**c)
        for c in _clean_competitors(context.competitor_candidates or [])
    ]
    return schemas.DraftBatchOut(
        batch_id=context.batch_id,
        context=context,
        drafts=drafts,
        competitor_candidates=competitors,
    )


def approve_drafts(db: Session, batch_id: str, draft_ids: list[str]) -> schemas.ApproveDraftsOut:
    allowed = set(draft_ids or [])
    if not allowed:
        raise ValueError("No draft IDs selected")

    drafts = (
        db.query(models.PromptDraft)
        .filter(models.PromptDraft.batch_id == batch_id)
        .filter(models.PromptDraft.draft_id.in_(allowed))
        .all()
    )
    existing = _existing_query_norms(db)
    imported: list[models.Prompt] = []
    skipped: list[str] = []

    for d in drafts:
        norm = normalize_query(d.query_text)
        if not norm or norm in existing or d.status == "imported":
            d.status = "skipped"
            skipped.append(d.draft_id)
            continue
        prompt = models.Prompt(
            prompt_id=_next_prompt_id(db),
            prompt_text=d.query_text,
            topic_cluster=d.topic_cluster,
            business_priority=d.business_priority,
            priority=_priority_label(d.business_priority),
            monitor_status="Unchecked",
            status="active",
        )
        db.add(prompt)
        db.flush()
        d.status = "imported"
        imported.append(prompt)
        existing.add(norm)

    db.commit()
    for p in imported:
        db.refresh(p)
    return schemas.ApproveDraftsOut(batch_id=batch_id, imported=imported, skipped=skipped)


def approve_competitors(db: Session, competitors: list[schemas.CompetitorCandidate]) -> schemas.ApproveCompetitorsOut:
    names = []
    seen = set()
    existing = [x.strip() for x in _setting(db, "competitors", "").split(",") if x.strip()]
    for name in existing + [c.name for c in competitors]:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            names.append(name)
    value = ",".join(names)
    row = db.query(models.Setting).filter_by(setting_key="competitors").one_or_none()
    if row:
        row.setting_value = value
    else:
        db.add(models.Setting(setting_key="competitors", setting_value=value, notes="Comma-separated competitor list."))
    db.commit()
    return schemas.ApproveCompetitorsOut(competitors=value)
