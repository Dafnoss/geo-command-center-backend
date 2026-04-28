from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import prompt_research, schemas
from app.database import get_db


router = APIRouter(prefix="/prompt-research", tags=["prompt-research"])


@router.post("/run", response_model=schemas.PromptResearchOut)
def run_research(data: schemas.PromptResearchRunRequest, db: Session = Depends(get_db)):
    try:
        return prompt_research.run_prompt_research(db, count=data.count)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.get("/latest", response_model=schemas.PromptResearchOut)
def latest_research(db: Session = Depends(get_db)):
    return prompt_research.latest_research(db)


@router.post("/{batch_id}/apply", response_model=schemas.PromptResearchApplyOut)
def apply_research(batch_id: str, data: schemas.PromptResearchApplyRequest, db: Session = Depends(get_db)):
    if not data.item_ids:
        raise HTTPException(400, "Select at least one research item.")
    return prompt_research.apply_research(db, batch_id=batch_id, item_ids=data.item_ids)


@router.post("/{batch_id}/items/{item_id}/approve")
def approve_research_item(batch_id: str, item_id: str, db: Session = Depends(get_db)):
    try:
        return prompt_research.approve_item(db, batch_id=batch_id, item_id=item_id, run_after_add=True)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{batch_id}/items/{item_id}/reject")
def reject_research_item(batch_id: str, item_id: str, db: Session = Depends(get_db)):
    try:
        return prompt_research.reject_item(db, batch_id=batch_id, item_id=item_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
