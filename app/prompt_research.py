from __future__ import annotations

import re
import uuid
import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from app import models, schemas
from app.intelligence import FALLBACK_QUERIES, normalize_query


STOPWORDS = {
    "what", "which", "best", "how", "for", "the", "and", "with", "without",
    "additive", "additives", "agent", "agents", "polymer", "polymers", "carbon",
    "conductive", "conductivity", "anti", "static", "esd", "to", "in", "of",
    "a", "an", "is", "are", "does", "do", "can", "should", "use", "make",
}


def run_prompt_research(db: Session, count: int = 25) -> schemas.PromptResearchOut:
    count = max(10, min(50, int(count or 25)))
    source_status = {"gsc": "ok", "ga4": "ok", "trends": "ok"}
    gsc_rows = db.query(models.GoogleSearchMetric).all()
    ga4_rows = db.query(models.GoogleAnalyticsMetric).all()
    prompts = db.query(models.Prompt).all()
    if not gsc_rows:
        source_status["gsc"] = "missing"
    if not ga4_rows:
        source_status["ga4"] = "missing"

    trend_rows, trend_error = _ensure_trends(db, gsc_rows, prompts)
    if trend_error:
        source_status["trends"] = "unavailable: " + trend_error[:160]
    elif not trend_rows:
        source_status["trends"] = "missing"

    candidates = []
    candidates.extend(_add_candidates(gsc_rows, ga4_rows, trend_rows, prompts))
    candidates.extend(_keep_delete_candidates(gsc_rows, ga4_rows, trend_rows, prompts))
    candidates.extend(_fallback_add_candidates(candidates, prompts))

    selected = _rank_and_balance(candidates, count)
    batch_id = f"PRB-{uuid.uuid4().hex[:10].upper()}"
    batch = models.PromptResearchBatch(
        batch_id=batch_id,
        generated_at=datetime.utcnow(),
        source_status=source_status,
        summary=_summary(selected, source_status),
        raw_summary={
            "gsc_rows": len(gsc_rows),
            "ga4_rows": len(ga4_rows),
            "trends_rows": len(trend_rows),
            "candidate_count": len(candidates),
        },
    )
    db.add(batch)
    for item in selected:
        db.add(models.PromptResearchItem(
            item_id=f"PRI-{uuid.uuid4().hex[:10].upper()}",
            batch_id=batch_id,
            action=item["action"],
            prompt_id=item.get("prompt_id"),
            query_text=item["query_text"],
            topic_cluster=item["topic_cluster"],
            intent_type=item["intent_type"],
            priority_score=item["priority_score"],
            confidence_score=item["confidence_score"],
            evidence=item["evidence"],
            reason=item["reason"],
            status="draft",
            created_at=datetime.utcnow(),
        ))
    db.commit()
    return latest_research(db, batch_id)


def latest_research(db: Session, batch_id: str | None = None) -> schemas.PromptResearchOut:
    q = db.query(models.PromptResearchBatch)
    if batch_id:
        q = q.filter(models.PromptResearchBatch.batch_id == batch_id)
    batch = q.order_by(models.PromptResearchBatch.generated_at.desc()).first()
    if not batch:
        return schemas.PromptResearchOut(batch=None, items=[])
    items = (
        db.query(models.PromptResearchItem)
        .filter_by(batch_id=batch.batch_id)
        .order_by(models.PromptResearchItem.priority_score.desc(), models.PromptResearchItem.created_at.asc())
        .all()
    )
    return schemas.PromptResearchOut(batch=batch, items=items)


def apply_research(db: Session, batch_id: str, item_ids: list[str]) -> schemas.PromptResearchApplyOut:
    allowed = set(item_ids or [])
    items = (
        db.query(models.PromptResearchItem)
        .filter_by(batch_id=batch_id)
        .filter(models.PromptResearchItem.item_id.in_(allowed))
        .all()
    )
    existing = {normalize_query(p.prompt_text): p for p in db.query(models.Prompt).all()}
    added: list[models.Prompt] = []
    deleted: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []
    for item in items:
        if item.status == "applied":
            skipped.append(item.item_id)
            continue
        if item.action == "Add":
            norm = normalize_query(item.query_text)
            if not norm or norm in existing:
                item.status = "skipped"
                skipped.append(item.item_id)
                continue
            prompt = models.Prompt(
                prompt_id=_next_prompt_id(db),
                prompt_text=item.query_text,
                topic_cluster=item.topic_cluster,
                country="United States",
                language="en",
                business_priority=max(3, min(5, round((item.priority_score or 50) / 20))),
                priority=_priority_label(item.priority_score),
                monitor_status="Unchecked",
            )
            db.add(prompt)
            db.flush()
            added.append(prompt)
            existing[norm] = prompt
            item.status = "applied"
        elif item.action == "Delete":
            prompt = db.query(models.Prompt).filter_by(prompt_id=item.prompt_id).one_or_none()
            if not prompt:
                item.status = "skipped"
                skipped.append(item.item_id)
                continue
            deleted.append(prompt.prompt_id)
            db.delete(prompt)
            item.status = "applied"
        else:
            kept.append(item.item_id)
            item.status = "applied"
    db.commit()
    for prompt in added:
        db.refresh(prompt)
    return schemas.PromptResearchApplyOut(batch_id=batch_id, added=added, deleted=deleted, kept=kept, skipped=skipped)


def _add_candidates(gsc_rows, ga4_rows, trend_rows, prompts) -> list[dict]:
    existing_norms = {normalize_query(p.prompt_text) for p in prompts}
    ga4_index = _ga4_index(ga4_rows)
    trend_index = {normalize_query(t.keyword): t for t in trend_rows}
    by_query: dict[str, list[models.GoogleSearchMetric]] = defaultdict(list)
    for row in gsc_rows:
        if row.query and len(row.query.strip()) > 4:
            by_query[normalize_query(row.query)].append(row)
    out = []
    for norm, rows in by_query.items():
        if norm in existing_norms:
            continue
        text = max(rows, key=lambda r: r.impressions or 0).query.strip()
        if _bad_query(text):
            continue
        gsc_impressions = sum(r.impressions or 0 for r in rows)
        gsc_clicks = sum(r.clicks or 0 for r in rows)
        pos = _weighted_position(rows)
        ga4 = _best_ga4_for_text(text, rows, ga4_index)
        trend = trend_index.get(norm) or _best_trend_for_text(text, trend_rows)
        score = _score_add(gsc_impressions, gsc_clicks, pos, ga4, trend)
        out.append({
            "action": "Add",
            "query_text": _promptize(text),
            "topic_cluster": _cluster_for(text),
            "intent_type": _intent_for(text),
            "priority_score": score,
            "confidence_score": _confidence(gsc_impressions, ga4, trend),
            "reason": _reason_add(gsc_impressions, ga4, trend),
            "evidence": _evidence(gsc_impressions, gsc_clicks, pos, ga4, trend, rows),
        })
    for trend in trend_rows:
        norm = normalize_query(trend.keyword)
        if norm in existing_norms or any(normalize_query(c["query_text"]) == norm for c in out):
            continue
        if trend.interest_avg < 10:
            continue
        text = _promptize(trend.keyword)
        out.append({
            "action": "Add",
            "query_text": text,
            "topic_cluster": _cluster_for(text),
            "intent_type": _intent_for(text),
            "priority_score": min(82, 45 + round(trend.interest_avg / 2)),
            "confidence_score": 55,
            "reason": "Trend demand exists but current monitoring does not cover it.",
            "evidence": {"trends": _trend_dict(trend), "gsc": {}, "ga4": {}},
        })
    return out


def _keep_delete_candidates(gsc_rows, ga4_rows, trend_rows, prompts) -> list[dict]:
    ga4_index = _ga4_index(ga4_rows)
    out = []
    for prompt in prompts:
        matches = [r for r in gsc_rows if _similar(prompt.prompt_text, r.query + " " + r.page)]
        gsc_impressions = sum(r.impressions or 0 for r in matches)
        gsc_clicks = sum(r.clicks or 0 for r in matches)
        pos = _weighted_position(matches)
        ga4 = _best_ga4_for_text(prompt.prompt_text, matches, ga4_index)
        trend = _best_trend_for_text(prompt.prompt_text, trend_rows)
        gap_boost = {"Gap": 18, "Risk": 12, "Unchecked": -8, "Good": 4}.get(prompt.monitor_status, 0)
        value = _score_add(gsc_impressions, gsc_clicks, pos, ga4, trend) + gap_boost + (prompt.business_priority or 3) * 3
        action = "Keep"
        reason = "Keep monitoring: it has search/traffic evidence or current AI visibility value."
        if value < 40 and prompt.monitor_status in ("Unchecked", "Gap") and (prompt.business_priority or 3) <= 3:
            action = "Delete"
            reason = "Low evidence and low priority; remove to keep monitoring focused."
        out.append({
            "action": action,
            "prompt_id": prompt.prompt_id,
            "query_text": prompt.prompt_text,
            "topic_cluster": prompt.topic_cluster or _cluster_for(prompt.prompt_text),
            "intent_type": _intent_for(prompt.prompt_text),
            "priority_score": max(1, min(100, round(value))),
            "confidence_score": _confidence(gsc_impressions, ga4, trend),
            "reason": reason,
            "evidence": _evidence(gsc_impressions, gsc_clicks, pos, ga4, trend, matches) | {
                "monitor": {"status": prompt.monitor_status, "business_priority": prompt.business_priority},
            },
        })
    return out


def _ensure_trends(db: Session, gsc_rows, prompts) -> tuple[list[models.GoogleTrendsMetric], str]:
    cached = db.query(models.GoogleTrendsMetric).all()
    if os.getenv("ENABLE_GOOGLE_TRENDS", "0") != "1":
        return cached, "disabled for live research; using GSC + GA4"

    keywords = []
    for row in sorted(gsc_rows, key=lambda r: r.impressions or 0, reverse=True)[:20]:
        if row.query and not _bad_query(row.query):
            keywords.append(row.query)
    for prompt in prompts[:20]:
        keywords.append(prompt.prompt_text)
    # pytrends is unofficial and can block or rate-limit. Keep the live request
    # intentionally small; GSC + GA4 remain the primary evidence sources.
    keywords = list(dict.fromkeys(k[:90] for k in keywords if k))[:5]
    cached_norms = {normalize_query(t.keyword) for t in cached}
    missing = [k for k in keywords if normalize_query(k) not in cached_norms]
    if not missing:
        return cached, ""
    try:
        from pytrends.request import TrendReq  # type: ignore
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(2, 5), retries=0)
        for chunk in [missing[i:i + 5] for i in range(0, len(missing), 5)]:
            pytrends.build_payload(chunk, timeframe="today 12-m", geo="")
            interest = pytrends.interest_over_time()
            related = pytrends.related_queries()
            for kw in chunk:
                values = []
                if hasattr(interest, "columns") and kw in interest.columns:
                    values = [float(v) for v in interest[kw].fillna(0).tolist()]
                top = _related_rows((related.get(kw) or {}).get("top") if isinstance(related, dict) else None)
                rising = _related_rows((related.get(kw) or {}).get("rising") if isinstance(related, dict) else None)
                db.add(models.GoogleTrendsMetric(
                    metric_id=f"GT-{uuid.uuid4().hex[:12]}",
                    keyword=kw,
                    geo="",
                    timeframe="today 12-m",
                    interest_avg=round(sum(values) / len(values), 2) if values else 0,
                    interest_max=round(max(values)) if values else 0,
                    related_queries=top,
                    rising_queries=rising,
                    fetched_at=datetime.utcnow(),
                ))
        db.commit()
    except Exception as exc:
        return cached, str(exc)
    return db.query(models.GoogleTrendsMetric).all(), ""


def _related_rows(frame) -> list[dict]:
    if frame is None:
        return []
    try:
        return [
            {"query": str(row.get("query") or ""), "value": int(row.get("value") or 0)}
            for _, row in frame.head(8).iterrows()
        ]
    except Exception:
        return []


def _rank_and_balance(candidates: list[dict], count: int) -> list[dict]:
    candidates = sorted(candidates, key=lambda c: c["priority_score"], reverse=True)
    selected = []
    seen = set()
    for action in ("Add", "Keep", "Delete"):
        for c in candidates:
            key = (c["action"], normalize_query(c["query_text"]), c.get("prompt_id") or "")
            if c["action"] == action and key not in seen:
                selected.append(c)
                seen.add(key)
                break
    for c in candidates:
        key = (c["action"], normalize_query(c["query_text"]), c.get("prompt_id") or "")
        if key in seen:
            continue
        selected.append(c)
        seen.add(key)
        if len(selected) >= count:
            break
    return selected[:count]


def _fallback_add_candidates(existing_candidates: list[dict], prompts) -> list[dict]:
    seen = {normalize_query(c["query_text"]) for c in existing_candidates}
    seen |= {normalize_query(p.prompt_text) for p in prompts}
    out = []
    for text, cluster, intent, priority, reason in FALLBACK_QUERIES:
        norm = normalize_query(text)
        if norm in seen:
            continue
        out.append({
            "action": "Add",
            "query_text": text,
            "topic_cluster": cluster,
            "intent_type": intent,
            "priority_score": 38 + priority * 7,
            "confidence_score": 40,
            "reason": reason,
            "evidence": {"gsc": {}, "ga4": {}, "trends": {}, "fallback": True},
        })
        seen.add(norm)
    return out


def _ga4_index(rows) -> list[dict]:
    return [{"text": f"{r.page_path} {r.page_title}", "row": r} for r in rows]


def _best_ga4_for_text(text: str, gsc_rows, ga4_index) -> dict:
    candidates = []
    for row in gsc_rows or []:
        candidates.extend([x for x in ga4_index if _path_key(row.page) and _path_key(row.page) == _path_key(x["row"].page_path)])
    candidates.extend([x for x in ga4_index if _similar(text, x["text"])])
    if not candidates:
        return {}
    row = max((x["row"] for x in candidates), key=lambda r: r.sessions or 0)
    return {"page": row.page_path, "title": row.page_title, "sessions": row.sessions or 0, "users": row.active_users or 0, "conversions": row.conversions or 0}


def _best_trend_for_text(text: str, trend_rows) -> models.GoogleTrendsMetric | None:
    matches = [t for t in trend_rows if _similar(text, t.keyword)]
    return max(matches, key=lambda t: t.interest_avg or 0) if matches else None


def _score_add(impressions: int, clicks: int, pos: float, ga4: dict, trend) -> int:
    gsc = min(40, round(impressions / 120)) + min(10, clicks)
    rank = 12 if 5 <= pos <= 20 else 6 if 1 <= pos < 5 else 0
    traffic = min(22, round((ga4.get("sessions") or 0) / 20)) if ga4 else 0
    trends = min(18, round((trend.interest_avg if trend else 0) / 4))
    return max(1, min(100, gsc + rank + traffic + trends))


def _confidence(impressions: int, ga4: dict, trend) -> int:
    return min(95, 35 + (25 if impressions else 0) + (20 if ga4 else 0) + (15 if trend else 0))


def _evidence(impressions: int, clicks: int, pos: float, ga4: dict, trend, gsc_rows) -> dict:
    return {
        "gsc": {"impressions": impressions, "clicks": clicks, "avg_position": pos, "examples": [{"query": r.query, "page": r.page, "impressions": r.impressions} for r in (gsc_rows or [])[:5]]},
        "ga4": ga4 or {},
        "trends": _trend_dict(trend) if trend else {},
    }


def _trend_dict(trend) -> dict:
    return {"keyword": trend.keyword, "interest_avg": trend.interest_avg, "interest_max": trend.interest_max, "rising_queries": trend.rising_queries or []}


def _weighted_position(rows) -> float:
    if not rows:
        return 0
    weight = sum(max(r.impressions or 0, 1) for r in rows)
    return round(sum((r.avg_position or 0) * max(r.impressions or 0, 1) for r in rows) / weight, 1) if weight else 0


def _promptize(query: str) -> str:
    text = query.strip().rstrip("?")
    if re.match(r"^(what|which|how|best|top|compare)", text, re.I) or re.search(r"\bvs\b", text, re.I):
        return text[:220]
    if any(x in text.lower() for x in ("supplier", "manufacturer", "company", "companies")):
        return f"Which suppliers should buyers consider for {text}?"[:220]
    if any(x in text.lower() for x in ("coating", "rubber", "plastic", "polymer", "elastomer", "epoxy", "battery")):
        return f"What should buyers know about {text}?"[:220]
    return f"What should buyers know about {text} as conductive or anti-static additives?"[:220]


def _cluster_for(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("coating", "paint", "epoxy", "flooring", "primer")):
        return "Coatings and paints"
    if any(x in low for x in ("rubber", "elastomer", "silicone", "tpu", "epdm", "fkm")):
        return "Rubber and elastomers"
    if any(x in low for x in ("battery", "batteries", "lithium", "anode", "cathode")):
        return "Batteries / energy storage"
    if any(x in low for x in ("supplier", "vendor", "companies", "manufacturer")):
        return "Supplier / procurement"
    if any(x in low for x in ("safety", "regulation", "reach", "toxic", "standard")):
        return "Safety and regulation"
    if any(x in low for x in ("vs", "compare", "alternative", "carbon black", "graphene")):
        return "Comparison and selection"
    return "General additive discovery"


def _intent_for(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("supplier", "vendor", "companies", "manufacturer")):
        return "supplier/vendor"
    if any(x in low for x in ("safety", "regulation", "reach", "toxic", "standard")):
        return "safety/regulatory"
    if any(x in low for x in ("alternative", "substitute")):
        return "substitute/alternative"
    if any(x in low for x in (" vs ", "compare", "carbon black", "graphene")):
        return "comparison"
    if any(x in low for x in ("coating", "rubber", "plastic", "battery", "epoxy", "silicone")):
        return "application/use-case"
    return "category education"


def _bad_query(text: str) -> bool:
    low = text.lower().strip()
    return len(low) < 5 or low in {"ocsial", "tuball"} or low.startswith(("http", "/"))


def _similar(a: str, b: str) -> bool:
    aw = _tokens(a)
    bw = _tokens(b)
    return bool(aw and bw and len(aw & bw) >= min(2, len(aw)))


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def _path_key(value: str) -> str:
    raw = (value or "").lower().split("?")[0].rstrip("/")
    if raw.startswith("http"):
        raw = "/" + raw.split("/", 3)[3] if raw.count("/") >= 3 else "/"
    return raw or "/"


def _reason_add(impressions: int, ga4: dict, trend) -> str:
    bits = []
    if impressions:
        bits.append(f"{impressions:,} GSC impressions")
    if ga4:
        bits.append(f"{ga4.get('sessions', 0):,} GA4 sessions")
    if trend:
        bits.append(f"Trends interest {trend.interest_avg:.0f}")
    return "Add because " + ", ".join(bits[:3]) + "." if bits else "Add to cover an uncovered buyer question."


def _summary(items: list[dict], source_status: dict) -> str:
    counts = Counter(i["action"] for i in items)
    return f"{len(items)} ranked suggestions: {counts.get('Add', 0)} add, {counts.get('Keep', 0)} keep, {counts.get('Delete', 0)} delete. Sources: GSC {source_status['gsc']}, GA4 {source_status['ga4']}, Trends {source_status['trends']}."


def _next_prompt_id(db: Session) -> str:
    ids = [r[0] for r in db.query(models.Prompt.prompt_id).filter(models.Prompt.prompt_id.like("P%")).all()]
    nums = []
    for pid in ids:
        try:
            nums.append(int(pid[1:]))
        except (TypeError, ValueError):
            continue
    return "P" + str((max(nums) if nums else 0) + 1).zfill(3)


def _priority_label(score: int) -> str:
    if score >= 75:
        return "High"
    if score >= 45:
        return "Medium"
    return "Low"
