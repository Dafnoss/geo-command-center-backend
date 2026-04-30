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
from app.recommender import generate_recommendations, process_prompt_evidence_recommendations


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
    rows = q.order_by(models.Recommendation.priority_score.desc()).all()
    active = []
    for row in rows:
        if row.status in ("Rejected", "Stale") and not status:
            continue
        if row.related_prompt_id:
            prompt = db.query(models.Prompt).filter_by(prompt_id=row.related_prompt_id).one_or_none()
            if prompt and prompt.monitor_status == "Good" and not status and (row.score_breakdown or {}).get("source") != "cluster_evidence":
                continue
        active.append(row)
    return active


@router.get("/summary", response_model=schemas.RecommendationSummaryOut)
def recommendation_summary(db: Session = Depends(get_db)):
    rows = db.query(models.Recommendation).all()
    active = [r for r in rows if r.status not in ("Rejected", "Stale", "Done", "Task created")]
    return schemas.RecommendationSummaryOut(
        total_active=len(active),
        high_priority=sum(1 for r in active if r.priority_score >= 80),
        gap_driven=sum(1 for r in active if (r.score_breakdown or {}).get("gap_count", 0) > 0),
        risk_driven=sum(1 for r in active if (r.score_breakdown or {}).get("risk_count", 0) > 0),
        cluster_level=sum(1 for r in active if (r.score_breakdown or {}).get("scope") in ("cluster", "opportunity")),
        prompt_level=sum(1 for r in active if (r.score_breakdown or {}).get("scope") not in ("cluster", "opportunity")),
        approved_or_done=sum(1 for r in rows if r.status in ("Accepted", "In Progress", "Done", "Approved", "Task created")),
        rejected=sum(1 for r in rows if r.status in ("Rejected", "Stale")),
    )


@router.post("/process-prompts", response_model=schemas.RecommendationProcessOut)
def process_prompts(db: Session = Depends(get_db)):
    result = process_prompt_evidence_recommendations(db)
    return schemas.RecommendationProcessOut(**result)


@router.post("/cleanup-stale")
def cleanup_stale_recommendations(db: Session = Depends(get_db)):
    """
    Non-destructive maintenance: stale/duplicate active recommendations are marked
    Rejected instead of deleted, so evidence history is preserved.
    """
    rejected_good = 0
    rejected_duplicates = 0
    active_by_prompt: dict[str, list[models.Recommendation]] = {}

    rows = db.query(models.Recommendation).filter(models.Recommendation.status.in_(("New", "Accepted", "In Progress"))).all()
    for row in rows:
        if not row.related_prompt_id:
            continue
        prompt = db.query(models.Prompt).filter_by(prompt_id=row.related_prompt_id).one_or_none()
        if prompt and prompt.monitor_status == "Good":
            row.status = "Stale"
            rejected_good += 1
            continue
        if prompt and prompt.monitor_status in ("Gap", "Risk"):
            active_by_prompt.setdefault(row.related_prompt_id, []).append(row)

    for recs in active_by_prompt.values():
        recs.sort(key=lambda r: (r.priority_score or 0, r.created_at), reverse=True)
        for stale in recs[1:]:
            stale.status = "Stale"
            rejected_duplicates += 1

    db.commit()
    return {
        "rejected_good_prompt_recommendations": rejected_good,
        "rejected_duplicate_prompt_recommendations": rejected_duplicates,
    }


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
    meta = dict(row.score_breakdown or {})
    lifecycle = dict(meta.get("lifecycle") or {})
    if data.notes:
        lifecycle["notes"] = data.notes
    if data.affected_page_url:
        lifecycle["affected_page_url"] = data.affected_page_url
        row.related_url = data.affected_page_url
    if data.expected_prompt_ids:
        lifecycle["expected_prompt_ids"] = data.expected_prompt_ids
    if data.status == "Done":
        lifecycle["completed_at"] = datetime.utcnow().isoformat()
        lifecycle["baseline"] = {
            "coverage_rate": meta.get("coverage_rate"),
            "owned_citation_rate": meta.get("owned_citation_rate"),
            "visibility_rate": meta.get("visibility_rate"),
            "gap_count": meta.get("gap_count"),
            "risk_count": meta.get("risk_count"),
        }
    meta["lifecycle"] = lifecycle
    row.score_breakdown = meta

    # Legacy compatibility: Approved still creates a task, but the new lifecycle
    # uses Accepted/In Progress/Done directly in the recommendation module.
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
    if req.prompt_id:
        prompt = db.query(models.Prompt).filter_by(prompt_id=req.prompt_id).one_or_none()
        if not prompt:
            raise HTTPException(404, "Prompt not found")
        if prompt.monitor_status == "Unchecked":
            raise HTTPException(409, "Run monitoring before generating recommendations.")
        if prompt.monitor_status == "Good":
            raise HTTPException(409, "Prompt already has OCSiAl/TUBALL visibility.")
    new_recs = generate_recommendations(db=db, request=req)
    return new_recs
