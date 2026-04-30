from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app import models
from app.visibility import domain_matches_owned, domain_of


def canonical_source_domain(url_or_domain: str) -> str:
    return domain_of(url_or_domain)


def canonical_source_url(domain: str) -> str:
    d = canonical_source_domain(domain)
    return f"https://{d}" if d else ""


def merge_source_types(types: list[str], *, owned: bool = False) -> str:
    clean = sorted({(t or "").strip() for t in types if (t or "").strip()})
    if owned:
        return "Supplier list"
    if not clean:
        return "Unknown"
    if len(clean) == 1:
        return clean[0]
    return "Mixed"


def merge_sources_by_domain(db: Session, owned_domains: list[str] | None = None) -> int:
    rows = db.query(models.Source).all()
    by_domain: dict[str, list[models.Source]] = {}
    for row in rows:
        domain = canonical_source_domain(row.domain or row.source_url)
        if not domain:
            continue
        row.domain = domain
        row.source_url = canonical_source_url(domain)
        row.title = row.title or domain
        by_domain.setdefault(domain, []).append(row)

    merged = 0
    for domain, group in by_domain.items():
        if len(group) <= 1:
            continue
        keep = sorted(
            group,
            key=lambda s: (len(s.cited_by_prompts or []), s.source_influence_score or 0, s.updated or ""),
            reverse=True,
        )[0]
        duplicates = [s for s in group if s.source_id != keep.source_id]
        cited_by = set(keep.cited_by_prompts or [])
        types = [keep.source_type]
        owned = bool(keep.links_to_owned_domain) or domain_matches_owned(domain, owned_domains or [])
        for row in duplicates:
            cited_by.update(row.cited_by_prompts or [])
            types.append(row.source_type)
            keep.mentions_brand = bool(keep.mentions_brand or row.mentions_brand)
            keep.mentions_product = bool(keep.mentions_product or row.mentions_product)
            keep.mentions_competitor = bool(keep.mentions_competitor or row.mentions_competitor)
            keep.links_to_owned_domain = bool(keep.links_to_owned_domain or row.links_to_owned_domain or owned)
            keep.source_influence_score = max(keep.source_influence_score or 0, row.source_influence_score or 0)
            keep.outreach_status = keep.outreach_status or row.outreach_status
            keep.recommended_action = keep.recommended_action or row.recommended_action
            keep.updated = max(keep.updated or "", row.updated or "")
            _repoint_source_references(db, old_id=row.source_id, new_id=keep.source_id)
            db.delete(row)
            merged += 1
        keep.domain = domain
        keep.source_url = canonical_source_url(domain)
        keep.title = domain
        keep.cited_by_prompts = sorted(cited_by)
        keep.source_type = merge_source_types(types, owned=bool(keep.links_to_owned_domain))
        keep.updated = keep.updated or date.today().isoformat()
    if merged:
        db.commit()
    else:
        db.flush()
    return merged


def _repoint_source_references(db: Session, *, old_id: str, new_id: str) -> None:
    for rec in db.query(models.Recommendation).filter(models.Recommendation.related_source_id == old_id).all():
        rec.related_source_id = new_id
    for task in db.query(models.Task).filter(models.Task.related_source_id == old_id).all():
        task.related_source_id = new_id
