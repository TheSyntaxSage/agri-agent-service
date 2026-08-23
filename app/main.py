from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from statistics import fmean
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import require_identity
from app.config import Settings, get_settings
from app.database import FeedbackRecord, create_session_factory
from app.java_client import JavaApiUnavailable
from app.models import (
    DecisionOutput,
    DecisionRequest,
    ErrorAnalysis,
    ErrorMetric,
    FeedbackCreate,
    FeedbackRead,
    HealthResponse,
    IdentityContext,
    Intent,
)
from app.workflow import DecisionWorkflow, LoopDetected, PermissionDenied, WorkflowError


def create_app(settings: Settings | None = None, workflow: DecisionWorkflow | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.session_factory = create_session_factory(app_settings)
        app.state.workflow = workflow or DecisionWorkflow(app_settings)
        yield

    app = FastAPI(title=app_settings.app_name, version="0.1.0", lifespan=lifespan)

    def get_db(request: Request):
        factory: sessionmaker[Session] = request.app.state.session_factory
        with factory() as session:
            yield session

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            deterministic=True,
            demo_fallback_enabled=app_settings.demo_fallback_enabled,
        )

    @app.post("/api/v1/decisions", response_model=DecisionOutput, response_model_by_alias=True)
    async def decide(
        payload: DecisionRequest,
        request: Request,
        identity: IdentityContext = Depends(require_identity),
    ) -> DecisionOutput:
        try:
            return await request.app.state.workflow.run(payload, identity)
        except PermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except LoopDetected as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except JavaApiUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/api/v1/feedback", response_model=FeedbackRead, response_model_by_alias=True, status_code=201)
    async def create_feedback(
        payload: FeedbackCreate,
        db: Session = Depends(get_db),
        _: IdentityContext = Depends(require_identity),
    ) -> FeedbackRecord:
        record = FeedbackRecord(
            trace_id=payload.trace_id,
            intent=payload.intent.value,
            accepted=payload.accepted,
            predicted_value=payload.predicted_value,
            actual_value=payload.actual_value,
            comment=payload.comment,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @app.get("/api/v1/feedback", response_model=list[FeedbackRead], response_model_by_alias=True)
    async def list_feedback(
        trace_id: str | None = Query(default=None),
        db: Session = Depends(get_db),
        _: IdentityContext = Depends(require_identity),
    ) -> list[FeedbackRecord]:
        statement = select(FeedbackRecord).order_by(FeedbackRecord.id.desc())
        if trace_id:
            statement = statement.where(FeedbackRecord.trace_id == trace_id)
        return list(db.scalars(statement).all())

    @app.get("/api/v1/error-analysis", response_model=ErrorAnalysis, response_model_by_alias=True)
    async def error_analysis(
        db: Session = Depends(get_db),
        _: IdentityContext = Depends(require_identity),
    ) -> ErrorAnalysis:
        records = list(db.scalars(select(FeedbackRecord)).all())
        grouped: dict[str, list[FeedbackRecord]] = defaultdict(list)
        for record in records:
            grouped[record.intent].append(record)
        metrics: list[ErrorMetric] = []
        for intent_name, rows in sorted(grouped.items()):
            paired = [row for row in rows if row.predicted_value is not None and row.actual_value is not None]
            absolute_errors = [abs(row.actual_value - row.predicted_value) for row in paired]  # type: ignore[operator]
            percentage_errors = [
                abs(row.actual_value - row.predicted_value) / abs(row.actual_value) * 100  # type: ignore[operator]
                for row in paired
                if row.actual_value != 0
            ]
            accepted_values = [row.accepted for row in rows if row.accepted is not None]
            metrics.append(
                ErrorMetric(
                    intent=Intent(intent_name),
                    sample_count=len(rows),
                    mean_absolute_error=round(fmean(absolute_errors), 4) if absolute_errors else None,
                    mean_absolute_percentage_error=round(fmean(percentage_errors), 4) if percentage_errors else None,
                    acceptance_rate=round(sum(accepted_values) / len(accepted_values), 4) if accepted_values else None,
                )
            )
        return ErrorAnalysis(total_feedback=len(records), metrics=metrics)

    return app


app = create_app()

