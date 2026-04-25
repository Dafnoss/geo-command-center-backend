"""
AI result CRUD. New rows update the parent prompt's UI snapshot fields.
"""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter(prefix="/ai-results", tags=["ai_results"])


@router.get("", response_model=List[schemas.AiResultOut])
def list_results(db: Session = Depends(get_db)):
    return db.query(models.AiResult).order_by(models.AiResult.date_checked.desc()).all()


@router.get("/{result_id}", response_model=schemas.AiResultOut)
def get_result(result_id: str, db: Session = Depends(get_db)):
    row = db.query(models.AiResult).filter_by(result_id=result_id).one_or_none()
    if not row:
        raise HTTPException(404, "AI result not found")
    return row


@router.post("", response_model=schemas.AiResultOut, status_code=201)
def create_result(data: schemas.AiResultCreate, db: Session = Depends(get_db)):
    prompt = db.query(models.Prompt).filter_by(prompt_id=data.prompt_id).one_or_none()
    if not prompt:
        raise HTTPException(404, "prompt_id not found")
    row = models.AiResult(
        result_id=data.result_id or f"AR-{uuid.uuid4().hex[:10]}",
        prompt_id=data.prompt_id,
        platform=data.platform,
        answer_text=data.answer_text,
        brand_mentioned=data.brand_mentioned,
        product_mentioned=data.product_mentioned,
        domain_cited=data.domain_cited,
        competitors_mentioned=list(data.competitors_mentioned),
        cited_sources=list(data.cited_sources),
        entities=list(data.entities),
        answer_quality_score=data.answer_quality_score,
        notes=data.notes,
    )
    db.add(row)

    # mirror onto prompt for fast list rendering
    prompt.platform = data.platform
    prompt.brand_mentioned = data.brand_mentioned
    prompt.product_mentioned = data.product_mentioned
    prompt.domain_cited = data.domain_cited
    prompt.competitors_mentioned = list(data.competitors_mentioned)
    prompt.cited_sources = list(data.cited_sources)
    prompt.answer_quality_score = data.answer_quality_score

    # naive monitor_status derivation
    if data.brand_mentioned and data.domain_cited:
        prompt.monitor_status = "Good"
    elif not data.brand_mentioned:
        prompt.monitor_status = "Gap"
    elif data.competitors_mentioned and not data.domain_cited:
        prompt.monitor_status = "Risk"
    else:
        prompt.monitor_status = "Needs review"

    db.commit()
    db.refresh(row)
    return row


@router.delete("/{result_id}", status_code=204)
def delete_result(result_id: str, db: Session = Depends(get_db)):
    row = db.query(models.AiResult).filter_by(result_id=result_id).one_or_none()
    if not row:
        raise HTTPException(404, "AI result not found")
    db.delete(row)
    db.commit()
