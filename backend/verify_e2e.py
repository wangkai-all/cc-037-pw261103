"""End-to-end backend verification (run with -I against the loopback API)."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            detail = json.loads(raw).get("detail")
        except json.JSONDecodeError:
            detail = raw[:200]
        # fail loudly: a silently swallowed error would let the demo proceed
        # against an un-reset data source and produce bogus results
        raise AssertionError(f"{method} {path} -> {e.code}: {detail}")


def counter(diff):
    c = {}
    for d in diff:
        c[d["diff_type"]] = c.get(d["diff_type"], 0) + 1
    return c


# 0. clean slate so the script is runnable on its own
call("POST", "/api/reset-all")

# 1. baseline
s = call("POST", "/api/snapshots")
s = call("POST", f"/api/snapshots/{s['id']}/collect")
assert s["status"] == "complete" and s["fetched_count"] == 12, s
s = call("POST", f"/api/snapshots/{s['id']}/commit")
assert s["is_current"] == 1
baseline_id = s["id"]
print("1. baseline committed:", baseline_id)

# 2. disconnect -> failure record only, official data untouched
call("POST", "/api/source/scenario", {"scenario": "disconnect"})
s2 = call("POST", "/api/snapshots")
s2 = call("POST", f"/api/snapshots/{s2['id']}/collect")
# fault fires on the second page: page 1 (5 rows) lands, page 2 is rejected
assert s2["status"] == "collecting" and s2["fetched_count"] == 5, s2
logs = call("GET", f"/api/snapshots/{s2['id']}")["fetch_logs"]
assert any(not l["ok"] and "503" in (l["error"] or "") for l in logs), logs
pd = call("GET", f"/api/snapshots/{s2['id']}/diff")
assert pd["provisional"] and counter(pd["diff"]) == {"suspected_missing": 7}, pd
assert len(call("GET", "/api/current-records")) == 12
print("2. disconnect logged, 7 suspected_missing, official data still 12")

# 3. resume same snapshot -> complete -> commit, missing become... nothing (same data)
call("POST", "/api/source/scenario", {"scenario": "normal"})
s2 = call("POST", f"/api/snapshots/{s2['id']}/collect")
assert s2["status"] == "complete" and s2["fetched_count"] == 12, s2
logs = call("GET", f"/api/snapshots/{s2['id']}")["fetch_logs"]
# page 1 ok, page 2 failed once, then pages 2 and 3 succeed on retry
assert len(logs) == 4 and sum(1 for l in logs if not l["ok"]) == 1, logs
s2 = call("POST", f"/api/snapshots/{s2['id']}/commit")
assert s2["is_current"] == 1
assert call("GET", "/api/delete-events") == []
# idempotent re-commit
call("POST", f"/api/snapshots/{s2['id']}/commit")
assert call("GET", "/api/delete-events") == []
print("3. resumed & committed; no delete events; re-commit idempotent")

# 4. delete 2 -> confirmed deletions + delete events
r = call("POST", "/api/source/mutate", {"action": "delete", "count": 2})
deleted = r["deleted"]
s3 = call("POST", "/api/snapshots")
s3 = call("POST", f"/api/snapshots/{s3['id']}/collect")
assert s3["status"] == "complete"
s3 = call("POST", f"/api/snapshots/{s3['id']}/commit")
d3 = call("GET", f"/api/snapshots/{s3['id']}/diff")["diff"]
assert counter(d3) == {"confirmed_deleted": 2}, d3
events = call("GET", "/api/delete-events")
assert {e["business_key"] for e in events} == set(deleted) and len(events) == 2
print("4. deletions confirmed:", deleted)

# 5. add + modify + re-add -> reappeared keeps history
call("POST", "/api/source/mutate", {"action": "add", "count": 2})
call("POST", "/api/source/mutate", {"action": "modify", "count": 1})
readd = call("POST", "/api/source/mutate", {"action": "readd", "count": 1})["readded"]
s4 = call("POST", "/api/snapshots")
s4 = call("POST", f"/api/snapshots/{s4['id']}/collect")
s4 = call("POST", f"/api/snapshots/{s4['id']}/commit")
d4 = call("GET", f"/api/snapshots/{s4['id']}/diff")["diff"]
assert counter(d4) == {"added": 2, "modified": 1, "reappeared": 1}, d4
hist = call("GET", f"/api/delete-events?business_key={readd[0]}")
assert len(hist) == 1 and hist[0]["snapshot_id"] == s3["id"]
print("5. reappeared key", readd, "keeps delete history")

# 6. duplicate page
call("POST", "/api/source/scenario", {"scenario": "duplicate_page"})
s5 = call("POST", "/api/snapshots")
s5 = call("POST", f"/api/snapshots/{s5['id']}/collect")
assert s5["status"] == "collecting" and "duplicate page" in s5["fail_reason"], s5
logs5 = call("GET", f"/api/snapshots/{s5['id']}")["fetch_logs"]
assert any(not l["ok"] and "duplicate" in l["error"] for l in logs5)
try:
    call("POST", f"/api/snapshots/{s5['id']}/commit")
    raise AssertionError("commit of an incomplete snapshot should have failed")
except AssertionError as e:
    assert "409" in str(e), e
print("6. duplicate page rejected, commit blocked")

# 7. missing page
call("POST", "/api/source/scenario", {"scenario": "missing_page"})
s6 = call("POST", "/api/snapshots")
s6 = call("POST", f"/api/snapshots/{s6['id']}/collect")
assert s6["status"] == "collecting" and "missing page" in s6["fail_reason"], s6
logs6 = call("GET", f"/api/snapshots/{s6['id']}")["fetch_logs"]
assert any(not l["ok"] and "missing" in l["error"] for l in logs6)
print("7. missing page rejected")

# 8. arbitrary two-snapshot comparison
cmp = call("POST", "/api/compare", {"left_snapshot_id": baseline_id,
                                    "right_snapshot_id": s3["id"]})
assert counter(cmp["diff"]) == {"confirmed_deleted": 2}, cmp
cmp2 = call("POST", "/api/compare", {"left_snapshot_id": s3["id"],
                                     "right_snapshot_id": s4["id"]})
assert counter(cmp2["diff"]) == {"added": 2, "modified": 1, "reappeared": 1}, cmp2
print("8. arbitrary snapshot comparison works")

# 9. current pointer still snapshot 4; failed rounds never became current
snaps = call("GET", "/api/snapshots")
current = [s for s in snaps if s["is_current"]]
assert len(current) == 1 and current[0]["id"] == s4["id"]
print("9. current snapshot =", s4["id"], "; failed rounds never promoted")

# 10. wrong_total: stream ends cleanly but record count != declared total.
#     The end-of-stream count gate must reject it and keep a failure record.
call("POST", "/api/source/scenario", {"scenario": "wrong_total"})
s7 = call("POST", "/api/snapshots")
s7 = call("POST", f"/api/snapshots/{s7['id']}/collect")
assert s7["status"] == "collecting" and "count mismatch" in s7["fail_reason"], s7
# declared total is inflated by 2; the rejected last page is rolled back so
# fetched_count stays at the 10 rows of the preceding pages
assert s7["fetched_count"] == 10 and s7["total_count"] == 15, s7
logs7 = call("GET", f"/api/snapshots/{s7['id']}")["fetch_logs"]
assert any(not l["ok"] and "count mismatch" in l["error"] for l in logs7), logs7
try:
    call("POST", f"/api/snapshots/{s7['id']}/commit")
    raise AssertionError("count-mismatched snapshot must not be committable")
except AssertionError as e:
    assert "409" in str(e), e
# the failed last page must not have polluted provisional data
assert call("GET", f"/api/snapshots/{s7['id']}")["fetched_count"] == 10
print("10. wrong_total rejected by count gate, failure recorded, commit blocked")

print("\nALL BACKEND CHECKS PASSED")
