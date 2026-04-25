"""
Market intelligence endpoints for generating and approving prompt drafts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import intelligence, schemas
from app.database import get_db


router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.post("/generate-drafts", response_model=schemas.DraftBatchOut)
def generate_drafts(data: schemas.GenerateDraftsRequest, db: Session = Depends(get_db)):
    try:
        return intelligence.generate_drafts(db, count=data.count)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/drafts/latest", response_model=schemas.DraftBatchOut)
def latest_drafts(db: Session = Depends(get_db)):
    return intelligence.latest_batch(db)


@router.post("/drafts/{batch_id}/approve", response_model=schemas.ApproveDraftsOut)
def approve_drafts(batch_id: str, data: schemas.ApproveDraftsRequest, db: Session = Depends(get_db)):
    try:
        return intelligence.approve_drafts(db, batch_id=batch_id, draft_ids=data.draft_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/competitors/approve", response_model=schemas.ApproveCompetitorsOut)
def approve_competitors(data: schemas.ApproveCompetitorsRequest, db: Session = Depends(get_db)):
    return intelligence.approve_competitors(db, data.competitors)

