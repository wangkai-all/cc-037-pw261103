import React from "react";

const TYPE_LABEL = {
  added: "新增",
  modified: "修改",
  suspected_missing: "疑似缺失",
  confirmed_deleted: "确认删除",
  reappeared: "重新出现",
};

export function DiffSummary({ diff }) {
  const counts = {};
  for (const d of diff) counts[d.diff_type] = (counts[d.diff_type] || 0) + 1;
  if (Object.keys(counts).length === 0) {
    return <div className="chip">无差异（两轮数据一致）</div>;
  }
  return (
    <div className="summary-chips">
      {Object.entries(counts).map(([type, n]) => (
        <span key={type} className="chip">
          <span className={`badge diff-${type}`}>{TYPE_LABEL[type] || type}</span>
          <b>{n}</b>
        </span>
      ))}
    </div>
  );
}

export default function DiffTable({ diff }) {
  if (!diff || diff.length === 0) {
    return <div className="empty">无差异记录</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th className="mono">业务键</th>
          <th>类型</th>
          <th>旧版本</th>
          <th>新版本</th>
        </tr>
      </thead>
      <tbody>
        {diff.map((d) => (
          <tr key={d.business_key}>
            <td className="mono">{d.business_key}</td>
            <td>
              <span className={`badge diff-${d.diff_type}`}>
                {TYPE_LABEL[d.diff_type] || d.diff_type}
              </span>
            </td>
            <td className="mono">{d.old_version ?? "—"}</td>
            <td className="mono">{d.new_version ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export { TYPE_LABEL };
