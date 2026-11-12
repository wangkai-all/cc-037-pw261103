import React, { useEffect, useState } from "react";
import { api } from "../api";
import DiffTable, { DiffSummary, TYPE_LABEL } from "./DiffTable";
import FetchLogs from "./FetchLogs";

const STATUS_LABEL = {
  collecting: "采集中",
  complete: "已采完待提交",
  committed: "已提交",
};

export default function SnapshotDetail({ snapshotId, onChanged, onError, busy, setBusy }) {
  const [snap, setSnap] = useState(null);
  const [diff, setDiff] = useState(null);
  const [tab, setTab] = useState("diff");

  async function refresh() {
    const [s, d] = await Promise.all([
      api.getSnapshot(snapshotId),
      api.diff(snapshotId),
    ]);
    setSnap(s);
    setDiff(d);
  }

  useEffect(() => {
    setTab("diff");
    refresh().catch(onError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotId]);

  if (!snap || !diff) return <div className="panel empty">加载中…</div>;

  const pct = snap.total_count ? Math.round((snap.fetched_count / snap.total_count) * 100) : 0;

  async function run(fn) {
    setBusy(true);
    try {
      await fn();
      await refresh();
      await onChanged();
    } catch (e) {
      onError(e.message);
      await refresh().catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="detail-head">
        <h2 style={{ margin: 0 }}>
          快照 #{snap.id} <span className="mono muted">{snap.snapshot_token}</span>
        </h2>
        <span className={`badge ${snap.status}`}>{STATUS_LABEL[snap.status]}</span>
        {snap.is_current ? <span className="badge current">当前正式快照</span> : null}
      </div>

      <dl className="kv">
        <dt>进度</dt>
        <dd>
          {snap.fetched_count} / {snap.total_count} 条（{pct}%）
        </dd>
        <dt>页大小</dt>
        <dd>{snap.page_size}</dd>
        <dt>下一游标</dt>
        <dd>{snap.next_cursor ? snap.next_cursor.slice(0, 28) + "…" : "已到末页"}</dd>
        <dt>提交时间</dt>
        <dd>{snap.committed_at?.replace("T", " ").slice(0, 19) || "—"}</dd>
      </dl>
      <div className="progress">
        <div style={{ width: `${pct}%` }} />
      </div>

      {snap.fail_reason && (
        <div className="notice warn">
          最近一次检查失败：{snap.fail_reason}
          <br />
          失败只留下检查记录，游标与已采数据不变；排除故障后可继续采集同一快照。
        </div>
      )}
      {snap.status === "complete" && (
        <div className="notice ok">
          已正常到达最后一页，业务键唯一，记录数与总数一致——可以提交。
        </div>
      )}
      {diff.provisional && diff.baseline_id && (
        <div className="notice warn">
          以下为对照当前正式快照的<b>临时</b>差异：未采到的键仅标记为“疑似缺失”，
          完整采完并提交后才会转为“确认删除”。
        </div>
      )}
      {diff.provisional && !diff.baseline_id && (
        <div className="notice info">尚无基线快照，首次成功采集将建立基线。</div>
      )}

      <div className="btn-row" style={{ marginBottom: 14 }}>
        <button disabled={busy || snap.status !== "collecting"}
                onClick={() => run(() => api.fetchPage(snap.id))}>
          采集一页
        </button>
        <button disabled={busy || snap.status !== "collecting"}
                onClick={() => run(() => api.collect(snap.id))}>
          连续采集
        </button>
        <button disabled={busy || snap.status !== "complete"}
                onClick={() => run(() => api.commit(snap.id))}>
          提交并切换当前快照
        </button>
        <button className="ghost" disabled={busy} onClick={() => run(async () => {})}>
          刷新
        </button>
      </div>

      <div className="tabs">
        <button className={tab === "diff" ? "active" : ""} onClick={() => setTab("diff")}>
          差异对照（{diff.diff.length}）
        </button>
        <button className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>
          采集检查记录（{snap.fetch_logs.length}）
        </button>
      </div>

      {tab === "diff" ? (
        <>
          <DiffSummary diff={diff.diff} />
          {diff.diff.some((d) => d.diff_type === "suspected_missing") && (
            <div className="notice info">
              {diff.diff.filter((d) => d.diff_type === "suspected_missing").length} 个键处于
              “{TYPE_LABEL.suspected_missing}”：本轮没采到，但采集尚未完成，不能当作删除。
            </div>
          )}
          <DiffTable diff={diff.diff} />
        </>
      ) : (
        <FetchLogs logs={snap.fetch_logs} />
      )}
    </div>
  );
}
