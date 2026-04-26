"""
Dashboard aggregate — KPIs, cluster stats, top items for the Home page.
"""

from __future__ import annotations

from typing import Dict
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.scoring import (
    ai_visibility_score,
    competitor_pressure_score,
)
from app.visibility import is_run_prompt


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# -------- helpers --------
def _kpi(value: int, suffix: str = "", help_text: str = "", trend: list[int] | None = None) -> dict:
    return {
        "value": int(value),
        "suffix": suffix,
        "delta": "",
        "delta_dir": "flat",
        "help": help_text,
        "trend": trend or [],
    }


_STATUS_COLORS = {
    "Recommended": "var(--text-muted)",
    "Approved": "var(--info)",
    "In progress": "var(--warning)",
    "Blocked": "var(--danger)",
    "Done": "var(--success)",
    "Impact check": "var(--success)",
}


@router.get("", response_model=schemas.DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    prompts = db.query(models.Prompt).all()
    run_prompts = [p for p in prompts if is_run_prompt(p)]
    sources = db.query(models.Source).all()
    tasks = db.query(models.Task).all()
    recs = []
    for r in db.query(models.Recommendation).filter(models.Recommendation.status != "Rejected").order_by(models.Recommendation.priority_score.desc()).all():
        if r.related_prompt_id:
            p = db.query(models.Prompt).filter_by(prompt_id=r.related_prompt_id).one_or_none()
            if p and p.monitor_status == "Good":
                continue
        recs.append(r)
        if len(recs) >= 5:
            break

    visibility = ai_visibility_score(run_prompts)
    pressure = competitor_pressure_score(run_prompts, sources)
    domain_citation = (
        round(sum(1 for p in run_prompts if p.domain_cited) / len(run_prompts) * 100)
        if run_prompts else 0
    )
    run_coverage = round(len(run_prompts) / len(prompts) * 100) if prompts else 0
    high_priority_tasks = sum(
        1 for t in tasks if t.priority == "High" and t.status not in ("Done", "Impact check")
    )

    # Cluster stats — average AI visibility per cluster
    by_cluster: Dict[str, list] = defaultdict(list)
    for p in run_prompts:
        by_cluster[p.topic_cluster or "Uncategorized"].append(p)
    cluster_stats = []
    for cluster_name, plist in by_cluster.items():
        cluster_stats.append({
            "name": cluster_name,
            "avg": ai_visibility_score(plist),
        })
    cluster_stats.sort(key=lambda c: c["avg"], reverse=True)

    # Task status segments
    counts = Counter(t.status for t in tasks)
    segments = [
        {"label": label, "value": counts.get(label, 0), "color": color}
        for label, color in _STATUS_COLORS.items()
    ]
    total_tasks = len(tasks)
    completed = sum(1 for t in tasks if t.status in ("Done", "Impact check"))

    # Top gaps — Gap-status prompts ordered by business priority
    top_gaps = (
        db.query(models.Prompt)
        .filter(models.Prompt.monitor_status.in_(("Gap", "Risk")))
        .order_by(models.Prompt.business_priority.desc())
        .limit(5)
        .all()
    )

    # Source opportunities — non-owned high influence sources without good outreach
    source_opps = (
        db.query(models.Source)
        .filter(models.Source.links_to_owned_domain == False)
        .order_by(models.Source.source_influence_score.desc())
        .limit(5)
        .all()
    )

    # Top issue: largest gap cluster (lowest avg visibility, but at least 1 prompt)
    top_issue = ""
    if cluster_stats:
        worst = min(cluster_stats, key=lambda c: c["avg"])
        top_issue = (
            f"{worst['name']} cluster has the lowest OCSiAl/TUBALL visibility ({worst['avg']}/100). "
            f"Prioritize content + citations for this cluster."
        )
    elif prompts:
        top_issue = "Prompts are imported but not run yet. Run monitoring to create evidence-backed recommendations."

    return schemas.DashboardOut(
        top_issue=top_issue,
        imported_prompts=schemas.KpiDelta(**_kpi(len(prompts), "", "Imported monitoring prompts.")),
        run_coverage=schemas.KpiDelta(**_kpi(run_coverage, "%", "Share of imported prompts with at least one AI answer.")),
        ai_visibility=schemas.KpiDelta(**_kpi(visibility, "/100", "OCSiAl/TUBALL mention score across run prompts.")),
        domain_citation=schemas.KpiDelta(**_kpi(domain_citation, "%", "Share of run prompts where an owned domain was cited.")),
        competitor_pressure=schemas.KpiDelta(**_kpi(pressure, "/100", "Competitor mention + citation rate among run prompts.")),
        high_priority_tasks=schemas.KpiDelta(**_kpi(high_priority_tasks, "", "Open High-priority tasks.")),
        cluster_stats=[schemas.ClusterStat(**c) for c in cluster_stats],
        task_status_segments=[schemas.TaskStatusSegment(**s) for s in segments],
        total_tasks=total_tasks,
        completed_tasks=completed,
        avg_time_to_done_days=4.5,  # placeholder — real value would need created→done deltas
        top_gaps=top_gaps,
        top_recommendations=recs,
        source_opportunities=source_opps,
    )
