"""Local mock data source.

Behaviour under test:
- pagination via opaque page_token, a fixed snapshot_token per round, and an
  accurate total_count on every response;
- switchable scenarios: normal data changes (add/delete/modify/re-add),
  duplicate page, missing page and mid-stream disconnect.

Faults are properties of a frozen snapshot round so that a collector can
resume the *same* snapshot after a transient failure.
"""
from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/source", tags=["datasource"])

PAGE_SIZE = 5
SCENARIOS = ("normal", "duplicate_page", "missing_page", "disconnect", "wrong_total")
MUTATIONS = ("add", "delete", "modify", "readd")


def encode_token(snapshot_token: str, offset: int) -> str:
    raw = json.dumps({"snap": snapshot_token, "off": offset}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_token(token: str) -> dict:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode()))
        if not isinstance(data, dict) or "snap" not in data or "off" not in data:
            raise ValueError
        return data
    except Exception:
        raise HTTPException(status_code=400, detail="invalid page_token")


class FrozenRound:
    def __init__(self, token: str, records: list[dict], scenario: str, page_size: int):
        self.token = token
        self.records = records  # immutable, sorted view
        self.scenario = scenario
        self.page_size = page_size
        # wrong_total lies consistently (open call and every page) so only the
        # end-of-stream count gate can catch the discrepancy
        self.declared_total = len(records) + (2 if scenario == "wrong_total" else 0)
        # disconnect is transient: it fires exactly once per round, then the
        # retry of the same page succeeds so the same snapshot can be completed.
        self.disconnect_fired = False
        self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


class MockSource:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._add_counter = 0
        self._deleted: list[dict] = []  # keys removed, available for re-add
        self.scenario = "normal"
        self.rounds: dict[str, FrozenRound] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._records = {
                f"K{i:04d}": {"business_key": f"K{i:04d}", "version": "v1",
                              "payload": f"item {i:04d} baseline"}
                for i in range(1, 13)  # 12 records -> 3 pages of 5
            }
            self._add_counter = 0
            self._deleted = []
            self.scenario = "normal"
            self.rounds.clear()

    # -- dataset mutations (affect the *next* snapshot round) ---------------
    def mutate(self, action: str, count: int) -> dict:
        with self._lock:
            if action == "add":
                added = []
                for _ in range(count):
                    self._add_counter += 1
                    key = f"NEW{self._add_counter:03d}"
                    self._records[key] = {"business_key": key, "version": "v1",
                                          "payload": f"newly added {key}"}
                    added.append(key)
                return {"added": added}
            if action == "delete":
                # delete from the middle so the effect is visible in diffs
                keys = sorted(self._records)
                targets = keys[len(keys) // 2: len(keys) // 2 + count]
                for key in targets:
                    self._deleted.append(self._records.pop(key))
                return {"deleted": targets}
            if action == "modify":
                keys = sorted(self._records)[:count]
                for key in keys:
                    rec = self._records[key]
                    n = int(rec["version"].lstrip("v")) + 1
                    rec["version"] = f"v{n}"
                    rec["payload"] = f"item {key} revision {n}"
                return {"modified": keys}
            if action == "readd":
                if not self._deleted:
                    raise HTTPException(409, "no deleted key available to re-add")
                targets = self._deleted[:count]
                for rec in targets:
                    self._records[rec["business_key"]] = rec
                self._deleted = self._deleted[count:]
                return {"readded": [r["business_key"] for r in targets]}
            raise HTTPException(400, f"unknown action {action}")

    def open_snapshot(self) -> FrozenRound:
        with self._lock:
            token = f"snap-{uuid.uuid4().hex[:12]}"
            frozen = [dict(r) for r in self._records.values()]
            frozen.sort(key=lambda r: r["business_key"])
            rnd = FrozenRound(token, frozen, self.scenario, PAGE_SIZE)
            self.rounds[token] = rnd
            return rnd

    def fetch_page(self, page_token: str) -> dict:
        data = decode_token(page_token)
        with self._lock:
            rnd = self.rounds.get(data["snap"])
            if rnd is None:
                raise HTTPException(404, detail="unknown or expired snapshot_token")
            offset = data["off"]
            if offset < 0 or offset > len(rnd.records):
                raise HTTPException(400, detail="cursor out of range")
            ps = rnd.page_size
            true_index = offset // ps
            # faults strike at the second page so they still fire after the
            # dataset shrinks below three pages
            fault_offset = ps

            if rnd.scenario == "disconnect" and offset == fault_offset and not rnd.disconnect_fired:
                rnd.disconnect_fired = True
                raise HTTPException(503, "simulated upstream disconnect mid-stream")

            served_index = true_index
            skipped = False
            if offset == fault_offset:
                if rnd.scenario == "missing_page":
                    # silently skip one page: index jumps forward, cursor still
                    # advances so only an index-continuity check catches it
                    served_index = true_index + 1
                    skipped = True
                elif rnd.scenario == "duplicate_page":
                    # serve the previous page again and hand back its own cursor:
                    # neither page_index nor the cursor advances
                    served_index = true_index - 1

            start = served_index * ps
            page_records = rnd.records[start:start + ps]
            next_off = offset + ps
            if skipped and start >= len(rnd.records):
                # the skipped page was the last real page: stream ends early,
                # which also fails the collector's total-count gate
                next_token = None
            else:
                next_token = encode_token(rnd.token, next_off) if next_off < len(rnd.records) else None
            return {
                "snapshot_token": rnd.token,
                "page_index": served_index,
                "page_token": page_token,
                "records": [dict(r) for r in page_records],
                "next_page_token": next_token,
                # wrong_total: the round consistently reports an inflated total;
                # the collector's end-of-stream count gate must reject it
                "total_count": rnd.declared_total,
            }

    def status(self) -> dict:
        with self._lock:
            return {
                "scenario": self.scenario,
                "record_count": len(self._records),
                "page_size": PAGE_SIZE,
                "deleted_pool": [r["business_key"] for r in self._deleted],
                "open_rounds": [
                    {"snapshot_token": t, "total_count": len(r.records),
                     "scenario": r.scenario, "created_at": r.created_at}
                    for t, r in sorted(self.rounds.items())
                ],
            }


source = MockSource()


class ScenarioIn(BaseModel):
    scenario: str = Field(
        description="one of normal/duplicate_page/missing_page/disconnect/wrong_total")


class MutateIn(BaseModel):
    action: str
    count: int = 1


@router.get("/status")
def source_status():
    return source.status()


@router.post("/scenario")
def set_scenario(body: ScenarioIn):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"scenario must be one of {SCENARIOS}")
    source.scenario = body.scenario
    return {"scenario": source.scenario}


@router.post("/mutate")
def mutate_dataset(body: MutateIn):
    if body.action not in MUTATIONS:
        raise HTTPException(400, f"action must be one of {MUTATIONS}")
    if body.count < 1:
        raise HTTPException(400, "count must be >= 1")
    result = source.mutate(body.action, body.count)
    return {**result, "record_count": len(source._records)}


@router.post("/reset")
def reset_source():
    source.reset()
    return {"ok": True}


@router.post("/snapshot")
def open_snapshot():
    rnd = source.open_snapshot()
    first_token = encode_token(rnd.token, 0)
    return {
        "snapshot_token": rnd.token,
        "page_size": rnd.page_size,
        "total_count": rnd.declared_total,
        "first_page_token": first_token,
        "scenario": rnd.scenario,
        "created_at": rnd.created_at,
    }


@router.get("/pages")
def get_page(page_token: str):
    return source.fetch_page(page_token)
