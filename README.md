# 快照采集与对照系统

一轮全量采集没收齐时，**绝不把没采到的数据当成删除**。本系统把采集过程和正式数据
严格分开：失败的采集只留下检查记录，只有完整、通过全部校验的快照才能提交，提交时
在同一事务里切换“当前快照”并记录差异。

## 核心规则

### 采集端校验（全部通过才允许提交）

1. **快照归属**：每页的 `snapshot_token` 必须是本轮开启时拿到的那个；
2. **页码连续**：`page_index` 必须严格递增，漏页（游标仍推进但跳号）会被抓到；
3. **游标推进**：下一页 token 必须变化且不回退，重复页（重复上一页）会被抓到；
4. **业务键唯一**：页内、跨页都不允许重复业务键——不能只看去重后的数量；
5. **正常到末页**：数据源返回 `next_page_token = null`；
6. **总数一致**：采集条数必须等于数据源每轮固定返回的 `total_count`。

任何一项失败：写一条失败检查记录（`fetch_logs`），**游标和已采数据不变**，正式数据
不受影响。排除故障后可对**同一个 snapshot_token** 续采。

### 差异分类（提交时在同一事务物化）

| 类型 | 含义 |
| --- | --- |
| `added` 新增 | 基线没有、本轮有 |
| `modified` 修改 | 业务键相同但 version 变化 |
| `suspected_missing` 疑似缺失 | 采集中基线有、暂未采到——**仅嫌疑，不是删除** |
| `confirmed_deleted` 确认删除 | 完整快照提交后仍缺失，才转正式删除并写删除事件 |
| `reappeared` 重新出现 | 曾被删除的业务键再次出现，历史删除记录保留 |

- 首次成功采集建立**基线**（全部记为新增，无删除）；
- 后续完整快照才与上一轮当前快照比较；
- 删除事件表 `UNIQUE(snapshot_id, business_key)` + `INSERT OR IGNORE`：同一批次重复
  提交不会产生第二条删除事件，提交接口本身也幂等；
- 重新出现的键单独标记，`delete_events` 里的历史删除永久保留。

## 技术栈

- 后端：FastAPI + SQLite（标准库 `sqlite3`，无 ORM）
- 前端：React 18 + Vite，构建产物由 FastAPI 托管（SPA fallback）
- 采集端通过真实 HTTP（loopback）调用数据源，换外部源只需改 `SOURCE_BASE`

## 快速开始

```bash
./run.sh            # 自动建 venv、装依赖、构建前端、起在 http://127.0.0.1:8000
```

手动方式：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install --cache /tmp/npm-cache && npm run build && cd ..
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

前端开发模式（热更新，代理 /api 到 8000）：

```bash
cd frontend && npm run dev
```

## 页面能做什么

- **数据源控制台**：一键切换 正常 / 重复页 / 漏页 / 中途断开 / 总数造假；制造
  新增 / 删除 / 修改 / 恢复已删除键；
- **快照列表**：每轮状态（采集中 / 待提交 / 已提交）、进度、当前标记、失败原因；
- **快照详情**：单页采集 / 连续采集 / 提交；差异表区分五类；采集检查记录表保留每次
  成功与失败；
- **任选两轮成功快照比较**；
- 失败轮次在页面上能看到“疑似缺失”，补齐并提交后变为“确认删除”。

## 推荐演示路径（也可直接跑脚本）

```bash
.venv/bin/python -I backend/demo.py
```

1. 重置环境，正常采集并提交基线（12 条）；
2. 删除 2 条后开启“中途断开”，开启新一轮采集 → 第二页 503，只采到一部分，
   差异里只有“疑似缺失”，正式数据仍是 12 条；
3. 切回正常，对**同一快照**续采到完整，提交 → 疑似缺失变“确认删除”，生成删除事件；
4. 恢复一个被删键再采集 → 标记“重新出现”，删除历史仍在；
5. 分别用“重复页”“漏页”“总数造假”场景开新轮 → 均被拦截、无法提交、留下失败
   检查记录（总数造假专门验证末页数量闸门）；
6. 任选两轮已提交快照做对比。

## 测试

```bash
# 后端端到端（需要服务器在跑；脚本开头会自动重置环境）
.venv/bin/python -I backend/verify_e2e.py

# 前端 DOM 冒烟（jsdom + mock fetch，无需浏览器）
cd frontend && node --import ./scripts/register.js smoke/ui.jsx
```

## 目录结构

```
backend/
  app/
    db.py          # SQLite 连接、建表、wipe_db
    datasource.py  # 可切换故障的本地数据源（page_token 翻页 + 固定 snapshot_token）
    collector.py   # 采集校验、续采、差异物化、事务提交、快照比较
    main.py        # FastAPI 路由 + 静态托管
  verify_e2e.py    # 后端九条端到端断言
  demo.py          # 完整故事线演示
frontend/
  src/             # React 页面与组件
  smoke/ui.jsx     # DOM 冒烟测试
run.sh             # 一键启动
```

## 关键 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/source/scenario` | 切换故障场景 |
| POST | `/api/source/mutate` | 新增/删除/修改/恢复数据 |
| POST | `/api/source/snapshot` | 数据源侧开启一轮（冻结数据、发 token） |
| GET | `/api/source/pages?page_token=` | 翻页（含 total_count） |
| POST | `/api/snapshots` | 采集端开启一轮 |
| POST | `/api/snapshots/{id}/fetch` | 采一页并校验 |
| POST | `/api/snapshots/{id}/collect` | 连续采到完成或失败 |
| POST | `/api/snapshots/{id}/commit` | 事务提交并切换当前快照（幂等） |
| GET | `/api/snapshots/{id}/diff` | 已提交看正式差异，采集中看临时差异 |
| POST | `/api/compare` | 任意两轮快照比较 |
| GET | `/api/delete-events` | 删除历史（可按业务键过滤） |
| POST | `/api/reset-all` | 清空数据库并重置数据源 |
