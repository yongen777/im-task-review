# IMTask · 企业 IM 定时任务调度平台

面向企业内部 IM（当前对接天翼量子密信，可替换为企业微信 / 飞书 / 钉钉）的**定时点对点任务派发与提交统计平台**。

管理员在后台定义任务模板（发给谁、几点发、交什么、要不要审核），系统到点单聊推送到人；
员工在 H5 里填报或上传，后台出完成率、逐条审核、导出报表。

> 本仓库是**用于代码 review 的快照**，已剔除 `.env`、数据库、构建产物与真实人员信息。

---

## 目录结构

```
backend/            服务端（FastAPI + SQLite）
  app/
    main.py         双进程入口：公开端(9020/9012) + 管理端(9013)
    api/events.py   IM 事件回调：验签解密 → 身份绑定 → 待归纳池
    api/admin.py    管理后台 API（模板 / 角色成员 / 实例 / 审核 / 日志 / 统计）
    api/h5.py      H5 填报与上传
    scheduler.py    调度器：30s tick，生成实例、发提醒、推逾期
    state.py        数据访问层（业务规则 + SQL 混在一起，见技术债 1）
    dashboard.py    数据看板 PNG 渲染（matplotlib）
    channels/       IM 通道适配（zdxlz）
  tests/            pytest，35 passed / 11 skipped
  docs/             业务口径、接口规格、实施方案

admin-ui/           管理后台前端（React 18 + Vite + Tailwind，SPA）
  src/lib/realApi.ts     前端结构 ↔ 后端接口的映射层（历史 bug 高发区）
  src/components/imtask/ 各页面
  tests/e2e_admin_check.mjs  Playwright 端到端走查
```

---

## 核心链路

1. **事件回调**：`POST /api/app-events`，校验签名 → 解密 → 取 `userId` 与文本
2. **身份绑定**：用户私聊发「姓名 手机号」→ 落 `member` + `delivery_target`（防冒绑：同号被别人占用则拒绝）
3. **待归纳池**：不在名册的手机号/任意消息 → 进 `pending_member`，不拦截
4. **角色分配**：管理员在「已激活待归纳」里把人一键移入业务角色（手机号与姓名只能由用户自己发，管理员不可补录）
5. **模板**：`kind`（notice/form/image/file）与 `period`（day/week/month）**创建后不可修改**
6. **调度**：30s tick。notice 按 `notice_times[]` 逐个时刻发；上传类到 `remind_at` 生成 `task_instance`
7. **投递**：幂等键去重 → 经通道单聊发送 → 写 `reminder_log`
8. **提交与审核**：H5 提交 → `submitted` → 管理员通过/驳回 → 统计与 CSV 导出

---

## 关键设计取舍（review 时建议重点看）

| 设计 | 位置 | 说明 |
|---|---|---|
| 幂等键含计划时刻 | `scheduler.py::make_idem_key` | `sha1(template\|phone\|period_key\|kind\|seq\|planned_at)`，配合 `UNIQUE(idem_key, retry)`。**不含时刻会导致「改时间后不发」**，曾真实踩坑 |
| 补偿窗口 30 分钟 | `scheduler.py::COMPENSATION_WINDOW_SECONDS` | 服务重启后超过窗口记为 `missed`，同一时刻不再重试 |
| kind/period 不可变 | `state.py::_validate_and_normalize_template_unlocked` | 编辑器把 `kind`/`period` 也提交上来就会 400 `KIND_IMMUTABLE` |
| 单一筛选拼装 | `admin.py::_instance_filter_sql` | 列表 / 统计 / 导出共用，避免三处条件漂移 |
| 双真源 | `task_template.schema_json` + `spec_json` | 一个存后端校验用的字段结构，一个存前端完整配置，存在不一致风险 |
| 会话与 CSRF | `admin.py::require_session` | 单账号 + HttpOnly Cookie + CSRF token |

---

## 已知技术债（欢迎直接指出，不必客气）

1. `state.py` 1200+ 行，业务规则、参数校验、SQL 全混在一起，难测难改
2. `schema_json` 与 `spec_json` 双真源，任一路径漏写就会回显不一致
3. 前端 `realApi.ts` 承担「结构翻译」，与后端契约强耦合（历史 bug：映射层默认补 `kind=notice`，导致编辑上传类模板必 400）
4. 单账号鉴权，无 RBAC、无操作级授权细分
5. `task_instance` 只在生成时写 `name_snap` / `dept_snap`，人员调岗后历史数据不跟随（当前按模板 `role_ids` 反查角色）
6. 调度器是进程内 asyncio 循环，**默认单实例**，多副本会重复投递
7. 缺少并发/时序类测试；现有用例偏「行为快照」
8. SPA 产物靠手工 `npm run build` 再拷贝到 `backend/app/admin_static/`，无 CI

---

## 本地运行

### 后端

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # 填入自己的 IM 平台凭据
python -m app.main                                 # 公开端 + 管理端一起起
pytest                                             # 35 passed, 11 skipped
```

### 前端

```bash
cd admin-ui
npm ci
npm run dev        # 需要后端管理端（默认 :9013）在本机
npm run build      # 产物 dist/ → 手工拷到 backend/app/admin_static/
```

---

## 脱敏说明

仓库内所有人名、手机号、IM `userId`、服务器地址均为**示例值**（`张三` / `13900000000` / `3000000000000000001` / `203.0.113.10`）。
真实凭据（`IMT_APP_SECRET`、`IMT_ENCRYPT_KEY`、`IMT_VERIFY_TOKEN`）不在仓库内，仅通过环境变量注入。
