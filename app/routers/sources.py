"""
Source CRUD + filters.
"""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=List[schemas.SourceOut])
def list_sources(
    db: Session = Depends(get_db),
    source_type: Optional[str] = None,
    outreach_status: Optional[str] = None,
    search: Optional[str] = None,
):
    q = db.query(models.Source)
    if source_type:
        q = q.filter(models.Source.source_type == source_type)
    if outreach_status:
        q = q.filter(models.Source.outreach_status == outreach_status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (models.Source.title.ilike(like))
            | (models.Source.domain.ilike(like))
            | (models.Source.source_url.ilike(like))
        )
    return q.order_by(models.Source.source_influence_score.desc()).all()


@router.get("/{source_id}", response_model=schemas.SourceOut)
def get_source(source_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Source).filter_by(source_id=source_id).one_or_none()
    if not row:
        raise HTTPException(404, "Source not found")
    return row


@router.patch("/{source_id}", response_model=schemas.SourceOut)
def update_source(source_id: str, data: schemas.SourceUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Source).filter_by(source_id=source_id).one_or_none()
    if not row:
        raise HTTPException(404, "Source not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row
