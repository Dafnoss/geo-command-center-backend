"""
URL CRUD + filters. Mutating any has_* flag recomputes page_readiness_score.
"""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.scoring import page_readiness_score


router = APIRouter(prefix="/urls", tags=["urls"])


def _recompute(row: models.Url):
    row.page_readiness_score = page_readiness_score({
        "indexable": row.indexable,
        "has_direct_answer": row.has_direct_answer,
        "has_comparison_table": row.has_comparison_table,
        "has_faq": row.has_faq,
        "has_citations": row.has_citations,
        "has_internal_links": row.has_internal_links,
        "has_cta": row.has_cta,
        "has_schema": row.has_schema,
    })


@router.get("", response_model=List[schemas.UrlOut])
def list_urls(
    db: Session = Depends(get_db),
    cluster: Optional[str] = None,
    page_type: Optional[str] = None,
    indexable: Optional[bool] = None,
    search: Optional[str] = None,
):
    q = db.query(models.Url)
    if cluster:
        q = q.filter(models.Url.topic_cluster == cluster)
    if page_type:
        q = q.filter(models.Url.page_type == page_type)
    if indexable is not None:
        q = q.filter(models.Url.indexable == indexable)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Url.url.ilike(like))
    return q.order_by(models.Url.page_readiness_score.desc()).all()


@router.get("/{url_id}", response_model=schemas.UrlOut)
def get_url(url_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Url).filter_by(url_id=url_id).one_or_none()
    if not row:
        raise HTTPException(404, "URL not found")
    return row


@router.patch("/{url_id}", response_model=schemas.UrlOut)
def update_url(url_id: str, data: schemas.UrlUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Url).filter_by(url_id=url_id).one_or_none()
    if not row:
        raise HTTPException(404, "URL not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    _recompute(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{url_id}/seo-metrics", response_model=List[schemas.SeoMetricOut])
def url_seo_metrics(url_id: str, db: Session = Depends(get_db)):
    return (
        db.query(models.SeoMetric)
        .filter_by(url_id=url_id)
        .order_by(models.SeoMetric.date.desc())
        .all()
    )
