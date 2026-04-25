"""
Task CRUD + filters.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    return "".join(p[0] for p in parts[:2]).upper()


@router.get("", response_model=List[schemas.TaskOut])
def list_tasks(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    owner: Optional[str] = None,
    task_type: Optional[str] = None,
    search: Optional[str] = None,
):
    q = db.query(models.Task)
    if status:
        q = q.filter(models.Task.status == status)
    if priority:
        q = q.filter(models.Task.priority == priority)
    if owner:
        q = q.filter(models.Task.owner == owner)
    if task_type:
        q = q.filter(models.Task.task_type == task_type)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Task.task_title.ilike(like))
    return q.order_by(models.Task.created_at.desc()).all()


@router.get("/{task_id}", response_model=schemas.TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Task).filter_by(task_id=task_id).one_or_none()
    if not row:
        raise HTTPException(404, "Task not found")
    return row


@router.post("", response_model=schemas.TaskOut, status_code=201)
def create_task(data: schemas.TaskCreate, db: Session = Depends(get_db)):
    row = models.Task(
        task_id=f"T-{uuid.uuid4().hex[:8].upper()}",
        recommendation_id=data.recommendation_id,
        task_title=data.task_title,
        task_type=data.task_type,
        owner=data.owner,
        owner_initials=_initials(data.owner) if data.owner else "",
        status="Recommended",
        due_date=data.due_date,
        priority=data.priority,
        acceptance_criteria=list(data.acceptance_criteria),
        expected_impact=data.expected_impact,
        related_prompt_id=data.related_prompt_id,
        related_url=data.related_url,
        related_source_id=data.related_source_id,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{task_id}", response_model=schemas.TaskOut)
def update_task(task_id: str, data: schemas.TaskUpdate, db: Session = Depends(get_db)):
    row = db.query(models.Task).filter_by(task_id=task_id).one_or_none()
    if not row:
        raise HTTPException(404, "Task not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    if data.owner is not None:
        row.owner_initials = _initials(data.owner) if data.owner else ""
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, db: Session = Depends(get_db)):
    row = db.query(models.Task).filter_by(task_id=task_id).one_or_none()
    if not row:
        raise HTTPException(404, "Task not found")
    db.delete(row)
    db.commit()
