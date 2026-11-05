# IMTask · 企业 IM 定时任务调度平台

面向企业内部 IM（当前对接**天翼量子密信**与**企业微信智能机器人**，可扩展飞书 / 钉钉）的**定时点对点任务派发与填报统计平台**。

管理员在后台定义任务模板（发给谁、几点发、交什么、要不要审核），系统到点单聊推送到人；
员工在 H5 里填报或上传，后台出完成率、逐条审核、导出报表。

---

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                     企业 IM 平台                      │
│          （量子密信 / 企业微信智能机器人）                │
└──────────┬──────────────────────┬───────────────────┘
           │ 回调事件（验签解密）    │ 消息推送（单聊）
           ▼                      │
┌─────────────────────────────────────────────────────┐
│                   FastAPI 服务端                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ 公开端    │  │ 管理端    │  │ 调度器（30s tick） │   │
│  │ :9020    │  │ :9013    │  │ 实例生成·投递·催办 │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ IM 回调  │  │ H5 提交  │  │ 数据看板（PIL）    │   │
│  │ events   │  │ h5       │  │ dashboard         │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                        │                             │
│                   SQLite（WAL）                       │
└─────────────────────────────────────────────────────┘
```

---

## 核心业务流程

### 激活模型

```
管理员后台                     员工侧
────────────                 ─────────────────────────
① CSV 导入/手动添加成员
   （姓名 + 手机号 + 部门）
   ├→ 部门不存在？自动建档
   └→ 手机号在其他管理员处
      已激活？直接复用 ✅
                              ② 给机器人发任意文本
                              ③ 收到登记链接（H5）
                              ④ 填写姓名/手机号/部门
                                 与后台名册比对：
                                 ├ 一致 → 绑定成功
                                 ├ 不在名册 → 拒绝
                                 └ 姓名不符 → 拒绝
```

> **关键**：激活绑定（`delivery_target`）是**全平台全局共享**的 `(platform, phone)` 唯一记录。
> 任何管理员把已激活的成员导入自己分组，立刻显示已激活、到点即发，**无需二次登记**。

### 管理员分级

| 身份 | 权限 |
|---|---|
| 超级管理员 (`admin`) | 全局只读（任务/角色/成员/审核/看板/审计，支持按管理员筛选与创建人展示）+ 账号管理 + 通道配置 + 部门改名删除；**不可创建业务数据** |
| 分级管理员 (`sub_admin`) | 干净工作台，只管理自己名下（`owner_id`）的角色/成员/模板/实例；可分配平台权限（量子密信/企业微信，单个或多个） |

- 初始密码 `Hds22999!`（兼容全角 `！`），首登强制改密（未改密前业务接口一律 403）
- 停用即：登录拒绝 + 会话失效 + 名下模板停止生成与发送
- 删除需名下无角色和模板（有数据只能停用）
- 分级管理员可建同平台同名角色（唯一键 `UNIQUE(owner_id, platform, name)`）

### 任务调度

```
30s tick → 遍历启用模板 → 按「执行日」（选中的星期几）判断是否今天执行
→ 生成/复用当天实例（UNIQUE(template_id, phone, period_key)）
→ 到点单聊推送（按任务所属平台路由）→ 幂等去重 → 写 reminder_log
→ 上传任务：H5 链接提交 → 管理员审核（谁创建谁审核）
→ 逾期：申请补交 → 管理员审批 → 30 分钟补交窗口
```

### 多平台投递

| 平台 | 发送形态 | 特点 |
|---|---|---|
| 量子密信 (`zdxlz`) | 文本 + H5 链接 | 附图走自建 H5 图文页（手机端原生图片消息转圈打不开，实测口径） |
| 企业微信 (`wecom`) | 文本 + 原生图片 | 走长连接三步上传协议；附图直接发原生图片消息 |

- 任务归属平台 = 模板所选角色的平台（角色按平台隔离，模板禁止混选）
- 同一成员在两个平台都有绑定时，任务只往其归属平台发，不串台
- 附图支持管理员命名（如"bug修复"），H5 页标题随名称展示

---

## 目录结构

```
backend/                          服务端（Python 3.11+ / FastAPI / SQLite WAL）
  app/
    main.py                       双进程入口：公开端 + 管理端
    config.py                     环境变量配置
    db.py                         DDL + 幂等迁移（ALTER TABLE / 表重建）
    state.py                      数据访问层（业务规则 + SQL）
    scheduler.py                   调度器：30s tick、幂等投递、多平台路由
    dashboard.py                  数据看板 PNG 渲染（Pillow）
    channel_config.py              通道凭证管理（数据库加密存储 + 掩码返回）
    channels/
      zdxlz.py                    量子密信通道（验签解密 + 单聊发送）
      wecom_aibot.py              企业微信智能机器人（长连接 + 原生图片）
      wecom.py                    企业微信自建应用（兼容旧路径）
    api/
      events.py                   IM 回调入口：验签 → 激活登记 → 机器人回复
      admin.py                    管理后台全部接口（模板/角色/成员/审核/账号/通道/部门/审计…）
      h5.py                       H5 任务提交与身份登记
      wecom_callback.py           企微回调（自建应用，兼容旧路径）
    h5/
      bind.html                   身份登记页（姓名 + 手机号 + 部门，与名册比对）
      task.html                   任务提交页（拍照/上传/报数/文字说明 + 提交后锁定）
    admin_static/                 前端构建产物（vite build 输出）
  scripts/
    deploy_remote.py              部署脚本（打包 → 备份 → 上传 → 重启 → 验收 → 远程 pytest）
    create_admin.py               初始化管理员账号
    migrate_owner_to_sub_admin.py 存量数据归档迁移（划归分级管理员）
  tests/                          pytest，269 passed / 11 skipped
  docs/                           历史业务口径与接口规格

admin-ui/                         管理后台前端（React 19 + Vite + TypeScript + Tailwind CSS 4）
  src/
    App.tsx                       主布局 + 侧栏导航 + 超管全局查看条
    main.tsx                      SPA 路由（/login / /change-password / /）
    pages/
      LoginPage.tsx               独立登录页
      ChangePasswordPage.tsx      强制改密页（首登）
    components/
      system/AdminUserCard.tsx    身份徽标 + 平台切换器 + 改密弹窗
      imtask/
        AdminAccountsView.tsx     分级管理员账号管理（开设/授权/停用/重置/删除）
        AdminScopeHeader.tsx      超管视角切换条（超级管理员 + 各分级管理员）
        InstancesView.tsx         任务实例与审核
        TemplatesView.tsx         任务模板管理（多选角色 + 执行日 + 附图配置 + 实时预览）
        RolesMembersView.tsx      角色与成员名册（CSV 导入自动建部门 + 全局搜索已激活成员）
        PendingReviewPanel.tsx    跨日期审核待办聚合
        ChannelSettingsView.tsx   通道凭证配置向导
    lib/
      realApi.ts                  前端结构 ↔ 后端接口映射层
      api.ts                      薄封装（认证 + CSRF + 错误翻译）
      store.tsx                   全局状态（登录身份 / 平台切换 / 管理员筛选）
      types.ts                    TypeScript 类型定义
      toast.tsx                   Toast 通知组件 + 错误码翻译
  tests/
    e2e_admin_check.mjs           Playwright 端到端走查
```

---

## 数据模型

```
admin_user       ─ id, username, pwd_hash, must_change, role_level, display_name,
                   allowed_platforms, enabled, last_login_at, created_at

role             ─ role_id, name, note, platform, owner_id, created_at
                   UNIQUE(owner_id, platform, name)

member           ─ id, role_id, phone, name, dept, enabled, source, created_at
                   UNIQUE(role_id, phone)
                   （归属由 role_id → role.owner_id 推导，不加冗余列）

delivery_target  ─ platform, phone, external_id, usable, activated_at, last_event_at
                   PRIMARY KEY (platform, phone)  ← 全局共享，跨管理员复用

dept_option      ─ id, name, created_at（全局共享，H5 登记页共用）
                   UNIQUE(name)

task_template    ─ template_id, name, kind(notice/form), role_ids(JSON),
                   text_tpl, notice_times(JSON), period, exec_weekdays(JSON),
                   deadline, need_review, attach_image, attach_image_path,
                   attach_image_name, spec_json, enabled, deleted, owner_id

task_instance    ─ id, template_id, phone, name_snap, dept_snap, period_key,
                   status(pending/sent/submitted/approved/rejected/overdue/expired),
                   deadline_at, reject_reason, resubmit_status, owner_id
                   UNIQUE(template_id, phone, period_key)

reminder_log     ─ id, template_id, phone, kind, planned_at, sent_at,
                   result(sent/failed/skipped/missed), idem_key, retry
                   UNIQUE(idem_key, retry)   ← 幂等键含计划时刻

data_snapshot    ─ source_key, period_key, values_json, updated_by, owner_id
                   UNIQUE(source_key, period_key)
```

---

## 关键设计取舍

| 设计 | 位置 | 说明 |
|---|---|---|
| 幂等键含计划时刻 | `scheduler.py::make_idem_key` | `sha1(template\|phone\|period_key\|kind\|seq\|planned_at)`，配合 `UNIQUE(idem_key, retry)`。**不含时刻会导致「改时间后不发」**，曾真实踩坑 |
| 发送前查重 | `scheduler.py::_deliver_with_log` | **发送动作之前**查 `has_sent_reminder`，不是发送后靠唯一约束拦——约束拦得住记录拦不住已发出的消息 |
| 按任务平台路由 | `scheduler.py::_targets_for` | 任务属于哪个平台就只往那个平台发；`platform=None` 时 fail-open（全通道）。密信任务不串到企微，反之亦然 |
| 单一筛选拼装 | `admin.py::_instance_filter_sql` | 列表 / 统计 / 导出 / 待审共用一套 WHERE，避免四处条件漂移 |
| 双真源 | `schema_json` + `spec_json` | 前端完整配置存 `spec_json`（回显用），后端校验用 `schema_json`。存在不一致风险，编辑时前端保证同步 |
| 角色归属推导 | `member.role_id → role.owner_id` | 成员表不加冗余 owner 列，归属跟着角色走；一人多角色时天然隔离 |
| CSRF 双通道 | `admin.py::require_session` | HttpOnly Cookie（sid）+ Header（X-CSRF-Token） |
| 迁移幂等 | `db.py` | 全部 `_ensure_column` / 显式 `BEGIN IMMEDIATE` 重建表，重启不报错不丢数据 |

---

## 已知技术债

1. `state.py` 3400+ 行，业务规则、参数校验、SQL 全混在一起，难测难改
2. `schema_json` 与 `spec_json` 双真源，任一路径漏写就会回显不一致
3. 前端 `realApi.ts` 承担「结构翻译」，与后端契约强耦合
4. 调度器是进程内 asyncio 循环，**单实例部署**，多副本会重复投递（多副本需求 → 迁移 MySQL + 分布式锁）
5. SPA 产物靠手工 `npm run build` 再拷贝到 `backend/app/admin_static/`，无 CI
6. 审计日志按 `username` 字符串过滤（非外键），删除账号后审计仍留名
7. `admin.py` 3500+ 行，路由 + 守卫 + 权限 + 业务逻辑混合

---

## 本地运行

### 后端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # 填入 IM 平台凭据
python -m app.main                                   # 公开端 + 管理端一起起
pytest                                               # 269 passed, 11 skipped
```

### 前端

```bash
cd admin-ui
npm ci
npm run dev          # 开发服务器，需要后端管理端（默认 :9013）在本机
npm run build        # 产物 dist/ → 手工拷到 backend/app/admin_static/
```

### 初始管理员

```bash
cd backend
python scripts/create_admin.py --username admin
# 或设置环境变量 IMT_ADMIN_USER / IMT_ADMIN_PASSWORD
```

---

## 测试

```bash
cd backend
pytest                        # 269 passed / 11 skipped
```

### 测试覆盖面

| 文件 | 覆盖 |
|---|---|
| `test_activation_model` | 后台先导入才能激活；比对任一在册行；移除=彻底删绑定；跨平台隔离 |
| `test_multi_admin` | 超管只读 403、数据隔离、平台授权、免二次激活、停用踢会话、删除保护 |
| `test_admin_scope_guard` | 模板角色归属、平台授权收回、pwd_hash 不外泄、跨管理员免激活 |
| `test_multi_channel` | 任务按平台单发、multi-tick 幂等、附图走链接/原生图 |
| `test_import_dept_autocreate` | CSV 导入部门自动建档（同名去重、超长拒行、空允许、dry_run） |
| `test_roster_match_any_row` | 多角色异名任一匹配通过、全不符拒绝、取最新行部门 |
| `test_reply_channel_and_recycle` | 回调回复按来源平台、密信附图走链接、移出分组清未完成实例 |
| `test_wecom_aibot_image` | 企微原生图片三步上传协议、media_id 缓存 |
| `test_submit_feedback_dedupe` | H5 提交反馈回推幂等（发送前查重） |
| 其余 30+ 文件 | 各历史阶段回归：调度、审核、补交、CSV 导入、XSS、通道配置、H5 页面… |

---

## 环境变量

```bash
# 必填
IMT_APP_ID=                 # 量子密信 AppID
IMT_APP_SECRET=             # 量子密信 AppSecret
IMT_VERIFY_TOKEN=           # 回调 Token
IMT_ENCRYPT_KEY=            # 回调 EncryptKey
IMT_PUBLIC_BASE_URL=        # 公网回调地址（如 http://your-server:9020）

# 企业微信智能机器人（可选）
IMT_WECOM_BOT_ID=           # Bot ID（aib 开头）
IMT_WECOM_BOT_SECRET=       # Bot Secret

# 数据目录
IMT_DATA_DIR=./data         # SQLite 数据库、上传文件、看板渲染输出

# 调度
IMT_SCHEDULER_TICK_SECONDS=30

# 初始管理员
IMT_ADMIN_USER=admin
IMT_ADMIN_PASSWORD=         # 首次启动时创建（不设则随机生成并打印）
```

---

## API 概览

### 管理端（`:9013`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | 登录（返回 HttpOnly Cookie + CSRF Token） |
| GET | `/api/admin/me` | 当前身份（role_level / allowed_platforms / must_change_password） |
| POST | `/api/admin/change-password` | 修改密码（旧密码验证 + CSRF） |
| GET/POST | `/api/admin/roles` | 角色列表（支持 `?platform=` / `?owner_id=` 筛选） |
| GET/POST | `/api/admin/roles/{id}/members` | 成员列表 / 添加成员 |
| POST | `/api/admin/roles/{id}/members/import` | CSV 批量导入（部门自动建档） |
| GET/POST | `/api/admin/templates` | 模板列表（支持筛选）/ 创建模板 |
| PUT/DELETE | `/api/admin/templates/{id}` | 编辑 / 删除模板 |
| GET | `/api/admin/instances` | 实例列表（支持状态/日期/关键词/平台/管理员筛选） |
| GET | `/api/admin/instances/pending-reviews` | 跨日期审核待办聚合 |
| GET | `/api/admin/stats` | 统计（按状态计数 + 完成率） |
| GET | `/api/admin/global-activated-members` | 全局搜索已激活成员（跨管理员共享，免二次激活） |
| GET/POST | `/api/admin/accounts` | 管理员账号列表 / 开设分级管理员（仅超管） |
| PUT/DELETE | `/api/admin/accounts/{id}` | 修改 / 删除（仅超管） |
| POST | `/api/admin/accounts/{id}/reset-password` | 重置为初始密码 Hds22999!（仅超管） |
| GET/POST/PUT/DELETE | `/api/admin/departments` | 部门管理（POST 全员可用；PUT/DELETE 仅超管） |
| GET | `/api/admin/audit-logs` | 审计日志（sub_admin 只见自己） |
| GET/POST | `/api/admin/channel-config` | 通道凭证（仅超管；写入加密、返回掩码） |

### 公开端（`:9020`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/app-events` | 量子密信回调（验签 → 解密 → 激活登记 → 回复链接） |
| POST | `/api/wecom/callback` | 企业微信回调（自建应用，兼容旧路径） |
| GET | `/h5/bind` | 身份登记页（姓名/手机号/部门，与名册比对） |
| GET | `/h5/task` | 任务提交页（拍照/上传/报数/文字说明，提交后锁定） |
| POST | `/api/h5/bind` | 登记提交（防重复提交 + 平台归属校验） |
| POST | `/api/h5/task/submit` | 任务提交（成功后回推「【任务名】提交成功」） |
| GET | `/h5/img/{name}` | 通知附图直链 / H5 图文页（按管理员命名展示） |

---

## 脱敏说明

> 本仓库已剔除 `.env`、数据库文件、上传文件、构建产物缓存与真实人员信息。
> 凭证全部通过环境变量或数据库加密存储注入，源码中不包含任何密钥。
> 人员姓名在测试中均为虚构，不对应真实员工。

---

## License

MIT

> AI生成
