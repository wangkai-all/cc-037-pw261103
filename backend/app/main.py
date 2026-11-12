"""FastAPI entrypoint: data source + collector API + static React build."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import collector, db
from .datasource import router as source_router

app = FastAPI(title="Snapshot Collection & Diff", version="1.0")

# Vite dev server runs on 5173; allow it in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(source_router)


class CompareIn(BaseModel):
    left_snapshot_id: int
    right_snapshot_id: int


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.post("/api/snapshots")
def api_start_snapshot():
    return collector.start_snapshot()


@app.get("/api/snapshots")
def api_list_snapshots():
    return collector.list_snapshots()


@app.get("/api/snapshots/{snapshot_id}")
def api_get_snapshot(snapshot_id: int):
    return collector.get_snapshot(snapshot_id)


@app.post("/api/snapshots/{snapshot_id}/fetch")
def api_fetch_page(snapshot_id: int):
    return collector.fetch_next_page(snapshot_id)


@app.post("/api/snapshots/{snapshot_id}/collect")
def api_collect_all(snapshot_id: int):
    return collector.collect_all(snapshot_id)


@app.post("/api/snapshots/{snapshot_id}/commit")
def api_commit(snapshot_id: int):
    return collector.commit_snapshot(snapshot_id)


@app.get("/api/snapshots/{snapshot_id}/diff")
def api_provisional_diff(snapshot_id: int):
    return collector.provisional_diff(snapshot_id)


@app.post("/api/compare")
def api_compare(body: CompareIn):
    return collector.compare_snapshots(body.left_snapshot_id, body.right_snapshot_id)


@app.get("/api/delete-events")
def api_delete_events(business_key: str | None = None):
    return collector.delete_history(business_key)


@app.get("/api/current-records")
def api_current_records():
    return collector.current_records()


@app.post("/api/reset-all")
def api_reset_all():
    """Full demo reset: wipe the database and re-seed the mock source."""
    from .datasource import source

    source.reset()  # also restores scenario = "normal"
    db.wipe_db()
    return {"ok": True, "scenario": source.scenario}


FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST",
                                    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # Unknown /api paths get a real JSON 404 instead of the SPA shell.
        if full_path.startswith("api/"):
            raise HTTPException(404, detail=f"unknown api route: {full_path}")
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
