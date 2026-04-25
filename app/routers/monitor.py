"""
Monitoring runs (manual + cron-driven).
"""

from __future__ import annotations

import os
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from app import models
from app.database import get_db
from app import monitor as monitor_engine


router = APIRouter(prefix="/monitor", tags=["monitor"])


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


@router.post("/run-all")
def run_all(
    db: Session = Depends(get_db),
    x_cron_token: Optional[str] = Header(None),
    force: bool = False,
):
    """Run every active query.

    - Skipped when automation_mode is 'off' unless force=true (set by the UI button).
    - The Render cron job calls this with no token; we honor `x_cron_token` only if
      INTERNAL_CRON_TOKEN env var is set (currently unused, future-proof).
    """
    mode = _setting(db, "automation_mode", "off")
    if mode == "off" and not force:
        raise HTTPException(409, "Automation is off. Switch to manual or weekly first.")

    cron_token_env = os.getenv("INTERNAL_CRON_TOKEN", "")
    if cron_token_env and x_cron_token and x_cron_token != cron_token_env:
        raise HTTPException(401, "Bad cron token")

    summary = monitor_engine.run_all(db)
    # Stamp last_run_at
    last = db.query(models.Setting).filter_by(setting_key="last_run_at").one_or_none()
    if last:
        last.setting_value = datetime.utcnow().isoformat()
    else:
        db.add(models.Setting(setting_key="last_run_at", setting_value=datetime.utcnow().isoformat(), notes="ISO timestamp of the last monitor run."))
    db.commit()
    return summary


@router.post("/run/{prompt_id}")
def run_one(prompt_id: str, db: Session = Depends(get_db)):
    p = db.query(models.Prompt).filter_by(prompt_id=prompt_id).one_or_none()
    if not p:
        raise HTTPException(404, "prompt not found")
    res = monitor_engine.run_query(db, p)
    return res


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Aggregate state for the UI's Run-all button + dashboard cost widget."""
    today = date.today()
    month_total = (
        db.query(func.coalesce(func.sum(models.UsageLog.cost_usd), 0.0))
        .filter(extract("year", models.UsageLog.run_at) == today.year)
        .filter(extract("month", models.UsageLog.run_at) == today.month)
        .scalar()
    )
    runs_this_month = (
        db.query(func.count(models.UsageLog.log_id))
        .filter(extract("year", models.UsageLog.run_at) == today.year)
        .filter(extract("month", models.UsageLog.run_at) == today.month)
        .scalar()
    )
    return {
        "automation_mode": _setting(db, "automation_mode", "off"),
        "monthly_cost_cap_usd": float(_setting(db, "monthly_cost_cap_usd", "0") or 0),
        "month_to_date_cost_usd": float(month_total or 0.0),
        "runs_this_month": int(runs_this_month or 0),
        "last_run_at": _setting(db, "last_run_at", ""),
        "openai_model": _setting(db, "openai_model", "gpt-4o-mini"),
    }
