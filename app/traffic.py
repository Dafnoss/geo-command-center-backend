from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app import models


AI_SOURCE_PATTERNS = {
    "ChatGPT": ("chatgpt", "openai", "chat.openai"),
    "Perplexity": ("perplexity",),
    "Claude": ("claude", "anthropic"),
    "Gemini": ("gemini", "bard.google"),
    "Copilot": ("copilot", "bing.com/chat", "edgeservices.bing"),
    "DeepSeek": ("deepseek",),
    "You.com": ("you.com",),
    "Phind": ("phind",),
}


def classify_ai_source(*parts: str) -> str | None:
    hay = " ".join(p or "" for p in parts).lower()
    for label, needles in AI_SOURCE_PATTERNS.items():
        if any(n in hay for n in needles):
            return label
    return None


def summarize_ai_traffic(db: Session) -> dict:
    rows = (
        db.query(models.AiTrafficMetric)
        .order_by(models.AiTrafficMetric.date_end.desc(), models.AiTrafficMetric.sessions.desc())
        .all()
    )
    if not rows:
        today = date.today()
        return {
            "date_start": today,
            "date_end": today,
            "total_sessions": 0,
            "total_users": 0,
            "total_conversions": 0.0,
            "source_breakdown": [],
            "landing_pages": [],
            "previous": None,
        }

    latest_end = max(r.date_end for r in rows)
    current = [r for r in rows if r.date_end == latest_end]
    start = min(r.date_start for r in current)
    prev_ends = sorted({r.date_end for r in rows if r.date_end != latest_end}, reverse=True)
    previous_rows = [r for r in rows if prev_ends and r.date_end == prev_ends[0]]

    return _summary(current, start, latest_end) | {
        "previous": _summary(previous_rows, min((r.date_start for r in previous_rows), default=start), prev_ends[0]) if previous_rows else None
    }


def _summary(rows: list[models.AiTrafficMetric], start: date, end: date) -> dict:
    by_source: dict[str, dict] = {}
    by_page: dict[str, dict] = defaultdict(lambda: {"page": "", "sessions": 0, "users": 0, "conversions": 0.0, "sources": set()})
    for row in rows:
        source = by_source.setdefault(row.source, {
            "source": row.source,
            "sessions": 0,
            "users": 0,
            "conversions": 0.0,
        })
        source["sessions"] += row.sessions or 0
        source["users"] += row.active_users or 0
        source["conversions"] += row.conversions or 0.0
        for page in row.landing_pages or []:
            key = page.get("page") or ""
            if not key:
                continue
            target = by_page[key]
            target["page"] = key
            target["sessions"] += page.get("sessions") or 0
            target["users"] += page.get("users") or 0
            target["conversions"] += page.get("conversions") or 0.0
            target["sources"].add(row.source)
    pages = []
    for item in by_page.values():
        item["sources"] = sorted(item["sources"])
        pages.append(item)
    return {
        "date_start": start,
        "date_end": end,
        "total_sessions": sum(r.sessions or 0 for r in rows),
        "total_users": sum(r.active_users or 0 for r in rows),
        "total_conversions": round(sum(r.conversions or 0.0 for r in rows), 2),
        "source_breakdown": sorted(by_source.values(), key=lambda x: x["sessions"], reverse=True),
        "landing_pages": sorted(pages, key=lambda x: x["sessions"], reverse=True)[:10],
    }
