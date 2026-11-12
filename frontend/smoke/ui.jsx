/* DOM smoke test with jsdom + mocked fetch. Run: node --import ./scripts/register.jsx smoke/ui.jsx
   No bundler needed: register a tiny JSX -> React.createElement transform. */
import { JSDOM } from "jsdom";
import React from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App.jsx";

const { act } = React;

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.getComputedStyle = dom.window.getComputedStyle;
window.confirm = () => true;

// --- mock backend -----------------------------------------------------------
const state = {
  scenario: "normal",
  snapshots: [
    {
      id: 1, snapshot_token: "snap-aaa", status: "committed", is_current: 1,
      fetched_count: 12, total_count: 12, diff_count: 12, page_size: 5,
      next_cursor: null, fail_reason: null, created_at: "2026-11-13T10:00:00+00:00",
      committed_at: "2026-11-13T10:01:00+00:00",
    },
    {
      id: 2, snapshot_token: "snap-bbb", status: "collecting", is_current: 0,
      fetched_count: 10, total_count: 12, diff_count: 0, page_size: 5,
      next_cursor: "tok", fail_reason: "upstream 503: simulated upstream disconnect mid-stream",
      created_at: "2026-11-13T11:00:00+00:00", committed_at: null,
    },
  ],
  nextId: 3,
};

const detailOf = (id) => {
  const base = state.snapshots.find((s) => s.id === id);
  if (!base) {
    return { id, snapshot_token: "snap-new", status: "collecting", is_current: 0,
             fetched_count: 0, total_count: 12, page_size: 5, next_cursor: "t",
             fail_reason: null, created_at: "x", committed_at: null, fetch_logs: [] };
  }
  return {
    ...base,
    fetch_logs: [
      { id: 1, seq: 0, ok: 1, page_index: 0, returned_count: 5, error: null, created_at: "x" },
      { id: 2, seq: 1, ok: 1, page_index: 1, returned_count: 5, error: null, created_at: "x" },
      { id: 3, seq: 2, ok: 0, page_index: null, returned_count: null,
        error: "upstream 503: simulated upstream disconnect mid-stream", created_at: "x" },
    ],
  };
};

const diffOf = (id) =>
  id === 1
    ? { snapshot_id: 1, provisional: false, baseline_id: null,
        diff: Array.from({ length: 12 }, (_, i) => ({
          business_key: `K${i + 1}`, diff_type: "added", old_version: null, new_version: "v1",
        })) }
    : { snapshot_id: id, provisional: true, baseline_id: 1,
        diff: id >= 3
          ? []
          : [{ business_key: "K0011", diff_type: "suspected_missing", old_version: "v1", new_version: null },
             { business_key: "K0012", diff_type: "suspected_missing", old_version: "v1", new_version: null }] };

globalThis.fetch = async (url, opts = {}) => {
  const path = url.replace("/api", "");
  const body = opts.body ? JSON.parse(opts.body) : null;
  let payload;
  if (path === "/source/status") {
    payload = { scenario: state.scenario, record_count: 12, page_size: 5, deleted_pool: [], open_rounds: [] };
  } else if (path === "/snapshots" && opts.method === "POST") {
    const s = { id: state.nextId++, snapshot_token: "snap-new", status: "collecting", is_current: 0,
                fetched_count: 0, total_count: 12, diff_count: 0, page_size: 5, next_cursor: "t",
                fail_reason: null, created_at: "x", committed_at: null };
    state.snapshots.push(s);
    payload = s;
  } else if (path === "/snapshots") {
    payload = state.snapshots;
  } else if (/^\/snapshots\/\d+\/diff$/.test(path)) {
    payload = diffOf(Number(path.split("/")[2]));
  } else if (/^\/snapshots\/\d+$/.test(path)) {
    payload = detailOf(Number(path.split("/").pop()));
  } else if (path === "/reset-all") {
    state.snapshots = [];
    payload = { ok: true };
  } else {
    payload = {};
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
};

// --- render -----------------------------------------------------------------
const container = document.getElementById("root");
await act(async () => {
  createRoot(container).render(React.createElement(App));
  await new Promise((r) => setTimeout(r, 0));
});

const text = container.textContent;
const assert = (cond, msg) => { if (!cond) { console.error("FAIL:", msg); process.exit(1); } };

assert(text.includes("快照采集对照台"), "title rendered");
assert(text.includes("snap-aaa") && text.includes("snap-bbb"), "both snapshots listed");
assert(text.includes("已提交") && text.includes("采集中"), "status badges rendered");
assert(text.includes("当前"), "current badge rendered");
assert(text.includes("疑似缺失"), "provisional diff shows suspected_missing");
assert(container.querySelectorAll(".badge.diff-confirmed_deleted").length === 0,
       "no confirmed-deletion badge while round is incomplete");
const suspectedRows = [...container.querySelectorAll("table tbody tr")].filter(
  (tr) => tr.querySelector(".badge.diff-suspected_missing"));
assert(suspectedRows.length === 2, "two suspected-missing rows shown in the diff table");
assert(text.includes("simulated upstream disconnect"), "failure reason visible");
assert(text.includes("开始新一轮采集"), "start button present");

// switch to the committed snapshot: should show 12 新增
const cards = container.querySelectorAll(".snapshot-card");
await act(async () => {
  cards[0].click();
  await new Promise((r) => setTimeout(r, 0));
});
assert(container.textContent.includes("新增"), "baseline diff labels rendered");

// logs tab
await act(async () => {
  [...container.querySelectorAll(".tabs button")].find((b) => b.textContent.includes("检查记录")).click();
});
assert(container.textContent.includes("失败") && container.textContent.includes("通过"),
       "fetch check records rendered with pass/fail badges");

// start a new round
const before = state.snapshots.length;
await act(async () => {
  [...container.querySelectorAll("button")].find((b) => b.textContent.includes("开始新一轮采集")).click();
  await new Promise((r) => setTimeout(r, 0));
});
assert(state.snapshots.length === before + 1, "start round creates a snapshot");

console.log("UI SMOKE TEST PASSED");
