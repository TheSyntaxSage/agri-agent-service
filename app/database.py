from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings


class Base(DeclarativeBase):
    pass


class FeedbackRecord(Base):
    __tablename__ = "decision_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    intent: Mapped[str] = mapped_column(String(64), index=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    predicted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FeedbackScope(Base):
    """Ownership sidecar. Legacy feedback without a row here is deliberately unowned and unreadable."""

    __tablename__ = "decision_feedback_scope"

    feedback_id: Mapped[int] = mapped_column(ForeignKey("decision_feedback.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128))


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    engine_options = {"poolclass": StaticPool} if settings.database_url == "sqlite:///:memory:" else {}
    engine = create_engine(settings.database_url, connect_args=connect_args, **engine_options)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
