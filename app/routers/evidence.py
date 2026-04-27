"""
Canonical evidence endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import schemas
from app.database import get_db
from app.evidence import build_cluster_evidence, get_cluster_evidence


router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/clusters", response_model=list[schemas.ClusterEvidenceOut])
def clusters(db: Session = Depends(get_db)):
    return build_cluster_evidence(db)


@router.get("/clusters/{cluster}", response_model=schemas.ClusterEvidenceOut)
def cluster_detail(cluster: str, db: Session = Depends(get_db)):
    row = get_cluster_evidence(db, cluster)
    if not row:
        raise HTTPException(404, "Cluster evidence not found")
    return row
