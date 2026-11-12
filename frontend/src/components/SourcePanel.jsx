import React from "react";

const SCENARIOS = [
  { key: "normal", label: "正常" },
  { key: "duplicate_page", label: "重复页" },
  { key: "missing_page", label: "漏页" },
  { key: "disconnect", label: "中途断开" },
  { key: "wrong_total", label: "总数造假" },
];

const MUTATIONS = [
  { action: "add", label: "新增 2 条", count: 2 },
  { action: "delete", label: "删除 2 条", count: 2 },
  { action: "modify", label: "修改 1 条", count: 1 },
  { action: "readd", label: "恢复已删除键" },
];

export default function SourcePanel({ status, busy, onScenario, onMutate, onReset }) {
  if (!status) return <div className="panel empty">数据源状态加载中…</div>;
  return (
    <div className="panel">
      <h2>本地数据源</h2>
      <dl className="kv">
        <dt>当前场景</dt>
        <dd>{status.scenario}</dd>
        <dt>记录总数</dt>
        <dd>{status.record_count}（每页 {status.page_size} 条）</dd>
        <dt>可恢复键</dt>
        <dd>{status.deleted_pool.length ? status.deleted_pool.join(", ") : "—"}</dd>
      </dl>

      <h3>故障场景（对下一轮快照生效）</h3>
      <div className="scenario-grid">
        {SCENARIOS.map((s) => (
          <button
            key={s.key}
            className={`ghost ${status.scenario === s.key ? "active" : ""}`}
            disabled={busy}
            onClick={() => onScenario(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>

      <h3>数据变化（影响下一轮快照）</h3>
      <div className="btn-row">
        {MUTATIONS.map((m) => (
          <button key={m.action} className="ghost small" disabled={busy}
                  onClick={() => onMutate(m.action, m.count ?? 1)}>
            {m.label}
          </button>
        ))}
      </div>

      <h3>重置</h3>
      <button className="danger small" disabled={busy} onClick={onReset} style={{ width: "100%" }}>
        重置数据源与演示（清空数据库）
      </button>
    </div>
  );
}
