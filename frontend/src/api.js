const BASE = "/api";

async function request(method, path, body) {
  const resp = await fetch(BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${resp.status}`;
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return data;
}

export const api = {
  sourceStatus: () => request("GET", "/source/status"),
  setScenario: (scenario) => request("POST", "/source/scenario", { scenario }),
  mutate: (action, count = 1) => request("POST", "/source/mutate", { action, count }),
  resetSource: () => request("POST", "/source/reset"),

  listSnapshots: () => request("GET", "/snapshots"),
  getSnapshot: (id) => request("GET", `/snapshots/${id}`),
  startSnapshot: () => request("POST", "/snapshots"),
  collect: (id) => request("POST", `/snapshots/${id}/collect`),
  fetchPage: (id) => request("POST", `/snapshots/${id}/fetch`),
  commit: (id) => request("POST", `/snapshots/${id}/commit`),
  diff: (id) => request("GET", `/snapshots/${id}/diff`),
  compare: (left, right) =>
    request("POST", "/compare", { left_snapshot_id: left, right_snapshot_id: right }),
  deleteEvents: (key) =>
    request("GET", `/delete-events${key ? `?business_key=${encodeURIComponent(key)}` : ""}`),
  currentRecords: () => request("GET", "/current-records"),
  resetAll: () => request("POST", "/reset-all"),
};
