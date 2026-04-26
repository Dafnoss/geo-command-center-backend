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
from app.visibility import brand_success_terms, derive_monitor_status, detect_terms, split_csv


router = APIRouter(prefix="/ai-results", tags=["ai_results"])


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


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
    target_brand = _setting(db, "target_brand", "OCSiAl")
    target_product = _setting(db, "target_product", "TUBALL")
    aliases = split_csv(_setting(db, "brand_aliases", ""))
    success_terms = brand_success_terms(target_brand, target_product, aliases)
    brand_mentioned = bool(data.brand_mentioned or detect_terms(data.answer_text, [target_brand]))
    product_mentioned = bool(data.product_mentioned or detect_terms(data.answer_text, [target_product]))
    visible = bool(brand_mentioned or product_mentioned or detect_terms(data.answer_text, success_terms))
    row = models.AiResult(
        result_id=data.result_id or f"AR-{uuid.uuid4().hex[:10]}",
        prompt_id=data.prompt_id,
        platform=data.platform,
        answer_text=data.answer_text,
        brand_mentioned=brand_mentioned,
        product_mentioned=product_mentioned,
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
    prompt.brand_mentioned = brand_mentioned
    prompt.product_mentioned = product_mentioned
    prompt.domain_cited = data.domain_cited
    prompt.competitors_mentioned = list(data.competitors_mentioned)
    prompt.cited_sources = list(data.cited_sources)
    prompt.answer_quality_score = data.answer_quality_score

    prompt.monitor_status = derive_monitor_status(
        visible=visible,
        competitors=list(data.competitors_mentioned),
        domain_cited=data.domain_cited,
    )

    if prompt.monitor_status == "Good":
        for rec in db.query(models.Recommendation).filter(
            models.Recommendation.related_prompt_id == prompt.prompt_id,
            models.Recommendation.status == "New",
        ).all():
            rec.status = "Rejected"

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
