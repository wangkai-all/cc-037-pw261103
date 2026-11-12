import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import SourcePanel from "./components/SourcePanel";
import SnapshotList from "./components/SnapshotList";
import SnapshotDetail from "./components/SnapshotDetail";
import ComparePanel from "./components/ComparePanel";

export default function App() {
  const [sourceStatus, setSourceStatus] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);

  const showError = useCallback((msg) => {
    setToast({ kind: "error", text: msg });
    window.setTimeout(() => setToast(null), 5000);
  }, []);

  const showOk = useCallback((msg) => {
    setToast({ kind: "ok", text: msg });
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const refresh = useCallback(async () => {
    const [st, snaps] = await Promise.all([api.sourceStatus(), api.listSnapshots()]);
    setSourceStatus(st);
    setSnapshots(snaps);
    setSelectedId((cur) => cur ?? (snaps.length ? snaps[snaps.length - 1].id : null));
  }, []);

  useEffect(() => {
    refresh().catch((e) => showError(e.message));
  }, [refresh, showError]);

  async function startRound() {
    setBusy(true);
    try {
      const s = await api.startSnapshot();
      await refresh();
      setSelectedId(s.id);
      showOk(`已开启快照 ${s.snapshot_token}`);
    } catch (e) {
      showError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleScenario(key) {
    setBusy(true);
    try {
      await api.setScenario(key);
      await refresh();
      showOk(`数据源场景切换为：${key}`);
    } catch (e) {
      showError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleMutate(action, count) {
    setBusy(true);
    try {
      const r = await api.mutate(action, count);
      await refresh();
      showOk(`数据变化已生效：${JSON.stringify(r)}`);
    } catch (e) {
      showError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!window.confirm("将清空所有快照、差异和删除历史，并重置数据源，确定？")) return;
    setBusy(true);
    try {
      await api.resetAll();
      await refresh();
      setSelectedId(null);
      showOk("演示环境已重置");
    } catch (e) {
      showError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>快照采集对照台</h1>
          <div className="sub">
            未采全的数据不算删除 · 完整快照才参与对照 · 提交在同一事务切换当前快照
          </div>
        </div>
        <button onClick={startRound} disabled={busy}>
          开始新一轮采集
        </button>
      </header>

      <div className="layout">
        <div>
          <SourcePanel
            status={sourceStatus}
            busy={busy}
            onScenario={handleScenario}
            onMutate={handleMutate}
            onReset={handleReset}
          />
          <SnapshotList
            snapshots={snapshots}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>

        <div>
          {selectedId ? (
            <SnapshotDetail
              key={selectedId}
              snapshotId={selectedId}
              onChanged={refresh}
              onError={showError}
              busy={busy}
              setBusy={setBusy}
            />
          ) : (
            <div className="panel empty">选择或开启一轮快照开始采集</div>
          )}
          <ComparePanel snapshots={snapshots} onError={showError} />
        </div>
      </div>

      {toast && (
        <div className={`toast ${toast.kind === "ok" ? "ok" : ""}`}>{toast.text}</div>
      )}
    </div>
  );
}
