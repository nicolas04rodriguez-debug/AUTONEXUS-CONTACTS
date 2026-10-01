"""SQLAlchemy engine/session helpers."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

_engine = None
_SessionLocal = None


def get_engine(url: str | None = None):
    global _engine, _SessionLocal
    if _engine is None or url is not None:
        url = url or settings.database_url
        if url.startswith("sqlite:///"):
            path = url.replace("sqlite:///", "", 1)
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
                dbapi_conn.execute("PRAGMA foreign_keys=ON")
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def schema_sql(dialect: str = "sqlite") -> str:
    """Render the CREATE TABLE statements for documentation (schema.sql)."""
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    d = sqlite.dialect() if dialect == "sqlite" else postgresql.dialect()
    out = []
    for table in Base.metadata.sorted_tables:
        out.append(str(CreateTable(table).compile(dialect=d)).strip() + ";")
        for idx in table.indexes:
            out.append(str(CreateIndex(idx).compile(dialect=d)).strip() + ";")
    return "\n\n".join(out) + "\n"
