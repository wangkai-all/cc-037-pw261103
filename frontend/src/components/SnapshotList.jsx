import React from "react";

const STATUS_LABEL = {
  collecting: "采集中",
  complete: "已采完待提交",
  committed: "已提交",
};

export default function SnapshotList({ snapshots, selectedId, onSelect }) {
  return (
    <div className="panel">
      <h2>快照轮次（{snapshots.length}）</h2>
      {snapshots.length === 0 && <div className="empty">还没有快照，点击“开始新一轮采集”</div>}
      <div className="snapshot-list">
        {snapshots.map((s) => (
          <div
            key={s.id}
            className={`snapshot-card ${selectedId === s.id ? "selected" : ""}`}
            onClick={() => onSelect(s.id)}
          >
            <div className="top">
              <strong>
                #{s.id}
                {s.is_current ? <span className="badge current">当前</span> : null}
              </strong>
              <span className={`badge ${s.status}`}>{STATUS_LABEL[s.status] || s.status}</span>
            </div>
            <div className="token">{s.snapshot_token}</div>
            <div className="meta">
              <span>
                {s.fetched_count}/{s.total_count ?? "?"} 条
              </span>
              <span>差异 {s.diff_count}</span>
              <span>{s.created_at?.replace("T", " ").slice(0, 19)}</span>
            </div>
            {s.fail_reason && s.status === "collecting" && (
              <div className="reason">⚠ {s.fail_reason}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
