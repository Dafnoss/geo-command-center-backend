"""
SQLAlchemy engine + session factory.

Supports two backends out of the box:
- Local SQLite   DATABASE_URL=sqlite:///./data/geo.db
- Postgres       DATABASE_URL=postgresql://user:pass@host:5432/dbname
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _build_url() -> str:
    url = settings.database_url
    # Render injects DATABASE_URL using the older 'postgres://' scheme; SQLAlchemy 2.x
    # only recognizes 'postgresql://'. Normalize.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


_url = _build_url()

# SQLite needs check_same_thread=False with FastAPI's session dependency
connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(_url, connect_args=connect_args, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
