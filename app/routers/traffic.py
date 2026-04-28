from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas
from app.database import get_db
from app.traffic import summarize_ai_traffic


router = APIRouter(prefix="/traffic", tags=["traffic"])


@router.get("/ai/monthly", response_model=schemas.AiTrafficMonthlyOut)
def ai_monthly_traffic(db: Session = Depends(get_db)):
    return summarize_ai_traffic(db)
