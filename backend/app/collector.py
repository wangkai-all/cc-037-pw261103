"""Collector: pulls pages from the local data source and enforces the
invariants that make a snapshot trustworthy.

A round only becomes *complete* when ALL of the following hold:
  1. every page belongs to the snapshot we opened;
  2. page indices are strictly consecutive (missing pages are detected even
     when the cursor still moves);
  3. every cursor strictly advances (duplicate pages are detected even when
     the duplicated content would simply dedupe away);
  4. every business key is unique within the round;
  5. the source reports the end of the stream (next_page_token is null);
  6. the number of collected records equals the source's total_count.

Any failure is written to fetch_logs as a check record and the provisional
data collected so far stays untouched. Failed rounds never update the
official data: only a committed snapshot becomes "current".
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException

from . import db
from .datasource import decode_token

# The collector talks to the data source over real HTTP (loopback), so the
# same code works against an external source later.
SOURCE_BASE = os.environ.get("SOURCE_BASE", "http://127.0.0.1:8000")
MAX_PAGES = 1000


class CollectionError(Exception):
    def __init__(self, reason: str, status_code: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _source_call(method: str, path: str, query: dict | None = None) -> dict:
    url = SOURCE_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", e.reason)
        except Exception:
            detail = e.reason
        raise CollectionError(f"upstream {e.code}: {detail}", status_code=502)
    except urllib.error.URLError as e:
        raise CollectionError(f"upstream unreachable: {e.reason}", status_code=502)


def _log(conn, snapshot_id: int, seq: int, request_token: str | None,
         page_index, returned_count, next_token, ok: bool, error: str | None) -> None:
    conn.execute(
        """INSERT INTO fetch_logs
           (snapshot_id, seq, request_token, page_index, returned_count,
            next_page_token, ok, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, seq, request_token, page_index, returned_count,
         next_token, 1 if ok else 0, error, db.utcnow()),
    )


def start_snapshot() -> dict:
    info = _source_call("POST", "/api/source/snapshot")
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO snapshots
               (snapshot_token, status, page_size, total_count, next_cursor,
                expected_page_index, created_at)
               VALUES (?, 'collecting', ?, ?, ?, 0, ?)""",
            (info["snapshot_token"], info["page_size"], info["total_count"],
             info["first_page_token"], db.utcnow()),
        )
        snapshot_id = cur.lastrowid
    return get_snapshot(snapshot_id)


def _next_seq(conn, snapshot_id: int) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM fetch_logs WHERE snapshot_id = ?",
                       (snapshot_id,)).fetchone()
    return row["n"]


def _record_failure(snapshot_id: int, seq: int, request_token: str | None,
                    page_index, returned_count, next_token, reason: str) -> None:
    """Persist a failed check in its OWN transaction. Failure records must
    survive even though the page itself is rejected — hence a separate
    connection that commits before the error propagates and rolls back the
    caller's transaction."""
    conn = db.get_conn()
    try:
        _log(conn, snapshot_id, seq, request_token, page_index, returned_count,
             next_token, False, reason)
        conn.execute("UPDATE snapshots SET fail_reason = ? WHERE id = ?",
                     (reason, snapshot_id))
        conn.commit()
    finally:
        conn.close()


def fetch_next_page(snapshot_id: int) -> dict:
    """Fetch and validate one page. On any failure: log a check record,
    leave provisional data and cursor untouched, and raise."""
    # Phase 1: network + validation. A failure commits only its check record.
    conn = db.get_conn()
    closed = False
    try:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if snap is None:
            raise HTTPException(404, "snapshot not found")
        if snap["status"] != "collecting":
            raise HTTPException(409, f"snapshot is {snap['status']}, no more pages")
        if not snap["next_cursor"]:
            raise HTTPException(409, "cursor exhausted; nothing to fetch")

        seq = _next_seq(conn, snapshot_id)
        try:
            page = _source_call("GET", "/api/source/pages",
                                {"page_token": snap["next_cursor"]})
        except CollectionError as e:
            # e.g. simulated 503 disconnect: a check record only, no data moves
            conn.rollback()
            conn.close()
            closed = True
            _record_failure(snapshot_id, seq, snap["next_cursor"], None, None, None, e.reason)
            raise HTTPException(e.status_code, e.reason)

        try:
            _validate_page(conn, snap, page)
        except CollectionError as e:
            conn.rollback()
            conn.close()
            closed = True
            _record_failure(snapshot_id, seq, snap["next_cursor"],
                            page.get("page_index"), len(page.get("records", [])),
                            page.get("next_page_token"), e.reason)
            raise HTTPException(e.status_code, e.reason)
        conn.rollback()  # nothing written yet; validation only
    finally:
        if not closed:
            conn.close()

    # Phase 2: all checks passed — persist provisional records and cursor.
    conn = db.get_conn()
    try:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        new_fetched = snap["fetched_count"] + len(page["records"])
        next_token = page["next_page_token"]

        if next_token is None and new_fetched != snap["total_count"]:
            # Final gate failed. Roll back, then persist the failure in its own
            # transaction so the check record survives — same pattern as every
            # other failure path. This page's provisional rows are not kept.
            reason = (f"count mismatch at end of stream: fetched {new_fetched} "
                      f"!= total {snap['total_count']}")
            conn.rollback()
            conn.close()
            _record_failure(snapshot_id, seq, snap["next_cursor"],
                            page["page_index"], len(page["records"]), None, reason)
            raise HTTPException(409, reason)

        for rec in page["records"]:
            conn.execute(
                """INSERT INTO snapshot_records (snapshot_id, business_key, version, payload)
                   VALUES (?, ?, ?, ?)""",
                (snapshot_id, rec["business_key"], rec["version"],
                 json.dumps(rec, ensure_ascii=False)),
            )
        new_status = "collecting"
        completed_at = None
        if next_token is None:
            new_status = "complete"
            completed_at = db.utcnow()

        _log(conn, snapshot_id, seq, snap["next_cursor"], page["page_index"],
             len(page["records"]), next_token, True, None)
        conn.execute(
            """UPDATE snapshots
               SET fetched_count = ?, next_cursor = ?, expected_page_index = ?,
                   status = ?, fail_reason = NULL, completed_at = COALESCE(completed_at, ?)
               WHERE id = ?""",
            (new_fetched, next_token, page["page_index"] + 1,
             new_status, completed_at, snapshot_id),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.ProgrammingError:
            pass  # connection already closed on the count-mismatch path
        raise
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass
    return get_snapshot(snapshot_id)


def _validate_page(conn, snap, page: dict) -> None:
    # 1. the page must belong to the snapshot we opened
    if page.get("snapshot_token") != snap["snapshot_token"]:
        raise CollectionError(
            f"snapshot mismatch: page belongs to {page.get('snapshot_token')}, "
            f"expected {snap['snapshot_token']}")

    # 2. page index must be exactly the next consecutive one (catches missing
    #    pages where the cursor still advances, and repeated pages)
    if page.get("page_index") != snap["expected_page_index"]:
        got = page.get("page_index")
        if got is not None and got < snap["expected_page_index"]:
            raise CollectionError(
                f"duplicate page detected: expected page index "
                f"{snap['expected_page_index']}, source re-served page {got}")
        raise CollectionError(
            f"missing page detected: expected page index "
            f"{snap['expected_page_index']}, source jumped to {got}")

    # 3. cursor must advance: the returned next token must differ from the one
    #    we sent (catches duplicate pages that repeat the same cursor)
    if page.get("next_page_token") is not None:
        if page["next_page_token"] == snap["next_cursor"]:
            raise CollectionError("cursor did not advance: duplicate page detected")
        try:
            sent = decode_token(snap["next_cursor"])
            nxt = decode_token(page["next_page_token"])
            if nxt["snap"] != snap["snapshot_token"] or nxt["off"] <= sent["off"]:
                raise CollectionError("cursor points backwards or to another snapshot")
        except HTTPException:
            raise CollectionError("source returned an unparseable next_page_token")

    # 4. business keys must be unique inside this page and across the round
    keys = [r["business_key"] for r in page.get("records", [])]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise CollectionError(f"duplicate business key within page: {dupes}")
    for key in keys:
        row = conn.execute(
            "SELECT 1 FROM snapshot_records WHERE snapshot_id = ? AND business_key = ?",
            (snap["id"], key)).fetchone()
        if row is not None:
            raise CollectionError(
                f"duplicate business key across pages: {key} already collected")

    # 5. the source's total must stay consistent with what we opened against
    if page.get("total_count") != snap["total_count"]:
        raise CollectionError(
            f"total_count changed mid-round: opened with {snap['total_count']}, "
            f"page reports {page.get('total_count')}")


def collect_all(snapshot_id: int) -> dict:
    """Keep fetching until the round completes or a check fails. A disconnect
    only records the failure; retrying resumes from the unchanged cursor.
    Always returns the snapshot state — the failed check lives in fetch_logs,
    so callers never receive a raised error for an expected fault scenario."""
    last_error = None
    for _ in range(MAX_PAGES):
        snap = get_snapshot(snapshot_id)
        if snap["status"] != "collecting":
            return snap
        try:
            fetch_next_page(snapshot_id)
        except HTTPException as e:
            last_error = str(e.detail)
            break
    snap = get_snapshot(snapshot_id)
    if snap["status"] == "collecting" and last_error and not snap["fail_reason"]:
        snap["fail_reason"] = last_error
    return snap


# -- diffing & commit -------------------------------------------------------

def _diff_lists(baseline: dict[str, dict], current: dict[str, dict],
                deleted_history: set[str]) -> list[dict]:
    """Classify every key into one of the five diff buckets."""
    diffs: list[dict] = []
    for key in sorted(set(baseline) | set(current)):
        old = baseline.get(key)
        new = current.get(key)
        if old is None and new is not None:
            if key in deleted_history:
                diff_type = "reappeared"  # re-appearance: prior delete history stays
            else:
                diff_type = "added"
        elif old is not None and new is None:
            diff_type = "confirmed_deleted"
        elif old["version"] != new["version"]:
            diff_type = "modified"
        else:
            continue  # unchanged keys are not stored as diff rows
        diffs.append({
            "business_key": key, "diff_type": diff_type,
            "old_version": old["version"] if old else None,
            "new_version": new["version"] if new else None,
        })
    return diffs


def _load_records(conn, snapshot_id: int) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT business_key, version, payload FROM snapshot_records WHERE snapshot_id = ?",
        (snapshot_id,)).fetchall()
    return {r["business_key"]: {"version": r["version"], "payload": r["payload"]} for r in rows}


def commit_snapshot(snapshot_id: int) -> dict:
    with db.transaction() as conn:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if snap is None:
            raise HTTPException(404, "snapshot not found")
        if snap["status"] == "committed":
            # idempotent: re-submitting a batch returns the same result and
            # creates no second round of delete events (UNIQUE backs this too)
            return get_snapshot(snapshot_id)
        if snap["status"] != "complete":
            raise HTTPException(409, "only a complete snapshot can be committed")

        baseline_row = conn.execute(
            "SELECT id FROM snapshots WHERE is_current = 1 AND id != ? ORDER BY committed_at DESC LIMIT 1",
            (snapshot_id,)).fetchone()
        baseline_id = baseline_row["id"] if baseline_row else None
        current = _load_records(conn, snapshot_id)
        now = db.utcnow()

        if baseline_id is None:
            # first successful collection: baseline, nothing can be "deleted"
            diffs = [{"business_key": k, "diff_type": "added",
                      "old_version": None, "new_version": v["version"]}
                     for k, v in sorted(current.items())]
        else:
            baseline = _load_records(conn, baseline_id)
            history_rows = conn.execute("SELECT DISTINCT business_key FROM delete_events").fetchall()
            diffs = _diff_lists(baseline, current, {r["business_key"] for r in history_rows})

        for d in diffs:
            # OR IGNORE backs the API-level idempotency: even if two commits
            # raced past the status check, the UNIQUE(snapshot_id, business_key)
            # constraint would not create duplicate diff rows
            conn.execute(
                """INSERT OR IGNORE INTO diff_records
                   (snapshot_id, baseline_id, business_key, diff_type,
                    old_version, new_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, baseline_id, d["business_key"], d["diff_type"],
                 d["old_version"], d["new_version"], now))
            if d["diff_type"] == "confirmed_deleted":
                # append-only; UNIQUE(snapshot_id, business_key) makes a
                # duplicate batch submission unable to insert a second event
                conn.execute(
                    """INSERT OR IGNORE INTO delete_events
                       (business_key, snapshot_id, baseline_id, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (d["business_key"], snapshot_id, baseline_id, now))

        # single transaction: switch the current pointer and record the diff
        conn.execute("UPDATE snapshots SET is_current = 0 WHERE is_current = 1")
        conn.execute(
            """UPDATE snapshots
               SET status = 'committed', is_current = 1, committed_at = ?,
                   fail_reason = NULL
               WHERE id = ?""",
            (now, snapshot_id))
    return get_snapshot(snapshot_id)


# -- read models ------------------------------------------------------------

def get_snapshot(snapshot_id: int) -> dict:
    conn = db.get_conn()
    try:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if snap is None:
            raise HTTPException(404, "snapshot not found")
        data = dict(snap)
        logs = conn.execute(
            "SELECT * FROM fetch_logs WHERE snapshot_id = ? ORDER BY seq",
            (snapshot_id,)).fetchall()
        data["fetch_logs"] = [dict(r) for r in logs]
        return data
    finally:
        conn.close()


def list_snapshots() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM diff_records d WHERE d.snapshot_id = s.id) AS diff_count
               FROM snapshots s ORDER BY s.id""").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def provisional_diff(snapshot_id: int) -> dict:
    """For an in-progress round: what the diff *would* look like right now.

    Keys collected so far but... — actually: keys present in the current
    baseline and absent from the provisional data are "suspected_missing".
    They stay a suspicion until a complete round is committed, at which point
    they become confirmed deletions (or reappear later)."""
    conn = db.get_conn()
    try:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if snap is None:
            raise HTTPException(404, "snapshot not found")

        if snap["status"] == "committed":
            rows = conn.execute(
                "SELECT business_key, diff_type, old_version, new_version, baseline_id FROM diff_records WHERE snapshot_id = ? ORDER BY business_key",
                (snapshot_id,)).fetchall()
            baseline_id = rows[0]["baseline_id"] if rows else None
            return {"snapshot_id": snapshot_id, "provisional": False,
                    "baseline_id": baseline_id,
                    "diff": [{k: r[k] for k in ("business_key", "diff_type", "old_version", "new_version")}
                             for r in rows]}

        baseline_row = conn.execute(
            "SELECT id FROM snapshots WHERE is_current = 1 ORDER BY committed_at DESC LIMIT 1").fetchone()
        if baseline_row is None:
            return {"snapshot_id": snapshot_id, "provisional": True,
                    "baseline_id": None, "diff": [],
                    "note": "no baseline yet; first successful collection establishes it"}
        baseline = _load_records(conn, baseline_row["id"])
        current = _load_records(conn, snapshot_id)
        history_rows = conn.execute("SELECT DISTINCT business_key FROM delete_events").fetchall()
        diff = _diff_lists(baseline, current, {r["business_key"] for r in history_rows})
        # downgrade any deletion-looking bucket to suspected_missing while the
        # round is still incomplete (only confirmed_deleted can occur here)
        for d in diff:
            if d["diff_type"] == "confirmed_deleted":
                d["diff_type"] = "suspected_missing"
        return {"snapshot_id": snapshot_id, "provisional": True,
                "baseline_id": baseline_row["id"], "diff": diff}
    finally:
        conn.close()


def compare_snapshots(left_id: int, right_id: int) -> dict:
    """Ad-hoc comparison of any two committed (or complete) snapshots."""
    conn = db.get_conn()
    try:
        left = conn.execute("SELECT * FROM snapshots WHERE id = ?", (left_id,)).fetchone()
        right = conn.execute("SELECT * FROM snapshots WHERE id = ?", (right_id,)).fetchone()
        if left is None or right is None:
            raise HTTPException(404, "snapshot not found")
        left_records = _load_records(conn, left_id)
        right_records = _load_records(conn, right_id)
        history_rows = conn.execute("SELECT DISTINCT business_key FROM delete_events").fetchall()
        diff = _diff_lists(left_records, right_records, {r["business_key"] for r in history_rows})
        return {"left_snapshot_id": left_id, "right_snapshot_id": right_id,
                "left_token": left["snapshot_token"], "right_token": right["snapshot_token"],
                "diff": diff}
    finally:
        conn.close()


def delete_history(business_key: str | None = None) -> list[dict]:
    conn = db.get_conn()
    try:
        if business_key:
            rows = conn.execute(
                """SELECT de.*, s.snapshot_token FROM delete_events de
                   JOIN snapshots s ON s.id = de.snapshot_id
                   WHERE de.business_key = ? ORDER BY de.id""", (business_key,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT de.*, s.snapshot_token FROM delete_events de
                   JOIN snapshots s ON s.id = de.snapshot_id ORDER BY de.id""").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def current_records() -> list[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM snapshots WHERE is_current = 1").fetchone()
        if row is None:
            return []
        records = _load_records(conn, row["id"])
        return [{"business_key": k, **v} for k, v in sorted(records.items())]
    finally:
        conn.close()
