from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class ClinicCatalogue(Base):
    __tablename__ = "clinic_catalogue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_current: Mapped[bool] = mapped_column(default=True)


class Call(Base):
    __tablename__ = "calls"

    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    from_number_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, default="active")
    final_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow_stage: Mapped[str] = mapped_column(String, default="identify")
    workflow_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CallEvent(Base):
    __tablename__ = "call_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.call_id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ApiObservation(Base):
    __tablename__ = "api_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str | None] = mapped_column(
        ForeignKey("calls.call_id"), index=True, nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    endpoint: Mapped[str] = mapped_column(String)
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.call_id"), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String)
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
