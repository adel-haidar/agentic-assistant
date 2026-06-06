import logging
from typing import Optional

import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from assistant.job import agent as job_agent
from assistant.job.db import init_pool, list_matches, set_status
from assistant.job.models import RunReport
from assistant.job.report import format_report
from assistant.shared.settings import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])

_latest_report: Optional[RunReport] = None


class StatusUpdate(BaseModel):
    status: str


@router.get("/run")
async def trigger_run(
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    if not settings.database_url:
        raise HTTPException(503, "DATABASE_URL is not configured")
    background_tasks.add_task(_run_job_agent, settings)
    return {
        "status": "started",
        "message": "Job hunt agent running in background. Poll GET /jobs/report for results.",
    }


@router.get("/matches")
async def get_matches(
    tier: Optional[str] = Query(None, description="STRONG_MATCH | GOOD_MATCH"),
    country: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    settings: Settings = Depends(get_settings),
):
    if not settings.database_url:
        raise HTTPException(503, "DATABASE_URL is not configured")
    pool = await init_pool(settings.database_url)
    matches = await list_matches(pool, tier=tier, country=country, status=status)
    return {"count": len(matches), "matches": matches}


@router.post("/matches/{match_id}/status")
async def update_status(
    match_id: int,
    body: StatusUpdate,
    settings: Settings = Depends(get_settings),
):
    if not settings.database_url:
        raise HTTPException(503, "DATABASE_URL is not configured")
    pool = await init_pool(settings.database_url)
    ok = await set_status(pool, match_id, body.status)
    if not ok:
        raise HTTPException(
            400,
            f"Invalid status '{body.status}' or match #{match_id} not found. "
            "Valid: new | reviewing | applied | interviewing | rejected | withdrawn | expired",
        )
    return {"id": match_id, "status": body.status}


@router.get("/report")
async def get_report():
    if _latest_report is None:
        raise HTTPException(
            404, "No completed run yet — call GET /jobs/run to start one."
        )
    return {"report": format_report(_latest_report), "data": _latest_report}


async def _run_job_agent(settings: Settings) -> None:
    global _latest_report

    bedrock_client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    memory_client = None
    graph_client = None

    if settings.mcp_memory_url:
        from assistant.shared.memory_client import MemoryClient

        token = _get_mcp_token(settings)
        memory_client = MemoryClient(
            bedrock_client=bedrock_client,
            model_id=settings.bedrock_model_id,
            server_url=settings.mcp_memory_url,
            token=token,
        )

    try:
        from assistant.email.auth_service import get_token_store
        from assistant.shared.graph_client import GraphClient

        token_store = get_token_store()
        if token_store.is_connected:
            graph_client = GraphClient(token_store=token_store)
    except Exception:
        logger.warning("Microsoft auth not available — email notifications disabled")

    try:
        report = await job_agent.run_agent(
            database_url=settings.database_url,
            bedrock_client=bedrock_client,
            model_id=settings.bedrock_model_id,
            rapidapi_key=settings.rapidapi_key,
            rapidapi_host=settings.rapidapi_host,
            memory_client=memory_client,
            graph_client=graph_client,
            notification_email=settings.notification_email,
            delay_seconds=settings.scraper_delay_seconds,
            max_per_query=settings.scraper_max_results_per_query,
        )
        _latest_report = report
    except Exception:
        logger.exception("Job hunt agent run failed")


def _get_mcp_token(settings: Settings) -> Optional[str]:
    import httpx

    if not settings.mcp_memory_client_id or not settings.mcp_memory_refresh_token:
        return None
    try:
        r = httpx.post(
            "http://localhost:8002/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.mcp_memory_refresh_token,
                "client_id": settings.mcp_memory_client_id,
            },
            timeout=10.0,
        )
        return r.json().get("access_token")
    except Exception:
        logger.warning("Failed to obtain MCP memory token")
        return None
