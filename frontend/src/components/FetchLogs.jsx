import React from "react";

export default function FetchLogs({ logs }) {
  if (!logs || logs.length === 0) {
    return <div className="empty">还没有任何检查记录</div>;
  }
  return (
    <table className="log-table">
      <thead>
        <tr>
          <th>#</th>
          <th>结果</th>
          <th>页序</th>
          <th>返回条数</th>
          <th>错误信息</th>
          <th>时间</th>
        </tr>
      </thead>
      <tbody>
        {logs.map((l) => (
          <tr key={l.id}>
            <td>{l.seq}</td>
            <td>
              {l.ok ? <span className="badge ok">通过</span> : <span className="badge fail">失败</span>}
            </td>
            <td>{l.page_index ?? "—"}</td>
            <td>{l.returned_count ?? "—"}</td>
            <td className="error-text">{l.error || ""}</td>
            <td className="muted nowrap">{l.created_at?.replace("T", " ").slice(0, 19)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
