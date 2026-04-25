"""
Render Cron Job entry-point.

Runs every Sunday at 09:00 UTC. If automation_mode == 'weekly', it runs every
active query. Otherwise it logs and exits without burning OpenAI credits.
"""

from __future__ import annotations

import sys

from app.database import SessionLocal
from app.seed import init_db
from app import models
from app import monitor as monitor_engine


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        mode = (
            db.query(models.Setting)
            .filter_by(setting_key="automation_mode")
            .one_or_none()
        )
        if not mode or mode.setting_value != "weekly":
            print(f"automation_mode={mode.setting_value if mode else None} — skipping cron run.")
            return 0
        summary = monitor_engine.run_all(db)
        print(f"Ran {summary['count']} queries. Failures: {len(summary['failed'])}. Cost: ${summary['total_cost_usd']:.4f}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
