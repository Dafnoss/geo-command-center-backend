"""
Settings (key/value) read + update.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=List[schemas.SettingOut])
def list_settings(db: Session = Depends(get_db)):
    return db.query(models.Setting).order_by(models.Setting.setting_key).all()


@router.get("/{key}", response_model=schemas.SettingOut)
def get_setting(key: str, db: Session = Depends(get_db)):
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    if not row:
        raise HTTPException(404, "Setting not found")
    return row


@router.put("/{key}", response_model=schemas.SettingOut)
def update_setting(key: str, data: schemas.SettingUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    if not row:
        row = models.Setting(setting_key=key, setting_value=data.setting_value, notes="")
        db.add(row)
    else:
        row.setting_value = data.setting_value
    db.commit()
    db.refresh(row)
    return row
