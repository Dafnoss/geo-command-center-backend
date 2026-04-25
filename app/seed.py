"""
Lightweight DB initialization.

For the MVP we only seed Settings rows. Queries and Sources are populated
by the user (queries) and auto-extracted from monitor runs (sources).
"""

from __future__ import annotations

import os
from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app import models


SETTINGS = [
    {"setting_key": "target_brand",         "setting_value": "OCSiAl",                                                                "notes": "Primary brand to monitor in AI answers."},
    {"setting_key": "target_product",       "setting_value": "TUBALL",                                                                "notes": "Primary product name."},
    {"setting_key": "owned_domains",        "setting_value": "ocsial.com,tuball.com",                                                 "notes": "Comma-separated domains treated as 'owned' citations."},
    {"setting_key": "competitors",          "setting_value": "Cabot,Orion,Imerys,LG Chem,Cnano,Nanocyl,Arkema,Showa Denko",          "notes": "Comma-separated competitor list."},
    {"setting_key": "openai_model",         "setting_value": "gpt-4o-mini",                                                            "notes": "OpenAI model used for monitoring + recommendations."},
    {"setting_key": "automation_mode",      "setting_value": "off",                                                                    "notes": "off | manual | weekly"},
    {"setting_key": "monthly_cost_cap_usd", "setting_value": "20",                                                                     "notes": "Soft cap on OpenAI spend per month."},
]


def init_db():
    """Create tables and seed minimal settings only."""
    from app.config import settings as _s
    if _s.database_url.startswith("sqlite:///"):
        os.makedirs("data", exist_ok=True)

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Only seed settings that are missing — never overwrite existing values.
        existing = {s.setting_key for s in db.query(models.Setting).all()}
        for row in SETTINGS:
            if row["setting_key"] not in existing:
                db.add(models.Setting(**row))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("DB initialized.")
