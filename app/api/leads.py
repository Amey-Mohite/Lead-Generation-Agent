from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.agents.discovery_pipeline import parse_discovery_queries, run_discovery_sweep
from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.api.auth import require_api_key
from app.api.jobs import Job, JobStore, get_job_store
from app.config import Settings, get_settings
from app.db.repository import build_lead_repository

router = APIRouter(prefix="/v1", tags=["leads"], dependencies=[Depends(require_api_key)])


class LeadRunRequest(BaseModel):
    target: str


class JobAccepted(BaseModel):
    job_id: str
    status: str


def _run_lead_job(job_store: JobStore, job_id: str, settings: Settings, target: str) -> None:
    job_store.mark_running(job_id)
    try:
        orchestrator = build_lead_orchestrator_agent(settings)
        lead = orchestrator.run(target)
        build_lead_repository(settings).save(lead)
        job_store.mark_done(job_id, lead)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc), settings)


@router.post("/leads", status_code=202, response_model=JobAccepted)
def trigger_lead_run(
    body: LeadRunRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    job_store: JobStore = Depends(get_job_store),
) -> JobAccepted:
    job = job_store.create(kind="lead")
    background_tasks.add_task(_run_lead_job, job_store, job.job_id, settings, body.target)
    return JobAccepted(job_id=job.job_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, job_store: JobStore = Depends(get_job_store)) -> Job:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


class DiscoveryRunRequest(BaseModel):
    query: str | None = None
    queries: list[str] | None = None
    max_results: int | None = None


def _resolve_discovery_queries(body: "DiscoveryRunRequest", settings: Settings) -> list[str]:
    if body.queries:
        return body.queries
    if body.query:
        return [body.query]
    configured = parse_discovery_queries(settings.discovery_queries)
    if configured:
        return configured
    return ["credit unions in the UK"]


def _run_discovery_job(
    job_store: JobStore, job_id: str, settings: Settings, queries: list[str],
    max_results: int | None,
) -> None:
    job_store.mark_running(job_id)
    try:
        leads = run_discovery_sweep(settings, queries=queries, max_results=max_results)
        job_store.mark_done(job_id, leads)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc), settings)


@router.post("/discovery", status_code=202, response_model=JobAccepted)
def trigger_discovery_run(
    body: DiscoveryRunRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    job_store: JobStore = Depends(get_job_store),
) -> JobAccepted:
    queries = _resolve_discovery_queries(body, settings)
    job = job_store.create(kind="discovery")
    background_tasks.add_task(
        _run_discovery_job, job_store, job.job_id, settings, queries, body.max_results
    )
    return JobAccepted(job_id=job.job_id, status=job.status)


class LeadRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    company_name: str
    industry: str | None
    status: str
    score: int
    reasoning: str
    summary: str
    key_facts: list[str]
    contacts: list[dict]
    sources: list[str]
    outreach_subject: str | None
    outreach_body: str | None
    approval_status: str | None
    first_seen_at: datetime
    last_seen_at: datetime


@router.get("/leads", response_model=list[LeadRecordOut])
def list_leads(
    status: Literal["qualified", "disqualified"] | None = None,
    approval_status: Literal["pending", "approved", "rejected", "sent"] | None = None,
    limit: int = 50,
    offset: int = 0,
    settings: Settings = Depends(get_settings),
) -> list[LeadRecordOut]:
    repository = build_lead_repository(settings)
    records = repository.list_leads(
        status=status, approval_status=approval_status, limit=limit, offset=offset
    )
    return [LeadRecordOut.model_validate(r) for r in records]


@router.get("/leads/{domain}", response_model=LeadRecordOut)
def get_lead(domain: str, settings: Settings = Depends(get_settings)) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return LeadRecordOut.model_validate(record)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


@router.post("/leads/{domain}/approval", response_model=LeadRecordOut)
def decide_lead_approval(
    domain: str, body: ApprovalDecisionRequest, settings: Settings = Depends(get_settings),
) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if record.approval_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"lead is not pending approval (current status: {record.approval_status})",
        )
    updated = repository.set_approval_status(domain, body.decision)
    return LeadRecordOut.model_validate(updated)


@router.post("/leads/{domain}/sent", response_model=LeadRecordOut)
def mark_lead_sent(domain: str, settings: Settings = Depends(get_settings)) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if record.approval_status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"lead is not approved (current status: {record.approval_status})",
        )
    updated = repository.set_approval_status(domain, "sent")
    return LeadRecordOut.model_validate(updated)
