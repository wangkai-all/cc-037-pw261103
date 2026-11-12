import React, { useMemo, useState } from "react";
import { api } from "../api";
import DiffTable, { DiffSummary } from "./DiffTable";

export default function ComparePanel({ snapshots, onError }) {
  const committed = useMemo(
    () => snapshots.filter((s) => s.status === "committed"),
    [snapshots]
  );
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  React.useEffect(() => {
    if (committed.length >= 2) {
      setLeft((v) => v || String(committed[0].id));
      setRight((v) => v || String(committed[committed.length - 1].id));
    }
  }, [committed]);

  async function doCompare() {
    if (!left || !right || left === right) return;
    setLoading(true);
    try {
      setResult(await api.compare(Number(left), Number(right)));
    } catch (e) {
      onError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <h2>任选两轮成功快照比较</h2>
      {committed.length < 2 ? (
        <div className="empty">至少需要两轮已提交快照</div>
      ) : (
        <>
          <div className="compare-bar">
            <select value={left} onChange={(e) => setLeft(e.target.value)}>
              {committed.map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} {s.snapshot_token}
                </option>
              ))}
            </select>
            <span className="muted">对照</span>
            <select value={right} onChange={(e) => setRight(e.target.value)}>
              {committed.map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} {s.snapshot_token}
                </option>
              ))}
            </select>
            <button disabled={loading || !left || !right || left === right} onClick={doCompare}>
              比较
            </button>
          </div>
          {result && (
            <>
              <div className="muted" style={{ marginBottom: 10 }}>
                <span className="mono">{result.left_token}</span> →{" "}
                <span className="mono">{result.right_token}</span>
              </div>
              <DiffSummary diff={result.diff} />
              <DiffTable diff={result.diff} />
            </>
          )}
        </>
      )}
    </div>
  );
}
