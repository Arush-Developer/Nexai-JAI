"""The Data Manager's routes. The browser posts the files a person picked; the backend never opens a client path."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from backend.schemas.contracts import LocalInspectRequest, LocalRunRequest
from backend.services.app_state import AppState
from backend.services.local_data import LocalDataError, LocalDataService

LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
SSE_POLL_SECONDS = 0.2


def local_origin_only(request: Request) -> None:
    """CORS cannot stop a form post from another website, so that origin is refused here."""
    origin = request.headers.get("origin")
    if origin and not LOCAL_ORIGIN.match(origin):
        raise HTTPException(403, "This local API only accepts requests from the local website.")


router = APIRouter(prefix="/api/local-data", dependencies=[Depends(local_origin_only)])


def service_of(request: Request) -> LocalDataService:
    # Independent of the dashboard's state, so this works on a laptop with no copy of the official dataset.
    state = request.app.state
    if getattr(state, "local_data", None) is None:
        state.local_data = LocalDataService()
    return state.local_data


def _guard(call):
    try:
        return call()
    except LocalDataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/status")
def status(request: Request):
    return service_of(request).status()


@router.post("/upload")
def upload(request: Request, dataset_type: str = Form(...), files: list[UploadFile] = File(...),
           relative_paths: list[str] = Form(default=[])):
    """Multipart upload into a new run's staging directory. Starlette spools parts to disk, not memory."""
    paths = relative_paths if len(relative_paths) == len(files) else [f.filename for f in files]
    uploads = [(f.filename or "unnamed", path, f.file) for f, path in zip(files, paths)]
    return _guard(lambda: service_of(request).create_upload(dataset_type, uploads))


@router.post("/inspect")
def inspect(request: Request, body: LocalInspectRequest):
    return _guard(lambda: service_of(request).inspect(body.run_id, body.include))


@router.post("/run")
def run(request: Request, body: LocalRunRequest):
    return _guard(lambda: service_of(request).start_run(body.run_id, body.include, body.on_order_conflict,
                                                        body.allow_duplicate))


@router.get("/runs/{run_id}")
def run_view(request: Request, run_id: str):
    return _guard(lambda: service_of(request).run_view(run_id))


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str, since: int = 0):
    """Server-Sent Events of a run's real progress. Closes after the run's final event."""
    service = service_of(request)
    _guard(lambda: service.manifest(run_id))

    async def stream():
        seq = since
        while True:
            batch = service.events_since(run_id, seq)
            for event in batch:
                seq = event["seq"]
                yield f"id: {seq}\nevent: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
            if service.is_finished(run_id) and not service.events_since(run_id, seq):
                yield "event: closed\ndata: {}\n\n"
                return
            if await request.is_disconnected():
                return
            if not batch:
                yield ": keepalive\n\n"
                await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/validate")
def validate(request: Request, run_id: str):
    return _guard(lambda: service_of(request).validate(run_id))


@router.post("/runs/{run_id}/open-in-dashboard")
def open_in_dashboard(request: Request, run_id: str):
    """Point every dashboard screen at this run's input until the backend restarts. The saved replay is not touched."""
    service = service_of(request)
    partition, name = _guard(lambda: service.dashboard_input(run_id))
    pipeline = service.pipeline()
    state = AppState(pipeline, pipeline.analyse(partition), persist=False, replay_input=name)
    previous = getattr(request.app.state, "cs", None)
    if previous is not None:
        previous.replay.stop()
    request.app.state.cs = state
    request.app.state.load_error = None
    return {"replay_input": name, "records": len(partition.metadata), "defense_available": state.replay.defense_available}


@router.get("/runs/{run_id}/files/{name}")
def output_file(request: Request, run_id: str, name: str):
    return FileResponse(_guard(lambda: service_of(request).output_path(run_id, name)), filename=name)


@router.get("/runs/{run_id}/archive")
def archive(request: Request, run_id: str, submission_only: bool = False):
    path = _guard(lambda: service_of(request).archive(run_id, submission_only))
    return FileResponse(path, media_type="application/zip", filename=path.name)
