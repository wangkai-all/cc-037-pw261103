"""Guided demo of the full story from the requirements.

Run against a fresh server:
  POST /api/reset-all first (this script does it itself), then:
  python -I backend/demo.py
"""
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
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:200]}")


def show(title, obj):
    print(f"\n--- {title} ---")
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:1200])


def counts(diff):
    c = {}
    for d in diff:
        c[d["diff_type"]] = c.get(d["diff_type"], 0) + 1
    return c


print("0. clean slate")
call("POST", "/api/reset-all")

print("\n1. baseline round (normal source)")
s = call("POST", "/api/snapshots")
s = call("POST", f"/api/snapshots/{s['id']}/collect")
s = call("POST", f"/api/snapshots/{s['id']}/commit")
print(f"   snapshot #{s['id']} committed as current, {s['fetched_count']} records")

print("\n2. remove 2 keys, then enable mid-stream disconnect")
print("  ", call("POST", "/api/source/mutate", {"action": "delete", "count": 2}))
call("POST", "/api/source/scenario", {"scenario": "disconnect"})
s2 = call("POST", "/api/snapshots")
s2 = call("POST", f"/api/snapshots/{s2['id']}/collect")
print(f"   round #{s2['id']} status={s2['status']} fetched={s2['fetched_count']}/{s2['total_count']}")
print("   failure:", s2["fail_reason"])
show("provisional diff (uncollected keys are ONLY suspected_missing)",
     counts(call("GET", f"/api/snapshots/{s2['id']}/diff")["diff"]))
print("   official current records still:", len(call("GET", "/api/current-records")))

print("\n3. clear the fault and RESUME THE SAME SNAPSHOT")
call("POST", "/api/source/scenario", {"scenario": "normal"})
s2 = call("POST", f"/api/snapshots/{s2['id']}/collect")
print(f"   round #{s2['id']} status={s2['status']} fetched={s2['fetched_count']}/{s2['total_count']}")
s2 = call("POST", f"/api/snapshots/{s2['id']}/commit")
show("committed diff: suspected_missing has now become confirmed_deleted",
     counts(call("GET", f"/api/snapshots/{s2['id']}/diff")["diff"]))
print("   delete events:", [(e["business_key"], e["snapshot_id"])
                            for e in call("GET", "/api/delete-events")])

print("\n4. re-add one deleted key -> reappeared, delete history retained")
print("  ", call("POST", "/api/source/mutate", {"action": "readd", "count": 1}))
s3 = call("POST", "/api/snapshots")
s3 = call("POST", f"/api/snapshots/{s3['id']}/collect")
s3 = call("POST", f"/api/snapshots/{s3['id']}/commit")
show("round 3 diff", counts(call("GET", f"/api/snapshots/{s3['id']}/diff")["diff"]))
key = call("GET", "/api/source/status")["deleted_pool"]
print("   delete history still present:",
      call("GET", "/api/delete-events"))

print("\n5. duplicate-page and missing-page rounds are rejected with check records")
for scenario in ("duplicate_page", "missing_page"):
    call("POST", "/api/source/scenario", {"scenario": scenario})
    sx = call("POST", "/api/snapshots")
    sx = call("POST", f"/api/snapshots/{sx['id']}/collect")
    print(f"   {scenario}: status={sx['status']} reason={sx['fail_reason']}")
call("POST", "/api/source/scenario", {"scenario": "normal"})

print("\n6. compare any two successful snapshots (1 -> 3)")
show("ad-hoc comparison", counts(call("POST", "/api/compare",
                                      {"left_snapshot_id": 1,
                                       "right_snapshot_id": s3["id"]})["diff"]))
print("\nDEMO COMPLETE")
