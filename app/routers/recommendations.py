"""
Recommendation CRUD + status update + AI-driven generation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.recommender import generate_recommendations


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=List[schemas.RecommendationOut])
def list_recommendations(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    type: Optional[str] = None,
    related_prompt_id: Optional[str] = None,
    related_url: Optional[str] = None,
    related_source_id: Optional[str] = None,
):
    q = db.query(models.Recommendation)
    if status:
        q = q.filter(models.Recommendation.status == status)
    if type:
        q = q.filter(models.Recommendation.type == type)
    if related_prompt_id:
        q = q.filter(models.Recommendation.related_prompt_id == related_prompt_id)
    if related_url:
        q = q.filter(models.Recommendation.related_url == related_url)
    if related_source_id:
        q = q.filter(models.Recommendation.related_source_id == related_source_id)
    return q.order_by(models.Recommendation.priority_score.desc()).all()


@router.get("/{rec_id}", response_model=schemas.RecommendationOut)
def get_recommendation(rec_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Recommendation).filter_by(recommendation_id=rec_id).one_or_none()
    if not row:
        raise HTTPException(404, "Recommendation not found")
    return row


@router.post("", response_model=schemas.RecommendationOut, status_code=201)
def create_recommendation(data: schemas.RecommendationCreate, db: Session = Depends(get_db)):
    row = models.Recommendation(
        recommendation_id=f"R-{uuid.uuid4().hex[:8].upper()}",
        title=data.title,
        type=data.type,
        diagnosis=data.diagnosis,
        evidence=list(data.evidence),
        recommended_actions=list(data.recommended_actions),
        acceptance_criteria=list(data.acceptance_criteria),
        related_prompt_id=data.related_prompt_id,
        related_url=data.related_url,
        related_source_id=data.related_source_id,
        priority_score=data.priority_score,
        confidence_score=data.confidence_score,
        expected_impact=data.expected_impact,
        status="New",
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{rec_id}/status", response_model=schemas.RecommendationOut)
def update_status(rec_id: str, data: schemas.RecommendationStatusUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Recommendation).filter_by(recommendation_id=rec_id).one_or_none()
    if not row:
        raise HTTPException(404, "Recommendation not found")
    row.status = data.status

    # If approved, auto-create a Task
    if data.status == "Approved":
        existing = db.query(models.Task).filter_by(recommendation_id=rec_id).first()
        if not existing:
            task = models.Task(
                task_id=f"T-{uuid.uuid4().hex[:8].upper()}",
                recommendation_id=rec_id,
                task_title=row.title,
                task_type=row.type,
                owner="",
                owner_initials="",
                status="Approved",
                priority="High" if row.priority_score >= 80 else ("Medium" if row.priority_score >= 50 else "Low"),
                acceptance_criteria=list(row.acceptance_criteria),
                expected_impact=row.expected_impact,
                related_prompt_id=row.related_prompt_id,
                related_url=row.related_url,
                related_source_id=row.related_source_id,
                created_at=datetime.utcnow(),
            )
            db.add(task)
            row.status = "Task created"
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{rec_id}", status_code=204)
def delete_recommendation(rec_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Recommendation).filter_by(recommendation_id=rec_id).one_or_none()
    if not row:
        raise HTTPException(404, "Recommendation not found")
    db.delete(row)
    db.commit()


@router.post("/generate", response_model=List[schemas.RecommendationOut])
def generate(req: schemas.GenerateRequest, db: Session = Depends(get_db)):
    """Generate one or more recommendations using OpenAI for the given target."""
    new_recs = generate_recommendations(db=db, request=req)
    return new_recs
