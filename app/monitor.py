"""
Query-monitoring engine.

For each prompt, send it to OpenAI as a normal user query, save the answer,
extract cited URLs into the Sources table, mark brand/competitor/domain flags,
and (when visibility is weak) auto-generate one recommendation.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, date
from typing import Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings as app_settings
from app.scoring import (
    ai_visibility_score,
    recommendation_priority_score,
)
from app.recommender import generate_recommendations


URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+\.[a-z0-9.-]+\.(?:com|org|net|io|co|gov|edu|eu|de|cn|jp|uk|fr|es|it|ru))\b", re.IGNORECASE)


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


def _list_setting(db: Session, key: str) -> list[str]:
    raw = _setting(db, key, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _model_pricing(model: str) -> Tuple[float, float]:
    """Return (input $/1M tokens, output $/1M tokens) for known models."""
    table = {
        "gpt-4o-mini":   (0.15, 0.60),
        "gpt-4o":        (2.50, 10.00),
        "gpt-4.1":       (3.00, 12.00),
        "gpt-4.1-mini":  (0.40, 1.60),
        "o1-mini":       (3.00, 12.00),
        "o3-mini":       (1.10, 4.40),
    }
    return table.get(model, (0.15, 0.60))


def _extract_urls(text: str) -> list[str]:
    """Pull out URLs and bare-domain references from an answer."""
    out: list[str] = []
    seen = set()
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:")
        if u not in seen:
            seen.add(u)
            out.append(u)
    for m in BARE_DOMAIN_RE.finditer(text or ""):
        u = m.group(1).lower()
        if u not in seen and not any(u in s for s in seen):
            seen.add(u)
            out.append("https://" + u)
    return out


def _domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def _detect(text: str, needles: list[str]) -> list[str]:
    """Return the subset of `needles` that appear (case-insensitive) in text."""
    if not text:
        return []
    low = text.lower()
    found = []
    for n in needles:
        if n and n.lower() in low:
            found.append(n)
    return found


def _check_monthly_cap(db: Session) -> bool:
    """Return True if we're under the cap (OK to spend), False if exceeded."""
    cap = float(_setting(db, "monthly_cost_cap_usd", "0") or 0)
    if cap <= 0:
        return True
    # sum cost from this calendar month
    from sqlalchemy import func, extract
    today = date.today()
    total = (
        db.query(func.coalesce(func.sum(models.UsageLog.cost_usd), 0.0))
        .filter(extract("year", models.UsageLog.run_at) == today.year)
        .filter(extract("month", models.UsageLog.run_at) == today.month)
        .scalar()
    )
    return float(total or 0.0) < cap


def _call_openai(prompt_text: str, model: str, api_key: str) -> dict:
    """Call OpenAI chat completion with the user's prompt as a normal query."""
    from openai import OpenAI  # type: ignore
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.5,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Answer the user's question accurately and concisely. When you reference companies, products, or sources, name them explicitly. When relevant, include URLs."},
            {"role": "user", "content": prompt_text},
        ],
    )
    answer = resp.choices[0].message.content or ""
    usage = resp.usage
    return {
        "answer": answer,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "model": model,
    }


def run_query(db: Session, prompt: models.Prompt) -> dict:
    """Run one query end-to-end. Returns a dict summary."""
    api_key = app_settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    if not _check_monthly_cap(db):
        raise RuntimeError("Monthly OpenAI cost cap reached. Raise it in Settings.")

    model = _setting(db, "openai_model", app_settings.openai_model or "gpt-4o-mini")
    target_brand   = _setting(db, "target_brand",   "OCSiAl")
    target_product = _setting(db, "target_product", "TUBALL")
    competitors    = _list_setting(db, "competitors")
    owned_domains  = [d.lower() for d in _list_setting(db, "owned_domains")]

    result = _call_openai(prompt.prompt_text, model, api_key)
    answer = result["answer"]

    # Parse mentions
    brand_in   = bool(_detect(answer, [target_brand]))
    product_in = bool(_detect(answer, [target_product]))
    comps      = _detect(answer, competitors)

    # Extract URLs/domains
    urls = _extract_urls(answer)
    cited_domains = [_domain_of(u) for u in urls]
    cited_domains = [d for d in cited_domains if d]
    domain_cited = any(any(od in d for od in owned_domains) for d in cited_domains)

    # Heuristic answer-quality (1..5) — based on length + structure
    quality = 1
    if len(answer) > 200:  quality = 2
    if len(answer) > 500:  quality = 3
    if "\n" in answer:     quality += 1
    if len(urls) > 0:      quality += 1
    quality = max(1, min(5, quality))

    # Persist AiResult
    ar = models.AiResult(
        result_id=f"AR-{uuid.uuid4().hex[:10]}",
        prompt_id=prompt.prompt_id,
        platform="ChatGPT",  # OpenAI API; treat as ChatGPT-equivalent for the UI
        date_checked=date.today(),
        answer_text=answer,
        brand_mentioned=brand_in,
        product_mentioned=product_in,
        domain_cited=domain_cited,
        competitors_mentioned=comps,
        cited_sources=[_domain_of(u) for u in urls if _domain_of(u)],
        entities=[
            {"label": "Brand",       "items": [target_brand] if brand_in else []},
            {"label": "Product",     "items": [target_product] if product_in else []},
            {"label": "Competitors", "items": comps},
        ],
        answer_quality_score=quality,
        notes=f"Auto-monitor run via {model}",
    )
    db.add(ar)

    # Mirror onto the Prompt for fast list rendering
    prompt.platform = "ChatGPT"
    prompt.brand_mentioned   = brand_in
    prompt.product_mentioned = product_in
    prompt.domain_cited      = domain_cited
    prompt.competitors_mentioned = comps
    prompt.cited_sources     = [_domain_of(u) for u in urls if _domain_of(u)]
    prompt.answer_quality_score = quality
    if brand_in and domain_cited:
        prompt.monitor_status = "Good"
    elif not brand_in:
        prompt.monitor_status = "Gap"
    elif comps and not domain_cited:
        prompt.monitor_status = "Risk"
    else:
        prompt.monitor_status = "Needs review"

    # Upsert Sources from the cited URLs
    for u in urls:
        d = _domain_of(u)
        if not d:
            continue
        existing = db.query(models.Source).filter_by(source_url=u).one_or_none()
        if existing:
            cited_by = list(existing.cited_by_prompts or [])
            if prompt.prompt_id not in cited_by:
                cited_by.append(prompt.prompt_id)
                existing.cited_by_prompts = cited_by
            existing.updated = date.today().isoformat()
            existing.mentions_brand      = existing.mentions_brand      or brand_in
            existing.mentions_product    = existing.mentions_product    or product_in
            existing.mentions_competitor = existing.mentions_competitor or bool(comps)
            existing.links_to_owned_domain = existing.links_to_owned_domain or any(od in d for od in owned_domains)
        else:
            owned = any(od in d for od in owned_domains)
            db.add(models.Source(
                source_id=f"S-{uuid.uuid4().hex[:8].upper()}",
                source_url=u,
                domain=d,
                title=d,
                source_type="Industry media" if not owned else "Supplier list",
                cited_by_prompts=[prompt.prompt_id],
                mentions_brand=brand_in,
                mentions_product=product_in,
                mentions_competitor=bool(comps),
                links_to_owned_domain=owned,
                source_influence_score=50,
                outreach_status="Not reviewed",
                recommended_action="",
                updated=date.today().isoformat(),
            ))

    # Cost log
    in_p, out_p = _model_pricing(model)
    cost = (result["prompt_tokens"] / 1_000_000.0) * in_p + (result["completion_tokens"] / 1_000_000.0) * out_p
    db.add(models.UsageLog(
        log_id=f"U-{uuid.uuid4().hex[:8].upper()}",
        run_at=datetime.utcnow(),
        model=model,
        prompt_id=prompt.prompt_id,
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        cost_usd=cost,
    ))

    db.commit()

    # Auto-generate one recommendation if visibility is weak and there isn't one already
    weak = prompt.monitor_status in ("Gap", "Risk")
    if weak:
        existing = db.query(models.Recommendation).filter(
            models.Recommendation.related_prompt_id == prompt.prompt_id,
            models.Recommendation.status.in_(("New", "Approved", "Task created")),
        ).first()
        if not existing:
            try:
                generate_recommendations(db=db, request=schemas.GenerateRequest(prompt_id=prompt.prompt_id, limit=1))
            except Exception as e:
                # Don't fail the run if recommendation generation fails
                print("Recommendation generation failed:", e)

    return {
        "prompt_id": prompt.prompt_id,
        "monitor_status": prompt.monitor_status,
        "brand_mentioned": brand_in,
        "competitors_mentioned": comps,
        "domain_cited": domain_cited,
        "cited_urls": urls,
        "cost_usd": cost,
    }


def run_all(db: Session) -> dict:
    """Run every active query. Skips and reports failures."""
    prompts = db.query(models.Prompt).filter(models.Prompt.status == "active").all()
    ran = []
    failed = []
    total_cost = 0.0
    for p in prompts:
        try:
            res = run_query(db, p)
            ran.append({"prompt_id": p.prompt_id, "status": res["monitor_status"]})
            total_cost += float(res.get("cost_usd") or 0.0)
        except Exception as e:
            failed.append({"prompt_id": p.prompt_id, "error": str(e)})
    return {"ran": ran, "failed": failed, "total_cost_usd": total_cost, "count": len(prompts)}
