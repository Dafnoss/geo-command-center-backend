"""
Prompt CRUD + filters.
"""

from __future__ import annotations

import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.scoring import (
    ai_visibility_score,
    competitor_pressure_score,
)


router = APIRouter(prefix="/prompts", tags=["prompts"])


def _priority_label_from_business(n: int) -> str:
    if n >= 5:
        return "High"
    if n >= 3:
        return "Medium"
    return "Low"


@router.get("", response_model=List[schemas.PromptOut])
def list_prompts(
    db: Session = Depends(get_db),
    cluster: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    monitor_status: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
):
    q = db.query(models.Prompt)
    if cluster:
        q = q.filter(models.Prompt.topic_cluster == cluster)
    if platform:
        q = q.filter(models.Prompt.platform == platform)
    if status:
        q = q.filter(models.Prompt.status == status)
    if monitor_status:
        q = q.filter(models.Prompt.monitor_status == monitor_status)
    if priority:
        q = q.filter(models.Prompt.priority == priority)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Prompt.prompt_text.ilike(like))
    return q.order_by(models.Prompt.business_priority.desc(), models.Prompt.prompt_id).all()


@router.get("/{prompt_id}", response_model=schemas.PromptOut)
def get_prompt(prompt_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Prompt).filter_by(prompt_id=prompt_id).one_or_none()
    if not row:
        raise HTTPException(404, "Prompt not found")
    return row


@router.post("", response_model=schemas.PromptOut, status_code=201)
def create_prompt(data: schemas.PromptCreate, db: Session = Depends(get_db)):
    if db.query(models.Prompt).filter_by(prompt_id=data.prompt_id).first():
        raise HTTPException(409, "prompt_id already exists")
    row = models.Prompt(
        prompt_id=data.prompt_id,
        prompt_text=data.prompt_text,
        topic_cluster=data.topic_cluster,
        country=data.country,
        language=data.language,
        business_priority=data.business_priority,
        target_brand=data.target_brand,
        target_product=data.target_product,
        target_url=data.target_url,
        priority=_priority_label_from_business(data.business_priority),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{prompt_id}", response_model=schemas.PromptOut)
def update_prompt(prompt_id: str, data: schemas.PromptUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Prompt).filter_by(prompt_id=prompt_id).one_or_none()
    if not row:
        raise HTTPException(404, "Prompt not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    if data.business_priority is not None:
        row.priority = _priority_label_from_business(data.business_priority)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Prompt).filter_by(prompt_id=prompt_id).one_or_none()
    if not row:
        raise HTTPException(404, "Prompt not found")
    db.delete(row)
    db.commit()


@router.get("/{prompt_id}/results", response_model=List[schemas.AiResultOut])
def list_results_for_prompt(prompt_id: str, db: Session = Depends(get_db)):
    return (
        db.query(models.AiResult)
        .filter_by(prompt_id=prompt_id)
        .order_by(models.AiResult.date_checked.desc())
        .all()
    )
