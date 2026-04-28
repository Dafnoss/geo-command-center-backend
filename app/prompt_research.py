from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app import models, schemas
from app import monitor as monitor_engine
from app.intelligence import normalize_query


STOPWORDS = {
    "what", "which", "best", "how", "for", "the", "and", "with", "without",
    "additive", "additives", "agent", "agents", "polymer", "polymers", "carbon",
    "conductive", "conductivity", "anti", "static", "esd", "to", "in", "of",
    "a", "an", "is", "are", "does", "do", "can", "should", "use", "make",
    "buyer", "buyers", "know", "about", "consider", "variant",
}

MAX_PROMPTS_PER_INTENT_GROUP = 5


# Built-in commercial coverage map for OCSiAl/TUBALL. This is intentionally
# product/application/intent oriented rather than raw keyword oriented.
COVERAGE_TAXONOMY = [
    ("electrically conductive silicone rubber additive", "Conductive additives", "Silicone rubber", "application/use-case", "What additive should I use for electrically conductive silicone rubber?", ["silicone", "rubber", "conductive", "additive", "electrically"], 5),
    ("anti-static silicone rubber additive", "Anti-static / ESD additives", "Silicone rubber", "application/use-case", "What is the best anti-static additive for silicone rubber?", ["silicone", "rubber", "anti-static", "antistatic", "esd"], 5),
    ("conductive elastomer additive", "Conductive additives", "Elastomers", "application/use-case", "What additive should I use to make elastomers electrically conductive?", ["elastomer", "elastomers", "rubber", "conductive", "additive"], 5),
    ("conductive TPU additive", "Conductive additives", "TPU", "application/use-case", "What additive is best for electrically conductive TPU compounds?", ["tpu", "conductive", "additive", "compound"], 4),
    ("conductive EPDM additive", "Conductive additives", "EPDM", "application/use-case", "What additive is best for electrically conductive EPDM rubber?", ["epdm", "rubber", "conductive", "additive"], 4),
    ("conductive FKM additive", "Conductive additives", "FKM", "application/use-case", "What additive is best for electrically conductive FKM rubber?", ["fkm", "rubber", "conductive", "additive"], 4),
    ("conductive plastic additive", "Conductive additives", "Plastics", "application/use-case", "What additive should I use to make plastic electrically conductive?", ["plastic", "plastics", "conductive", "additive"], 5),
    ("anti-static plastic additive", "Anti-static / ESD additives", "Plastics", "application/use-case", "What is the best anti-static additive for plastics?", ["plastic", "plastics", "anti-static", "antistatic", "additive"], 5),
    ("ESD-safe polymer additive", "Anti-static / ESD additives", "Polymers", "application/use-case", "What additive is best for ESD-safe polymer compounds?", ["esd", "polymer", "compound", "additive"], 5),
    ("anti-static packaging film additive", "Anti-static / ESD additives", "Packaging films", "application/use-case", "What additive is best for anti-static packaging films?", ["anti-static", "antistatic", "packaging", "film", "additive"], 4),
    ("EMI shielding plastic additive", "Conductive additives", "EMI shielding plastics", "application/use-case", "What additive is best for EMI shielding plastic parts?", ["emi", "shielding", "plastic", "conductive", "additive"], 4),
    ("conductive polyamide additive", "Conductive additives", "PA compounds", "application/use-case", "What additive is best for electrically conductive polyamide compounds?", ["polyamide", "pa", "conductive", "compound", "additive"], 4),
    ("conductive polycarbonate additive", "Conductive additives", "Polycarbonate", "application/use-case", "What additive is best for electrically conductive polycarbonate?", ["polycarbonate", "pc", "conductive", "additive"], 4),
    ("conductive ABS additive", "Conductive additives", "ABS", "application/use-case", "What additive is best for electrically conductive ABS plastic?", ["abs", "plastic", "conductive", "additive"], 4),
    ("low dosage conductive additive", "Conductive additives", "Polymers", "performance", "What conductive additive works at the lowest loading level in polymers?", ["low", "dosage", "loading", "conductive", "polymer"], 5),
    ("colored conductive plastic additive", "Conductive additives", "Colored plastics", "performance", "Which conductive additive preserves color in plastics?", ["colored", "colour", "color", "plastic", "conductive"], 4),
    ("transparent conductive polymer additive", "Conductive additives", "Transparent polymers", "performance", "What additive can make transparent polymers anti-static or conductive?", ["transparent", "polymer", "anti-static", "conductive"], 4),
    ("conductive epoxy additive", "Conductive additives", "Epoxy resin", "application/use-case", "What additive is best for electrically conductive epoxy resin?", ["epoxy", "resin", "conductive", "additive"], 4),
    ("conductive coating additive", "Conductive additives", "Coatings", "application/use-case", "What additive is best for conductive coatings?", ["coating", "coatings", "conductive", "additive"], 4),
    ("conductive adhesive additive", "Conductive additives", "Adhesives", "application/use-case", "What additive is best for electrically conductive adhesives?", ["adhesive", "adhesives", "conductive", "additive"], 4),
    ("anti-static industrial flooring additive", "Anti-static / ESD additives", "Industrial flooring", "application/use-case", "What additive is best for anti-static industrial flooring?", ["flooring", "anti-static", "antistatic", "esd", "additive"], 4),
    ("conductive battery additive", "Conductive additives", "Batteries", "application/use-case", "What conductive additive improves lithium-ion battery electrodes?", ["battery", "batteries", "lithium", "electrode", "conductive"], 5),
    ("single-walled carbon nanotube supplier", "SWCNT", "Supplier discovery", "supplier/vendor", "Which companies supply single-walled carbon nanotube additives?", ["single", "walled", "swcnt", "carbon", "nanotube", "supplier"], 5),
    ("carbon nanotube additive supplier", "CNT additives", "Supplier discovery", "supplier/vendor", "Which companies supply carbon nanotube additives for industrial materials?", ["carbon", "nanotube", "cnt", "additive", "supplier"], 5),
    ("graphene nanotube additive supplier", "Graphene nanotubes", "Supplier discovery", "supplier/vendor", "Which companies supply graphene nanotube additives?", ["graphene", "nanotube", "additive", "supplier"], 5),
    ("conductive additive suppliers for polymers", "Conductive additives", "Supplier discovery", "supplier/vendor", "Which suppliers are recommended for conductive additives in polymer compounds?", ["supplier", "conductive", "additive", "polymer", "compound"], 5),
    ("anti-static additive suppliers", "Anti-static / ESD additives", "Supplier discovery", "supplier/vendor", "Which suppliers are recommended for anti-static additives in plastics?", ["supplier", "anti-static", "antistatic", "additive", "plastic"], 4),
    ("carbon black alternative conductive polymer", "Substitute alternatives", "Polymers", "substitute/alternative", "What is the best alternative to carbon black for conductive polymers?", ["carbon", "black", "alternative", "conductive", "polymer"], 5),
    ("carbon black vs carbon nanotubes conductive plastics", "Substitute comparison", "Plastics", "comparison", "Carbon black vs carbon nanotubes: which is better for conductive plastics?", ["carbon", "black", "nanotube", "conductive", "plastic"], 5),
    ("SWCNT vs MWCNT conductive additives", "CNT comparison", "Polymers", "comparison", "Single-walled vs multi-walled carbon nanotubes: which is better as a conductive additive?", ["swcnt", "mwcnt", "single", "multi", "walled", "conductive"], 5),
    ("graphene nanotubes vs graphene nanoplatelets", "Substitute comparison", "Polymers", "comparison", "Graphene nanotubes vs graphene nanoplatelets: which is better for conductive polymers?", ["graphene", "nanotube", "nanoplatelet", "conductive", "polymer"], 4),
    ("carbon fiber alternative conductive polymer", "Substitute alternatives", "Polymers", "substitute/alternative", "What is the best alternative to carbon fiber for lightweight conductive polymers?", ["carbon", "fiber", "alternative", "lightweight", "conductive"], 3),
    ("conductive masterbatch supplier", "TUBALL MATRIX", "Masterbatch", "supplier/vendor", "Which suppliers offer conductive masterbatch for plastics?", ["conductive", "masterbatch", "supplier", "plastic"], 4),
    ("TUBALL MATRIX conductive masterbatch", "TUBALL MATRIX", "Masterbatch", "branded", "What is TUBALL MATRIX used for in conductive polymer compounds?", ["tuball", "matrix", "conductive", "masterbatch"], 3),
    ("REACH safety carbon nanotube additives", "Safety and regulation", "Regulatory", "safety/regulatory", "Are carbon nanotube additives safe and REACH-compliant for polymer applications?", ["reach", "safety", "carbon", "nanotube", "polymer"], 4),
    ("conductive additive viscosity impact", "Conductive additives", "Processing", "performance", "Which conductive additive has the lowest impact on viscosity?", ["viscosity", "conductive", "additive", "processing"], 4),
    ("conductive additive mechanical properties", "Conductive additives", "Mechanical properties", "performance", "Which conductive additive preserves mechanical properties in polymers?", ["mechanical", "properties", "conductive", "additive", "polymer"], 4),
    ("permanent anti-static additive", "Anti-static / ESD additives", "Polymers", "performance", "Which additive gives permanent anti-static properties in polymers?", ["permanent", "anti-static", "antistatic", "polymer"], 5),
]


def run_prompt_research(db: Session, count: int = 25) -> schemas.PromptResearchOut:
    count = max(10, min(50, int(count or 25)))
    source_status = {"gsc": "ok", "ga4": "ok", "trends": "ok"}
    gsc_all = db.query(models.GoogleSearchMetric).all()
    ga4_all = db.query(models.GoogleAnalyticsMetric).all()
    gsc_rows = sorted(gsc_all, key=lambda r: r.impressions or 0, reverse=True)[:350]
    ga4_rows = sorted(ga4_all, key=lambda r: r.sessions or 0, reverse=True)[:350]
    prompts = db.query(models.Prompt).all()
    if not gsc_rows:
        source_status["gsc"] = "missing"
    if not ga4_rows:
        source_status["ga4"] = "missing"

    trend_rows, trend_error = _ensure_trends(db)
    if trend_error:
        source_status["trends"] = "unavailable: " + trend_error[:160]
    elif not trend_rows:
        source_status["trends"] = "missing"

    coverage = build_coverage_map(db, gsc_rows=gsc_rows, ga4_rows=ga4_rows, prompts=prompts, trend_rows=trend_rows)
    candidates = _add_candidates_from_coverage(coverage)
    candidates.extend(_delete_candidates(gsc_rows, ga4_rows, trend_rows, prompts))

    selected = _rank_and_balance(candidates, count)
    batch_id = f"PRB-{uuid.uuid4().hex[:10].upper()}"
    batch = models.PromptResearchBatch(
        batch_id=batch_id,
        generated_at=datetime.utcnow(),
        source_status=source_status,
        summary=_summary(selected, source_status),
        raw_summary={
            "gsc_rows": len(gsc_all),
            "ga4_rows": len(ga4_all),
            "gsc_rows_used": len(gsc_rows),
            "ga4_rows_used": len(ga4_rows),
            "trends_rows": len(trend_rows),
            "coverage_topics": len(coverage),
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
        .order_by(models.PromptResearchItem.created_at.asc())
        .all()
    )
    items = sorted(items, key=lambda item: (0 if item.action == "Add" else 1, -(item.priority_score or 0), item.created_at or datetime.min))
    return schemas.PromptResearchOut(batch=batch, items=items)


def coverage_report(db: Session) -> list[dict]:
    gsc_rows = sorted(db.query(models.GoogleSearchMetric).all(), key=lambda r: r.impressions or 0, reverse=True)[:350]
    ga4_rows = sorted(db.query(models.GoogleAnalyticsMetric).all(), key=lambda r: r.sessions or 0, reverse=True)[:350]
    prompts = db.query(models.Prompt).all()
    trends, _ = _ensure_trends(db)
    return build_coverage_map(db, gsc_rows, ga4_rows, prompts, trends)


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
    skipped: list[str] = []
    for item in items:
        if item.status == "applied":
            skipped.append(item.item_id)
            continue
        if item.action == "Add":
            norm = normalize_query(item.query_text)
            if not norm or norm in existing or _has_equivalent_prompt(item.query_text, existing.values()):
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
            item.status = "skipped"
            skipped.append(item.item_id)
    db.commit()
    for prompt in added:
        db.refresh(prompt)
    return schemas.PromptResearchApplyOut(batch_id=batch_id, added=added, deleted=deleted, skipped=skipped)


def approve_item(db: Session, batch_id: str, item_id: str, run_after_add: bool = True) -> dict:
    item = db.query(models.PromptResearchItem).filter_by(batch_id=batch_id, item_id=item_id).one_or_none()
    if not item:
        raise ValueError("Research item not found.")
    if item.status != "draft":
        return {"item_id": item_id, "status": item.status, "added": None, "deleted": None, "monitor": None}

    applied = apply_research(db, batch_id, [item_id])
    monitor_result = None
    monitor_error = ""
    added_prompt = applied.added[0] if applied.added else None
    added_payload = schemas.PromptOut.model_validate(added_prompt).model_dump(mode="json") if added_prompt else None
    if added_prompt and run_after_add:
        prompt = db.query(models.Prompt).filter_by(prompt_id=added_prompt.prompt_id).one_or_none()
        if prompt:
            try:
                monitor_result = monitor_engine.run_query(db, prompt)
                db.refresh(prompt)
                added_payload = schemas.PromptOut.model_validate(prompt).model_dump(mode="json")
            except Exception as exc:
                monitor_error = str(exc)
    return {
        "item_id": item_id,
        "status": "applied",
        "added": added_payload,
        "deleted": applied.deleted[0] if applied.deleted else None,
        "monitor": monitor_result,
        "monitor_error": monitor_error,
    }


def reject_item(db: Session, batch_id: str, item_id: str) -> dict:
    item = db.query(models.PromptResearchItem).filter_by(batch_id=batch_id, item_id=item_id).one_or_none()
    if not item:
        raise ValueError("Research item not found.")
    item.status = "rejected"
    db.commit()
    return {"item_id": item_id, "status": "rejected"}


def build_coverage_map(db: Session, gsc_rows, ga4_rows, prompts, trend_rows) -> list[dict]:
    out = []
    prompt_norms = {normalize_query(p.prompt_text) for p in prompts}
    for topic, product, application, intent, representative_prompt, terms, priority in COVERAGE_TAXONOMY:
        matched_prompts = _matched_prompts_for_topic(prompts, representative_prompt, terms)
        matched_gsc = _matched_gsc_for_topic(gsc_rows, terms)
        matched_ga4 = _matched_ga4_for_topic(ga4_rows, terms)
        trend = _best_trend_for_terms(terms, trend_rows)
        duplicate_count = _duplicate_count(matched_prompts)
        monitor_status = _coverage_status(matched_prompts, duplicate_count)
        gsc_impressions = sum(r.impressions or 0 for r in matched_gsc)
        gsc_clicks = sum(r.clicks or 0 for r in matched_gsc)
        ga4_sessions = sum(r.sessions or 0 for r in matched_ga4)
        ga4_users = sum(r.active_users or 0 for r in matched_ga4)
        demand_score = _score_add(gsc_impressions, gsc_clicks, _weighted_position(matched_gsc), _ga4_summary(matched_ga4), trend)
        strategic_score = min(100, priority * 12 + demand_score)
        if monitor_status == "missing":
            strategic_score += 10
        if intent == "supplier/vendor":
            strategic_score += 8
        if "silicone" in " ".join(terms):
            strategic_score += 8
        score = max(1, min(100, strategic_score))
        coverage = {
            "coverage_topic": topic,
            "product_area": product,
            "application": application,
            "buyer_intent": intent,
            "representative_prompt": representative_prompt,
            "monitor_status": monitor_status,
            "matched_existing_prompts": [
                {"prompt_id": p.prompt_id, "prompt_text": p.prompt_text, "monitor_status": p.monitor_status}
                for p in matched_prompts[:6]
            ],
            "matched_gsc_queries": [
                {"query": r.query, "page": r.page, "impressions": r.impressions, "clicks": r.clicks, "avg_position": r.avg_position}
                for r in matched_gsc[:6]
            ],
            "matched_ga4_pages": [
                {"page_path": r.page_path, "page_title": r.page_title, "sessions": r.sessions, "users": r.active_users}
                for r in matched_ga4[:6]
            ],
            "matched_owned_pages": _owned_pages(matched_gsc, matched_ga4)[:6],
            "priority_score": score,
            "confidence_score": min(95, 45 + (20 if matched_gsc else 0) + (20 if matched_ga4 else 0) + min(10, len(terms))),
            "gsc": {"impressions": gsc_impressions, "clicks": gsc_clicks, "avg_position": _weighted_position(matched_gsc), "examples": [
                {"query": r.query, "page": r.page, "impressions": r.impressions} for r in matched_gsc[:5]
            ]},
            "ga4": _ga4_summary(matched_ga4),
            "trends": _trend_dict(trend) if trend else {},
            "already_monitored": normalize_query(representative_prompt) in prompt_norms,
        }
        out.append(coverage)
    return sorted(out, key=lambda r: r["priority_score"], reverse=True)


def _add_candidates_from_coverage(coverage: list[dict]) -> list[dict]:
    out = []
    for row in coverage:
        if row["monitor_status"] != "missing":
            continue
        prompt = row["representative_prompt"]
        reason = _coverage_reason(row)
        out.append({
            "action": "Add",
            "query_text": prompt,
            "topic_cluster": _cluster_for_topic(row),
            "intent_type": row["buyer_intent"],
            "priority_score": row["priority_score"],
            "confidence_score": row["confidence_score"],
            "reason": reason,
            "evidence": {
                "coverage": row,
                "gsc": row["gsc"],
                "ga4": row["ga4"],
                "trends": row["trends"],
            },
        })
    return out


def _delete_candidates(gsc_rows, ga4_rows, trend_rows, prompts) -> list[dict]:
    out = []
    duplicate_delete_ids = _duplicate_delete_ids(gsc_rows, ga4_rows, trend_rows, prompts)
    for prompt in prompts:
        matches = [r for r in gsc_rows if _similar(prompt.prompt_text, f"{r.query} {r.page}")]
        ga4 = _matched_ga4_for_topic(ga4_rows, list(_tokens(prompt.prompt_text)))
        trend = _best_trend_for_terms(list(_tokens(prompt.prompt_text)), trend_rows)
        value = _score_add(sum(r.impressions or 0 for r in matches), sum(r.clicks or 0 for r in matches), _weighted_position(matches), _ga4_summary(ga4), trend)
        delete_reason = _delete_reason(prompt, duplicate_delete_ids, value, matches, ga4)
        if delete_reason:
            gsc = _evidence(sum(r.impressions or 0 for r in matches), sum(r.clicks or 0 for r in matches), _weighted_position(matches), _ga4_summary(ga4), trend, matches)
            reason_type = delete_reason["type"]
            gsc["coverage"] = {
                "coverage_topic": delete_reason["label"],
                "product_area": "",
                "application": "",
                "buyer_intent": _intent_for(prompt.prompt_text),
                "representative_prompt": prompt.prompt_text,
                "monitor_status": reason_type,
                "matched_existing_prompts": delete_reason.get("matched_prompts", [{"prompt_id": prompt.prompt_id, "prompt_text": prompt.prompt_text, "monitor_status": prompt.monitor_status}]),
                "matched_gsc_queries": gsc["gsc"].get("examples", []),
                "matched_ga4_pages": [],
                "matched_owned_pages": [],
            }
            out.append({
                "action": "Delete",
                "prompt_id": prompt.prompt_id,
                "query_text": prompt.prompt_text,
                "topic_cluster": prompt.topic_cluster or _cluster_for(prompt.prompt_text),
                "intent_type": _intent_for(prompt.prompt_text),
                "priority_score": delete_reason["priority_score"],
                "confidence_score": delete_reason["confidence_score"],
                "reason": delete_reason["reason"],
                "evidence": gsc,
            })
    return out


def _ensure_trends(db: Session) -> tuple[list[models.GoogleTrendsMetric], str]:
    cached = db.query(models.GoogleTrendsMetric).all()
    if os.getenv("ENABLE_GOOGLE_TRENDS", "0") != "1":
        return cached, "disabled for live research; using GSC + GA4"
    return cached, ""


def _rank_and_balance(candidates: list[dict], count: int) -> list[dict]:
    candidates = sorted(
        candidates,
        key=lambda c: (0 if c["action"] == "Add" else 1, -c["priority_score"]),
    )
    selected = []
    seen = set()
    for c in candidates:
        key = (c["action"], normalize_query(c["query_text"]), c.get("prompt_id") or "")
        semantic_key = _semantic_key(c["query_text"])
        if key in seen or semantic_key in seen:
            continue
        selected.append(c)
        seen.add(key)
        seen.add(semantic_key)
        if len(selected) >= count:
            break
    return selected[:count]


def _matched_prompts_for_topic(prompts, representative_prompt: str, terms: list[str]) -> list[models.Prompt]:
    out = []
    for prompt in prompts:
        if _topic_match(prompt.prompt_text, representative_prompt, terms):
            out.append(prompt)
    return out


def _matched_gsc_for_topic(rows, terms: list[str]) -> list[models.GoogleSearchMetric]:
    matches = [r for r in rows if _terms_match(f"{r.query} {r.page}", terms)]
    return sorted(matches, key=lambda r: r.impressions or 0, reverse=True)[:10]


def _matched_ga4_for_topic(rows, terms: list[str]) -> list[models.GoogleAnalyticsMetric]:
    matches = [r for r in rows if _terms_match(f"{r.page_path} {r.page_title}", terms)]
    return sorted(matches, key=lambda r: r.sessions or 0, reverse=True)[:10]


def _topic_match(text: str, representative_prompt: str, terms: list[str]) -> bool:
    if normalize_query(text) == normalize_query(representative_prompt):
        return True
    return _terms_match(text, terms, threshold=2) and _jaccard(_tokens(text), set(terms)) >= 0.16


def _terms_match(text: str, terms: list[str], threshold: int = 2) -> bool:
    low = (text or "").lower()
    tokens = _tokens(low)
    hits = 0
    for term in terms:
        term_l = term.lower()
        if term_l in low or term_l.replace("-", "") in low.replace("-", "") or term_l in tokens:
            hits += 1
    return hits >= min(threshold, max(1, len(terms)))


def _coverage_status(matches: list[models.Prompt], duplicate_count: int) -> str:
    if duplicate_count >= 2:
        return "duplicate"
    if not matches:
        return "missing"
    if any(p.monitor_status == "Good" for p in matches):
        return "monitored"
    if any(p.monitor_status in ("Risk", "Gap") for p in matches):
        return "weak"
    return "monitored"


def _duplicate_count(prompts: list[models.Prompt]) -> int:
    norms = Counter(normalize_query(p.prompt_text) for p in prompts)
    return sum(1 for count in norms.values() if count > 1)


def _has_equivalent_prompt(text: str, prompts) -> bool:
    text_tokens = _tokens(text)
    for p in prompts:
        if normalize_query(text) == normalize_query(p.prompt_text):
            return True
        if _jaccard(text_tokens, _tokens(p.prompt_text)) >= 0.72:
            return True
    return False


def _has_better_duplicate(prompt: models.Prompt, prompts: list[models.Prompt]) -> bool:
    for other in prompts:
        if other.prompt_id == prompt.prompt_id:
            continue
        if normalize_query(other.prompt_text) == normalize_query(prompt.prompt_text) or _jaccard(_tokens(other.prompt_text), _tokens(prompt.prompt_text)) >= 0.82:
            return (other.business_priority or 3) >= (prompt.business_priority or 3) and other.monitor_status != "Gap"
    return False


def _duplicate_delete_ids(gsc_rows, ga4_rows, trend_rows, prompts) -> dict[str, dict]:
    delete: dict[str, dict] = {}
    exact_groups: dict[str, list[models.Prompt]] = {}
    for prompt in prompts:
        exact_groups.setdefault(normalize_query(prompt.prompt_text), []).append(prompt)
    for norm, group_prompts in exact_groups.items():
        if not norm or len(group_prompts) <= 1:
            continue
        ranked = sorted(
            group_prompts,
            key=lambda p: _prompt_keep_score(p, gsc_rows, ga4_rows, trend_rows),
            reverse=True,
        )
        keep = ranked[0]
        matched = [{"prompt_id": keep.prompt_id, "prompt_text": keep.prompt_text, "monitor_status": keep.monitor_status}]
        for prompt in ranked[1:]:
            delete[prompt.prompt_id] = {
                "type": "duplicate-intent",
                "label": "Exact duplicate",
                "priority_score": 70,
                "confidence_score": 95,
                "matched_prompts": matched,
                "reason": (
                    "Delete because this prompt is an exact duplicate of a stronger prompt already kept in the Monitor Queue. "
                    "It does not add new AI visibility evidence."
                ),
            }

    groups: dict[str, list[models.Prompt]] = {}
    for prompt in prompts:
        groups.setdefault(_intent_group_key(prompt.prompt_text), []).append(prompt)

    for group_key, group_prompts in groups.items():
        if group_key.endswith(":general") or ":general:" in group_key:
            continue
        if len(group_prompts) <= MAX_PROMPTS_PER_INTENT_GROUP:
            continue
        ranked = sorted(
            group_prompts,
            key=lambda p: _prompt_keep_score(p, gsc_rows, ga4_rows, trend_rows),
            reverse=True,
        )
        keep = ranked[:MAX_PROMPTS_PER_INTENT_GROUP]
        keep_ids = {p.prompt_id for p in keep}
        matched = [
            {"prompt_id": p.prompt_id, "prompt_text": p.prompt_text, "monitor_status": p.monitor_status}
            for p in keep
        ]
        for prompt in ranked[MAX_PROMPTS_PER_INTENT_GROUP:]:
            if prompt.prompt_id in keep_ids:
                continue
            if prompt.prompt_id in delete:
                continue
            delete[prompt.prompt_id] = {
                "type": "duplicate-intent",
                "label": "Duplicate intent",
                "priority_score": 55,
                "confidence_score": 82,
                "matched_prompts": matched,
                "reason": (
                    f"Delete because this is the {len(group_prompts)}th variant of the same buyer intent. "
                    f"The queue should keep at most {MAX_PROMPTS_PER_INTENT_GROUP} strong variants so we measure coverage without asking the same question dozens of ways."
                ),
            }
    return delete


def _delete_reason(prompt, duplicate_delete_ids: dict[str, dict], value: int, gsc_rows, ga4_rows) -> dict | None:
    if prompt.prompt_id in duplicate_delete_ids:
        return duplicate_delete_ids[prompt.prompt_id]

    text = prompt.prompt_text or ""
    if _outside_business_scope(text):
        return {
            "type": "outside-business-scope",
            "label": "Outside business scope",
            "priority_score": 65,
            "confidence_score": 85,
            "reason": (
                "Delete because this prompt asks about buying or producing a finished downstream product/service, "
                "not selecting conductive, anti-static, ESD, CNT, SWCNT, graphene nanotube, or TUBALL additive materials."
            ),
        }

    if _too_narrow_low_value(prompt, text, value, gsc_rows, ga4_rows):
        return {
            "type": "narrow-low-value",
            "label": "Too narrow / low evidence",
            "priority_score": 35,
            "confidence_score": 55,
            "reason": (
                "Delete because this is a very narrow technical/property prompt with no meaningful GSC or GA4 evidence "
                "and it is not needed to understand core AI visibility coverage."
            ),
        }

    return None


def _prompt_keep_score(prompt, gsc_rows, ga4_rows, trend_rows) -> float:
    matches = [r for r in gsc_rows if _similar(prompt.prompt_text, f"{r.query} {r.page}")]
    ga4 = _matched_ga4_for_topic(ga4_rows, list(_tokens(prompt.prompt_text)))
    trend = _best_trend_for_terms(list(_tokens(prompt.prompt_text)), trend_rows)
    value = _score_add(sum(r.impressions or 0 for r in matches), sum(r.clicks or 0 for r in matches), _weighted_position(matches), _ga4_summary(ga4), trend)
    status_weight = {"Good": 35, "Risk": 25, "Gap": 15, "Unchecked": 5}.get(prompt.monitor_status or "Unchecked", 5)
    return status_weight + (prompt.business_priority or 3) * 8 + value


def _intent_group_key(text: str) -> str:
    low = (text or "").lower()
    tokens = sorted(_tokens(low))
    family = _intent_family(low, tokens)
    material = _material_facet(low, tokens)
    application = _application_facet(low, tokens)
    comparison = _comparison_facet(low, tokens) if family in {"comparison", "alternative"} else ""

    # Supplier searches are not one intent by themselves. "SWCNT supplier for
    # ESD floor coating" and "SWCNT supplier for battery electrodes" are
    # different buyer situations and should both remain monitorable.
    if family == "supplier":
        return f"supplier:{material}:{application}"
    if family in {"comparison", "alternative"}:
        return f"{family}:{material}:{application}:{comparison}"
    if family == "safety":
        return f"safety:{material}:{application}"

    anchor = [t for t in tokens if t not in STOPWORDS and t not in {material, application, comparison}][:3]
    return f"{family}:{material}:{application}:{'-'.join(anchor)}"


def _intent_family(low: str, tokens: set[str] | list[str]) -> str:
    if any(t in tokens for t in ("supplier", "suppliers", "supply", "vendor", "vendors", "companies", "manufacturer", "manufacturers")):
        return "supplier"
    if any(t in tokens for t in ("alternative", "alternatives", "substitute", "substitutes")):
        return "alternative"
    if any(t in tokens for t in ("vs", "compare", "comparison")) or " vs " in f" {low} ":
        return "comparison"
    if any(t in tokens for t in ("safety", "reach", "regulatory", "regulation", "compliant", "compliance")):
        return "safety"
    return "application"


def _material_facet(low: str, tokens: set[str] | list[str]) -> str:
    if "single wall" in low or "single-walled" in low or "swcnt" in tokens:
        return "swcnt"
    if "multi wall" in low or "multi-walled" in low or "mwcnt" in tokens:
        return "mwcnt"
    if "graphene nanotube" in low or "tuball" in tokens:
        return "graphene-nanotubes"
    if "carbon black" in low:
        return "carbon-black"
    if "graphene nanoplatelet" in low or "gnp" in tokens:
        return "graphene-nanoplatelets"
    if "carbon fiber" in low or "carbon fibre" in low:
        return "carbon-fiber"
    if "nanotube" in tokens or "nanotubes" in tokens or "cnt" in tokens:
        return "cnt"
    if "masterbatch" in tokens:
        return "masterbatch"
    if "anti-static" in low or "antistatic" in tokens or "esd" in tokens:
        return "antistatic-additive"
    if "conductive" in tokens or "conductivity" in tokens:
        return "conductive-additive"
    return "general-additive"


def _application_facet(low: str, tokens: set[str] | list[str]) -> str:
    if any(t in tokens for t in ("battery", "batteries", "electrode", "electrodes", "anode", "cathode", "lithium")):
        return "battery-electrodes"
    if any(t in tokens for t in ("aerospace", "aircraft", "aviation")):
        return "aerospace"
    if any(t in tokens for t in ("automotive", "ev", "vehicle", "vehicles")):
        return "automotive"
    if any(t in tokens for t in ("electronics", "electronic", "semiconductor", "semiconductors")):
        return "electronics"
    if "floor" in low or "flooring" in tokens:
        return "flooring"
    if any(t in tokens for t in ("coating", "coatings", "paint", "paints", "primer", "primers")):
        return "coatings"
    if "silicone" in tokens:
        return "silicone-rubber"
    if any(t in tokens for t in ("rubber", "elastomer", "elastomers", "tpu", "epdm", "fkm")):
        return "elastomers"
    if any(t in tokens for t in ("plastic", "plastics", "polyamide", "polycarbonate", "abs", "pa", "pc")):
        return "plastics"
    if any(t in tokens for t in ("epoxy", "resin", "adhesive", "adhesives")):
        return "resins-adhesives"
    if any(t in tokens for t in ("film", "films", "packaging")):
        return "films-packaging"
    if any(t in tokens for t in ("emi", "shielding")):
        return "emi-shielding"
    return "general"


def _comparison_facet(low: str, tokens: set[str] | list[str]) -> str:
    materials = []
    if "carbon black" in low:
        materials.append("carbon-black")
    if "carbon fiber" in low or "carbon fibre" in low:
        materials.append("carbon-fiber")
    if "graphene nanoplatelet" in low or "gnp" in tokens:
        materials.append("graphene-nanoplatelets")
    if "graphene nanotube" in low:
        materials.append("graphene-nanotubes")
    if "mwcnt" in tokens or "multi-walled" in low or "multi wall" in low:
        materials.append("mwcnt")
    if "swcnt" in tokens or "single-walled" in low or "single wall" in low:
        materials.append("swcnt")
    if "cnt" in tokens or "nanotube" in tokens or "nanotubes" in tokens:
        materials.append("cnt")
    return "-vs-".join(sorted(set(materials))) or "general"


def _outside_business_scope(text: str) -> bool:
    low = (text or "").lower()
    if "additive" in low or "nanotube" in low or "swcnt" in low or "tuball" in low:
        return False
    procurement = any(x in low for x in ("where to order", "where can i order", "where to buy", "buy ", "purchase ", "factory", "factories", "producer of", "manufactures "))
    downstream = any(x in low for x in (
        "esd floor", "esd floors", "anti-static floor", "antistatic floor", "flooring contractor",
        "pu item", "pu items", "polyurethane item", "polyurethane items", "plastic parts",
        "rubber parts", "finished product", "molding factory", "injection molding",
    ))
    return procurement and downstream


def _too_narrow_low_value(prompt, text: str, value: int, gsc_rows, ga4_rows) -> bool:
    if prompt.monitor_status not in ("Unchecked", "Gap"):
        return False
    if (prompt.business_priority or 3) > 2:
        return False
    if value >= 18 or gsc_rows or ga4_rows:
        return False
    low = (text or "").lower()
    property_terms = (
        "elongation at break", "tensile modulus", "young's modulus", "shore hardness",
        "melt flow", "specific surface area", "emi shielding effectiveness",
    )
    return any(term in low for term in property_terms)


def _best_trend_for_terms(terms: list[str], trend_rows) -> models.GoogleTrendsMetric | None:
    matches = [t for t in trend_rows if _terms_match(t.keyword, terms, threshold=1)]
    return max(matches, key=lambda t: t.interest_avg or 0) if matches else None


def _ga4_summary(rows) -> dict:
    if not rows:
        return {}
    best = max(rows, key=lambda r: r.sessions or 0)
    return {
        "page": best.page_path,
        "title": best.page_title,
        "sessions": sum(r.sessions or 0 for r in rows),
        "users": sum(r.active_users or 0 for r in rows),
        "conversions": sum(r.conversions or 0 for r in rows),
    }


def _owned_pages(gsc_rows, ga4_rows) -> list[dict]:
    pages = {}
    for row in gsc_rows:
        if row.page:
            pages[row.page] = {"url": row.page, "source": "gsc", "impressions": row.impressions or 0}
    for row in ga4_rows:
        if row.page_path:
            pages[row.page_path] = {"url": row.page_path, "source": "ga4", "sessions": row.sessions or 0, "title": row.page_title}
    return list(pages.values())


def _coverage_reason(row: dict) -> str:
    bits = []
    if row["monitor_status"] == "missing":
        bits.append("missing from Monitor Queue")
    if row["gsc"]["impressions"]:
        bits.append(f"{row['gsc']['impressions']:,} GSC impressions")
    if row["ga4"].get("sessions"):
        bits.append(f"{row['ga4']['sessions']:,} GA4 sessions on related pages")
    return "Add because " + ", ".join(bits) + "."


def _cluster_for_topic(row: dict) -> str:
    intent = row["buyer_intent"]
    app = row["application"]
    if intent == "supplier/vendor":
        return "Supplier / procurement"
    if intent == "comparison":
        return "Comparison and selection"
    if intent == "substitute/alternative":
        return "Comparison and selection"
    if intent == "safety/regulatory":
        return "Safety and regulation"
    if "silicone" in app.lower() or "elastomer" in app.lower() or "rubber" in app.lower() or app in {"TPU", "EPDM", "FKM"}:
        return "Rubber and elastomers"
    if "coating" in app.lower() or "epoxy" in app.lower() or "flooring" in app.lower():
        return "Coatings and paints"
    if "battery" in app.lower():
        return "Batteries / energy storage"
    return "General additive discovery"


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


def _summary(items: list[dict], source_status: dict) -> str:
    counts = Counter(i["action"] for i in items)
    return f"{len(items)} coverage suggestions: {counts.get('Add', 0)} add, {counts.get('Delete', 0)} delete. Sources: GSC {source_status['gsc']}, GA4 {source_status['ga4']}, Trends {source_status['trends']}."


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


def _semantic_key(text: str) -> str:
    return " ".join(sorted(_tokens(text))[:8])


def _similar(a: str, b: str) -> bool:
    aw = _tokens(a)
    bw = _tokens(b)
    return bool(aw and bw and len(aw & bw) >= min(2, len(aw)))


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)
