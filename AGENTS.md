# AGENTS.md — IMTask 项目接手须知

> 给 AI 编码代理（dsh / Codex / Claude Code 等）的入口文件。**每次会话先读这一份，再动手。**
> 最后更新：2026-11-20（对照线上仓库 main HEAD=`4ba9d78`）。以**代码为准**，本文与本文件冲突时以代码为准并回来修本文。
> 2026-11-20 新增两条**强制纪律**：① 按改动范围分级测试（小改动禁止跑全量，见第六节）；
> ② 不做 Agent 自动截图验收（前端/H5 样式交给用户预览或真机验收，见第六节）。

> 📌 **致并行协作的 agent（2026-11-20 留言）**：跑测试**之前**先读第六节「按改动范围分级测试」——
> 单点小修 / 前端 / 文案**不要**跑全量 pytest（本机一次 ≈23 分钟），只跑相关专项文件 + `tsc` + `sync_review` 构建对账；
> 与本轮改动无交集的失败，单跑 1 次通过即止损，别二分、别写探针、别重跑全量。
> 另：你未提交的改动会让部署闸门（工作区 vs 评审库）变红 → 部署前先 `python scripts/sync_review.py --commit "..."`。

---

## 一、这是什么项目

以量子密信为起点、可扩展至企业微信的**定时点对点任务派发与提交统计平台**。

管理员在后台定义任务模板（谁、几点、提交什么、要不要审核），系统按时单聊推送到人，
用户在 H5 里提交，后台出完成率。技术栈：Python(FastAPI) + MySQL(5.7 兼容 / 纯 MySQL，2026-11-16 方案B 去 SQLite 化) + React18/Vite。

---

## 二、⛔ 四地结构（**最容易搞错的地方，先看这节**）

代码散在 **4 个目录**，各司其职，**不要混用**：

| 目录 | 角色 | git 状态 | 你要不要改 |
|---|---|---|---|
| `D:\im-task` | **开发源 + 部署源**（deploy 从这里打包） | ⚠️ **git 仓库但是 0 提交、无 remote** | ✅ **改这里** |
| `D:\im-task-review` | **脱敏评审库**（GitHub 同步用） | ✅ main（HEAD `2c78c81`，103 提交，有 remote） | ❌ **不要直接改**，由脚本同步 |
| `D:\im-task-admin` | **前端 React 源码** | ❌ 非 git 仓库 | ✅ 改这里（前端） |
| `D:\im-task-review-bak-*` | 历史备份 | 0 提交 | ❌ 忽略 |

### ⚠️ 陷阱 1：`D:\im-task` 的 git 是空的

```bash
cd D:\im-task && git log        # → fatal: 没有任何提交
cd D:\im-task && git status     # → 全部文件显示 ?? （untracked）
```

**这不是「有待提交的工作」，而是这个 .git 从来没被用过。**

🚫 **绝对不要在 `D:\im-task` 里执行任何 git 写操作**（`git add` / `git commit` / `git init` / `git reset`）。
那会造出一个与评审库完全无关的历史，导致部署闸门永久失效。

✅ **git 操作只在 `D:\im-task-review` 做**，而且**通过 `sync_review.py` 做**，不要手工 commit。

### ⚠️ 陷阱 2：想「看 commit 历史」要看对地方

用户说「结合 GitHub commit 了解项目」时，**唯一有历史的是 `D:\im-task-review`**：

```bash
cd D:\im-task-review && git log --oneline            # 真实历史（103 条，HEAD=2c78c81）
cd D:\im-task-review && git log -p -- backend/app/state.py   # 看某文件演变
```

在 `D:\im-task` 里看历史 = 看不到，别浪费时间也别下「项目没有历史」的错误结论。

---

## 三、日常工作流（**顺序不能变**）

```
① 在 D:\im-task 改后端（+ D:\im-task-admin 改前端源码）
② 跑测试（见第六节）
③ python scripts/sync_review.py --commit "消息" --push     ← 同步到评审库、提交并推送
④ 在验收汇报中原样附上两个完整 URL，并提醒用户可将桌面源码 diff 直接拖给评审端审计
⑤ 等用户明确确认验收通过并允许部署后，才执行 python scripts/deploy_remote.py --skip-deps --host <生产地址> ...
```

### 为什么第 ③ 步不能省

`deploy_remote.py` **第一步就是闸门**：比对「工作区 `D:\im-task`」与「版本库 `D:\im-task-review`」，
不一致直接中止部署。跳过 ③ 直接部署 = **一定被拦，白改**。

`sync_review.py` 做的事（全自动，别手工 cp）：
1. 后端 `app/` `scripts/` `tests/` 全目录 → 评审库 `backend/*`（**带脱敏**）
2. 前端 `D:\im-task-admin\src` → 评审库 `admin-ui/src`
3. `vite build` → 产物同步到两地的 `admin_static/`
4. 产物对账，并同步根目录 `AGENTS.md` 的验收规范

参数：`--skip-build`（跳过构建，仍同步源码）、`--commit-paths`（列出本轮允许提交的评审库路径，目录以 `/` 结尾）和 `--push`（推送评审库 `main`、打印验收链接并导出桌面源码 diff）。`--push` 可与 `--commit` 合用，也可用于重试已提交的推送。

### 部署脚本的坑

- `DEFAULT_HOST` 是**脱敏后的 `203.0.113.10`**（文档保留网段）→ **必须显式传 `--host`**
- **2026-11-11 部署原则（用户拍板）：tests/ 与任何 .db 严禁上生产机**——deploy_remote.py 已
  **彻底移除「远程 pytest」验收步骤**（历史版本曾临时上传 tests 跑完即删，现已删除该代码路径）；
  打包阶段有 `assert_no_tests` **硬断言**（包内出现 tests/ 或 .db/-wal/-shm 直接中止）；
  每次部署还会自动清理生产机上 tests/scripts 历史残留
- 部署前的**测试闸门按改动分级**（2026-11-20 修订，见第六节「按改动范围分级测试」）：
  A 级小改动 = 相关专项 pytest 绿 + `tsc` + `sync_review` 构建对账；B 级核心底层改动 = 代码冻结后 1 次全量 pytest。
  ⛔ 不再对任何改动都要求"先跑一遍全量"。CI/测试机与生产物理隔离
  （将来上 CI 也在隔离容器跑，跑完销毁）；部署验收只做线上运行态检查：
  healthz 四项 + echostr 回显 + 后台 200 + 静态资源逐个 200

### 运行时信息（改完要验证时用）

| 项 | 值 |
|---|---|
| 后台管理端口 | `9013`（React SPA + REST API） |
| 公网 / H5 端口 | `9020` |
| systemd 服务名 | `imtask` |
| 生产目录 | `/opt/imtask`（代码 `app/`、数据 `data/`、备份 `app.bak-<时间戳>`） |
| 健康检查 | `curl -s http://127.0.0.1:9020/healthz` → 四项全 `ok` 才算正常 |
| 生产 Python | `/opt/imtask/.venv/bin/python`（Linux，3.10） |

⚠️ **前端改了必须构建**：`D:\im-task\app\admin_static\` 是**构建产物**，源码在
`D:\im-task-admin\src`。只改源码不 build = 线上界面还是旧的。
`sync_review.py` 不带 `--skip-build` 时会自动 build。

### ⚠️ 本机 Node 环境坑（2026-11-12 实锤；构建必须走 sync_review 的内置 Node）

本机 PATH 首位 `C:\Program Files\nodejs\node.exe` = **v11.13.0**（`npm run build` / `npx tsc`
会因 ESM 语法直接报错；`C:\Users\lenovo\npm-global\node.exe` = v22.22.2 但**不在 PATH**）。
✅ `sync_review.py --commit` 的 `build_frontend()` 已改为**优先用 DSH 内置 Electron-as-Node
v24.21.0 直跑 `vite.js`**（`ELECTRON_RUN_AS_NODE=1`），npm 仅作回退；并修了 GBK 控制台下的
subprocess UTF-8 编码。**不要手动 `npm run build`**——前端改动后的标准构建 = 跑 `sync_review.py`
（2026-11-16 轮实测：`tsc --noEmit` 0 错误、`vite build` 成功、产物对账一致）。

---

## 四、业务口径铁律（**这些我全踩过，改错就是生产事故**）

### 4.1 平台（办公软件）口径
- 成员的「已激活」按**角色所属平台**判定，**不是当前通道**（`store.platform`）
- `is_phone_activated(phone, platform=None)` = **任意平台**已激活（不是回退当前通道）
- `_plat(None)` 会**回退到 `store.platform`** → 写「任意平台」语义时**不要用它**
- 激活关系（`delivery_target`）**跨管理员、跨平台共享**：任一平台绑过即算已激活

### 4.2 管理员分级
- `admin` = 超管：全局只读 + 账号管理 + 通道配置；**业务写一律 403**（`SUPER_ADMIN_READONLY`）
- `sub_admin` = 分级管理员：只读写自己 `owner_id` 名下的数据
- 超管改业务数据要用**成员池接口**（有独立白名单），不是角色成员接口

### 4.3 字段解析（**易错**）
```python
# ❌ 错：get_admin_by_id 返回原始行，allowed_platforms 是 JSON 字符串 '["zdxlz"]'
for p in target.get("allowed_platforms"):   # → 迭代出单个字符！

# ✅ 对：必须解析
plats = state.parse_admin_account(target).get("allowed_platforms") or []
```

### 4.4 任务实例模型（**扩展功能时最卡**）

- 2026-11-18 本地评审：停用模板或成员，只取消失去资格的待办实例。移除成员或模板角色时，仍有其他启用关联角色则保留任务，否则取消待办、逾期未交与待重交。已完成、已提交、已发送及其提交记录保留。重新启用不复活已取消的旧实例。已完成链接在原有令牌与访问校验通过时可查看上次提交内容及附件。

- 2026-11-17 本地评审：模板可保存空角色。删除角色时所在模板同步解除引用，未完成实例按移除成员口径保留为「已取消」。空角色模板可在后台选中和重新绑定，派发与手推均不生成实例。

- 定期任务唯一键 = `(template_id, phone, period_key)`；实例**不存 role_id**，只能靠模板
  `role_ids` 反查
- 2026-11-19 同日多批次手动推送（批次键，⛔ 格式别改）：`POST /templates/{id}/push-now` 已是
  **两段式** —— 不带 `confirmed` 时若本周期/历史上已推送过，只回
  `{"needs_confirm":true,"target_count":N,"canonical_period_key":...}`（**不写冷却 marker、不建实例**）；
  管理员确认后带 `?confirmed=1` 再来才真正受理（冷却照旧 2min 429），并走**批次键**
  `{canonical}#YYYYMMDDHHMMSS`（同秒碰撞追加 `-2/-3`）→ manual 形如 `once#20261119160230`、
  periodic 形如 `2026-11-19#20261119160230`。批次键自带唯一性，幂等键/实例唯一键天然放行重推；
  建完新批次后旧批次的未完成实例被置 `cancelled`（notice: pending/sent 且未回执；form: pending/rejected，
  已完成/已回执不动）→ H5 待办不再刷屏。**首次推送仍走 canonical 键**，调度级 `push_now(reprocess=False)`
  与 `run_once` 定时链行为逐字节不变。`#` 不参与 LIKE 通配：统计/导出按批次键精确匹配
  （`state._stats_period_keys_for` 单独分支），后台实例页日期筛选已在 `_instance_filter_sql` 显式兼容。
- 2026-11-19 实例角色快照：`task_instance.role_name_snapshot`（SCHEMA_VERSION=2）固化创建时刻的
  `role.name` 拼接值；前端列表/详情**优先**用它，模板改绑角色后历史实例不再「变成新角色」。
  统计面板的成员部门有**兜底**：当前角色查不到的手机号，按同 `owner_id` 名册取最新非空 `dept_path`，
  明细/导出/部门下钻共用同一份结果（模板改绑角色后不再整片塌成「未分配部门」）。
- 2026-11-19 通知 `receipt_mode='link'` 改**完整投递**（`scheduler._deliver_notice`）：正文发给全部人
  → 媒体（图片→文件→视频，原生优先/H5 兜底）→ 收尾按模式分流（text=回执短语提示，
  link=单独一条 H5 确认链接）。后台 `NoticePhonePreview` 与实发顺序一一对应（所见即所得）。
  H5 回执页 `h5/notice-receipt.html` 已重排为单卡片阅读版式（无发布时间/送达时间、图片满幅无「图片」标题），
  ⛔ 不得引入外部 CDN 或 `crypto.randomUUID()`（纯 HTTP 环境）。
- `period` 枚举仍只有 `day / week / month`。不定期模板用 `is_adhoc=1` 标记，只出现在 H5 自主上报，
  手动推送返回 400。每次打开只签发表单令牌，成功提交才原子创建实例并消费令牌，各设备可重复打开。
  旧未提交记录保留于数据层但不显示在待办或后台实例。工作台「已提交」汇总本平台已提交记录。
- 已完成和待审核的提交可由本人或归属分级管理员更正。编辑时更新原提交，保留实例、提交时间和审核状态，
  写入审计记录并用版本校验防止覆盖他人的更正。提交结果由 H5 提示，机器人无需发送成功回执。
- 截止周期三档（2026-11-12 E1）：`has_deadline` 开关 + `deadline_type ∈ {same_day, weekend,
  month_end}` + `deadline_time`（HH:MM）；无截止 = 哨兵 `9999-12-31 23:59:59`（H5 显示「不限」，
  Token 720h）
- **逾期补交（resubmit）流程已物理下线**（2026-11-12 E3：用户端按钮/触发词、管理端审核端点、
  调度逾期文案、dashboard 面板全删；`resubmit_*` DB 列保留不读写）；**驳回重交**（rejected →
  新链接、重交窗口 72h）保留

### 4.5 激活与实例生成（2026-11-09 拍板口径；2026-11-10 方案A 双通道增补；2026-11-11 任务一收口；2026-11-12 A1 导入收口）
- **2026-11-11 任务一（用户拍板）：私聊零激活**——未绑定用户发任意文本（两通道）→
  统一回复「尚未录入您的业务权限，请联系管理员录入激活」；**不解析、不反查、不写任何表**
  （delivery_target / pending_member / member 一律不落）。原「路径 A/B/C/U」全部废除；
  H5 登记页保留（老链接仍可用，但机器人不再主动下发登记链接）。
- **唯一激活路径 = 管理端录入**：后台导入/加成员/组织架构选人（Org Picker）→
  系统自动反查官方通讯录完成绑定。用户侧零操作。
  - **CSV 批量导入**：手机号必填，姓名选填。支持手机号单列和姓名、手机号两列自动识别。
    先查同平台全量可用的已激活成员池，未命中再查官方通讯录。姓名为空时回填核验到的姓名，
    已填姓名时必须匹配。部门自动回填 `dept_path`，多余列或任一错误**整批零写入**；
    单次上限 `IMPORT_ROW_LIMIT=5000`
- **2026-11-10 方案A（导入即反查绑定）双通道**：
  - **量子密信**：通讯录响应官方 decryptV2 算法本地解密（`app/channels/zdxlz_crypto.py`；
    10-27 工单「KMS 托管解不开」结论作废——官方文档+当日实测推翻）。
    导入/加成员/组织选人 → `user/search(phone)` → union_id → 绑定（`external_type='union_id'`）；
    发送按 `receive_type="union_id"` 直投（官方口径：不要求用户先跟机器人说过话）；
    存量 'user' 绑定行为不变（迁移脚本 `scripts/migrate_delivery_to_union_id.py`，默认 dry-run）
  - **企业微信**：①自建应用 `POST /cgi-bin/user/getuserid`（手机号精确反查）；
    ②智能机器人 MCP 通讯录（`app/channels/wecom_mcp.py`，姓名+部门匹配，唯一命中才绑）。
    aibot 长连接直接按 userid 投递。⚠️ getuserid 凭证 = 自建应用 corpid+secret（与 aibot bot_id/secret 互不相干）；
    MCP apikey（IMT_WECOM_MCP_APIKEY）只是兜底通道，走完会切回自建应用路径
  - **resolver 平台隔离**：`store.contacts`（zdxlz）/ `store.wecom_contacts`（wecom）各管各的，
    跨平台导入绝不串用解析器（有专项用例守住）
  - **组织架构选择器（2026-11-11 任务二；组件就绪、入口当前隐藏）**：`OrgPickerModal.tsx`
    已实现并接线（官方部门树面包屑下钻 + 人员勾选 + 整部门打包；`GET /org/departments`、
    `GET /org/department-users`、`POST /roles/{id}/import-from-org` 三接口就绪）。
    ⚠️ 角色页「从组织架构添加」按钮**自 2026-11-12 A1 起被 JSX 块注释隐藏**（注释原文
    「待明确启用」，见 `RolesMembersView.tsx` 约 L1342-1351）→ **当前 UI 不可达**；
    恢复 = 取消该块注释即可（组件、接口、弹窗渲染均已就绪）
- 2026-11-17 本地评审：快速添加与 CSV 优先复用同平台有可用绑定、未标记离职的名册档案。姓名不符会拦截，本地池未命中时走官方手机号核验。正式后台在无法核验时拦截写入。
- 管理后台的部门架构和成员档案按官方通讯录口径只读。分级管理员可通过「同步最新部门」刷新自己在当前平台的去重名册。确认查无此人时保留记录并标记离职预警，查询失败单独计数。
- 密信多部门默认优先匹配「中国电信德阳分公司」。可用 `IMT_ORG_PREFER_DEPT_KEYWORD` 覆盖；多个匹配优先主部门，未匹配回落主部门或第一项。
- 导入只成名册；**导入即绑定**，无绑定 → 实例生成层**直接不建实例**
- **两道过滤是独立条件，都要在取数层拦**：`未激活`（无 delivery_target）与
  `已停用`（member.enabled=0）——分别对应 11-09 已验与 11-10 已验两条口径
- **停用是「行级」语义，不跨管理员**：同一手机号在 A 管理员组里停用 = 只停 A 侧；
  B 组若仍 enabled=1，B 的模板照常给 TA 建实例发消息（2026-11-10 用户确认的正常口径，
  勿当成 bug 修）。判定时永远按「模板所属角色关联的 member 行」取 enabled，不按手机号全局聚合

---

## 五、代码导航（**不要通读，按需 grep**）

### 后端（`D:\im-task\app\`）

| 文件 | 行数 | 管什么 |
|---|---|---|
| `state.py` | **5050** | 数据访问 + 全部业务规则（模板/角色成员/实例/提交/审核/H5令牌/统计取数/adhoc菜单） |
| `api/admin.py` | **3064** | 后台 REST（会话/模板/角色成员/实例/日志/统计面板/组织架构/部门树） |
| `scheduler.py` | **1343** | 周期纯函数 + 调度循环（提醒/逾期/驳回重交/失败退避/8 线程投递/令牌桶/push_now） |
| `api/h5.py` | **742** | H5 绑定与三类任务提交（截止/令牌/删除硬闸统一拦截） |
| `db.py` | **712** | 纯 MySQL 数据层（PooledDB 连接池 + 事务代理 + SQL 适配层；18 表 DDL 逐列对齐） |
| `api/events.py` | **464** | 回调入口（私聊固定提醒 /【待办】/【菜单】指令；零落库红线） |
| `api/wecom_callback.py` | **182** | 企微通道入口（解 XML → 调同一业务函数） |
| `throttle.py` | **204** | 令牌桶限流（25 QPS，8 线程投递链） |
| `h5log.py` | **138** | H5 访问日志按天切割 |
| `channels/contacts_cache.py` | **106** | 通讯录查询 TTL 内存缓存（严格实名校验链） |
| `channels/wecom_contacts.py` | **189** | 企微通讯录 resolver（手机号/姓名+部门反查） |

> 通道实现全集在 `app/channels/`（zdxlz.py 594 / wecom.py 463 / wecom_aibot.py 499 /
> wecom_mcp.py 225 / zdxlz_contacts.py 276 / wecom_crypto.py 239 / zdxlz_crypto.py 110），按需 grep。

### 前端（`D:\im-task-admin\src\`）— **超大单文件，禁止通读**

| 文件 | 行数 | 注意 |
|---|---|---|
| `components/imtask/RolesMembersView.tsx` | **2759** | 角色/成员/CSV 导入/部门管理，最复杂 |
| `components/imtask/TemplatesView.tsx` | **2316** | 模板编辑（截止三件套/执行日/不定期） |
| `components/imtask/InstancesView.tsx` | **1336** | 实例列表与审核 |
| `components/imtask/StatisticsView.tsx` | **1027** | 任务数据统计面板（单任务独占/Excel 导出） |
| `components/imtask/OrgPickerModal.tsx` | **394** | 组织架构选择器（组件就绪，入口隐藏见 4.5） |
| `components/imtask/PendingReviewPanel.tsx` | **288** | 审核待办 |
| `lib/store.tsx` | **237** | 全局状态（platform / allowedPlatforms / ownerFilter） |
| `App.tsx` | **236** | 导航与 Tab 路由 |

**读法**：先 `grep -n "关键词"` 定位行号，再读那一段。**不要整文件读进上下文**。

### 文档（`D:\im-task\docs\`）
- ⚠️ **README.md 已过时**：说「28 条测试」（实际 505+）、说「admin_static 单文件 SPA」
  （实际是 React+Vite 构建产物，源码在 `D:\im-task-admin`）
- `交接包-20261023.md`：业务/数据模型口径（**部分过时，以代码为准**）
- `拓展需求设计-用户触发式任务与数据统计-20261109.md`：设计稿（**部分已实现**：用户触发
  【菜单】不定期任务 2026-11-12 上线、任务数据统计面板 2026-11-16 上线；其余仍为设计稿，以代码为准）
- `HANDOFF.md`：历史交接快照（2026-11-12 批次），**不再作为入口交接**——以本文件 + 代码为准

---

## 六、测试打法（**在这里最烧时间，按这个来**）

### 🔴 按改动范围分级测试（2026-11-20 用户拍板，**优先级高于本节其它测试口径**）

> 背景：2026-11-19 那轮为了一个小改动连跑两次全量（各 23 分钟），最终失败的还是一条与本轮 diff 无交集的用例。
> 全量 pytest 是**稀缺资源**，不是默认动作。

**A 级（小改动，禁止跑全量）**——纯前端 / 纯 H5 样式 / 文案微调 / 单点小修复：
1. 只跑与改动**直接相关**的专项 pytest 文件（例：`pytest tests/test_20261119_manual_repush_batches.py`，目标 ≤2 min）；
2. `tsc --noEmit`（前端改动必做）；
3. `python scripts/sync_review.py`（构建 + 产物对账）。
⛔ 明确禁止：`pytest`（不带文件名）、`pytest tests/`、任何"顺手跑一下全量看看"。

**B 级（核心底层，允许跑 1 次全量）**——`db.py` 表结构/迁移、调度主循环 `run_once`、全局鉴权/中间件、
跨模块大重构：
1. 仍先跑专项红→绿；
2. **代码完全冻结**（含前端 build）后，跑**最多 1 次**全量 pytest；
3. 全量失败只按「与本轮 diff 是否有交集」两分法处理，不展开调查（见下条）。

**无关偶发失败 5 分钟止损**：与本轮改动无交集的失败，单跑 1 次通过即判定为既有 flake，
在汇报里写「单跑 X/X 通过，判为环境性 flake，未修复待查」，**严禁**反复重跑全量、做子集二分或写探针深挖。
判定顺序：① 该文件单跑 1 次 → ② 单跑绿即结案 → ③ 单跑仍红且与 diff 有交集才允许继续查。

### 🚫 不要自己截图做视觉验收（2026-11-20 用户拍板）

前端 / H5 样式改完，**不要**再用 headless Chrome 截图、调视口宽高、搬运截图文件（上一轮为此来回搬了 6 次、重截 2 次）。
做完 `tsc --noEmit` + `sync_review.py` 构建对账即可交付，**视觉由用户通过预览链接 / 真机验收**（更快也更真实）。
例外：用户当轮明确要求"给我看截图"。

### ⚠️ 跑测试前：必须用项目的 `.venv`（**这条最容易浪费时间**）

用错解释器会看到 **17 个 collection error**，让你误以为环境坏了、跑去修依赖甚至改代码：

| 解释器 | 结果 |
|---|---|
| `D:\im-task\.venv\Scripts\python.exe` | ✅ 全绿（2026-11-17 本地评审基线：**525 passed / 11 skipped**，在 `im_task_test` 上跑） |
| `C:\Users\lenovo\anaconda3\python.exe` | ❌ 17 errors: `No module named 'fastapi'` |

### 跑测试
```bash
cd D:\im-task
# A 级（默认动作）：只跑本次改动直接相关的文件
.venv\Scripts\python.exe -m pytest tests/test_xxx.py -q
# B 级（仅核心底层，代码冻结后 1 次）：本地评审基线 638 passed / 11 skipped（2026-11-19，HEAD=4ba9d78）
.venv\Scripts\python.exe -m pytest
```
⚠️ 判通过看**退出码**：`-q` 下 pytest 9 的最终计数行可能被控制台吞掉，`echo $?` == 0 才算绿。

`pytest.ini` 已配好 `testpaths = tests`、`pythonpath = .`、`addopts = -q --strict-markers`，
**不需要额外加 `-o addopts=""`**（我上一轮一直这么写是多此一举）。

> `.venv` 缺依赖时：`.venv\Scripts\pip.exe install -r requirements.txt`

> **2026-11-17 本地评审 `2479ac6`**：原 7 个先行红测已转绿，同文件新增连续调度验证。
> 全量凭证为 `525 passed, 11 skipped, 1 warning`。本地评审通过后仍按约定等待用户验收。

### 口径变更时（**禁止逐条盲改**）
1. **先写专项新用例**：`tests/test_<日期>_<主题>.py`，**必须是 pytest 标准写法**
   （`def test_xxx()` + `assert`）。⛔ 用顶层脚本 + print 的话 pytest 收集不到，
   会误以为「全绿」——这个坑我踩过。
2. 红（复现缺口）→ 修生产代码 → 绿
3. **再批量适配旧用例**：跑全量拿失败清单，按**类别**批量改（缺前置 / 断言过期 / 语义冲突）
4. 语义冲突类**必须列清单请用户拍板**，不许自行按新口径改写
5. **反向验证是「修复」的验收标准**：拆掉守卫 → 对应用例必须转红 → 恢复 → 全绿

### ⚠️ 测试设计的坑
- 造数据要**造真实的激活绑定**（`upsert_delivery_target`），否则新口径下「未激活不建实例」
  会让用例拿不到实例
- 涉及历史 `period_key` 时，激活时刻必须设在**那之前**（`upsert_delivery_target(..., activated_at=...)`）
- 设计「跨平台隔离」用例时，必须是**同一个手机号在两个平台各有一条名册**，
  否则去掉平台过滤也测不出来（用例成摆设）

---

## 七、禁区（越界即事故）

### 🔴 最高优先级红线：消息幂等 / 刷屏零容忍

用户最高避讳是**机器人重复消息刷屏**（历史上发生过每 30 秒刷一条的真实事故）。
任何涉及**消息发送**的改动：
- 同一消息对同一接收人在同一周期内**有且仅有一条**（调度器 30 秒一轮 tick，
  必须以**连续跑 ≥3 个 tick 只发 1 条**作为验收标准，单次发送成功不算过）
- 幂等拦截必须在**发送动作之前**（查记录后再发），不允许「先发后记靠唯一约束拦」
- 回调入口（events.py 对话触发）同理：重试回调不得产生重复回复

### 部署时窗 - 避开真实发送点

生产每天有固定发送时点（如 08:15 / 11:50 / 12:00 / 12:30 / 15:00 / 17:19 / 17:50 / 18:00 / 20:00，
随模板调整）。**部署会重启服务，重启瞬间会丢失 tick**：
- 动消息路径的改动：选两个时点的**间隙**部署
- 动调度/激活/绑定逻辑：部署后盯一轮下一个时点是否正常发出

### 生产环境
- 生产目录 `/opt/imtask/` **只允许 `deploy_remote.py` 写入**，**不要直接 cp/scp**
- **tests/ 与测试数据库严禁上生产机**（2026-11-11 用户拍板）：生产机上不上传、不运行
  任何测试套件——测试会真实读写文件/建库；打包与部署脚本有硬断言兜底，
  部署时还会自动清理历史残留
- `rm` **禁止落在生产根目录** —— 宁可留垃圾报告用户
- 生产数据在远端 MySQL `im_task` 库（同机 127.0.0.1:28620，凭据在 `/opt/imtask/.env`）；
  读生产库用 `mysql -h127.0.0.1 -P28620 -uimtask -p im_task -e "select ..."`，
  ⛔ 只读，绝不 UPDATE/DELETE
- `data/im_task.db`（及 -wal/-shm/.bak-*）是 2026-11-16 方案B 之前的 SQLite 历史残留，
  服务已不再读写；清理动作见当轮报告，**清理前必须先归档**
- 部署前自查：`ls /opt/imtask/*.py` 必须 = 0

### 读生产库（MySQL；2026-11-16 方案B 后）
```bash
# 判断「现在线上是什么样」（删没删/值对不对）→ 直接只读查询
ssh <生产> "mysql -h127.0.0.1 -P28620 -uimtask -p'<密码见/opt/imtask/.env>' im_task -e 'select ...'"
```
⚠️ 旧的 `sqlite3 im_task.db` / `immutable=1` 读法已随方案B 作废（文件是历史残留，读到的
是 2026-11-13 之前的旧数据，**用它判断现状必然误判**）。

### 提交纪律（共享仓库，另一个 agent 也在用）
- 提交前 `git status` **逐文件核对**，只 `git add` **本轮自己改的文件**
- 发现清单里有**不是你写的东西** → **停下报告用户**，等指示
- ⛔ **禁止 `git add -A` / `git commit -a`**
- **2026-11-18 用户更新流程：每轮开发完成并通过本地验证后，先推送本轮提交供用户 / Gemini 验收，无需重复询问推送权限；必须等用户明确确认验收通过并允许部署后，才可部署。** 不得把推送成功视为部署授权。

### 其它
- 不改 `_legacy/`（旧实现，已归档）
- 不改 `D:\group-ops\`（另一个线上项目，只读参考）
- **2026-11-18 用户确认：H5 改版先核对生产实际启用的入口，只改当前在用页面。** H5 登记入口已关闭，代码与旧路由保留；此后不纳入改版，除非用户明确要求。
- **2026-11-18 用户确认：UI 调整用本地实时预览，Playwright 使用有头模式，方便边改边验收。** 启动命令为 `powershell -File scripts/dev_preview.ps1`。H5 保存自动刷新，PC 后台走 Vite HMR。预览只使用本机 `im_task_test`，全量 pytest 会清空该库，需串行执行，测试完可重启预览重新生成演示数据。
- 密钥只走环境变量，**不进仓库**

### 🔒 docs/ 目录的 PII 红线（同步盲区，必须手动警觉）
工作区 `D:\im-task\docs\` 里有**多份文件名含真实公网/内网 IP 的部署记录**（文件名形如
`部署记录-<真实IP>-<日期>.md`，共两份，涉及生产地址与内网地址各一）。
`sync_review.py` 只同步 `app/scripts/tests`，docs **不会**自动进版本库——这是好事，
⛔ **任何情况下不要手工把 docs/ 提交进评审库**，除非逐份确认已脱敏。

### 📈 数据库容量预警义务
2026-11-16 方案B 已完成 SQLite → MySQL 全栈切换（PooledDB 连接池 + InnoDB 行级锁）。
出现以下任一信号，**必须主动向用户提出扩容/优化预警**（不要等问题发生）：
1. 需要多服务器部署/负载均衡；2. 单表 > 百万行或库 > 1GB；
3. 高频写锁竞争（`SHOW ENGINE INNODB STATUS` 出现长等待）；4. 连接池打满（16 上限告警）。
测试底座在本机 MySQL `im_task_test`（tests/conftest.py 有防误连生产熔断锁：
库名必须 `_test` 结尾 + Host 必须本机，否则 pytest.exit）。

### 协作规矩
另一位 agent（workbuddy）在同仓库工作，其完整协作规矩见工作空间根目录
`workbuddy协作规矩-20261109.md`（7 条：完成=凭证 / 只提交自己的改动 /
生产只经部署脚本写入 / DB 只读纪律 / 语义变更先写专项用例 / 同步部署走工具 /
删除四要素申报）。**本文件第七、八节与它重叠，冲突时以更严者为准。**

---

## 八、交付标准（做完了 ≠ 说做完了）

任何「已完成 / 已修复 / 已删除」**必须附当场凭证**，至少一种：

- API 响应原文（含 HTTP 状态码）
- DB 查询结果行
- 审计表 `admin_audit` 的记录
- 测试 / 构建输出尾部（含计数行；**A 级小改动给专项 pytest 的退出码/计数行即可**，不必凑全量凭证）

**没有凭证只能写「未执行 / 待执行」。** 宁报「我没做」，不报「我做完了」。

每次推送评审仓库成功后，面向用户的验收汇报**必须原样附带脚本输出的两个完整 URL**。两条链接都要使用完整的 40 位提交哈希，供外部评审工具直接拉取纯文本 diff。

- Commit 页面 `https://github.com/yongen777/im-task-review/commit/<full_hash>`
- 纯文本 Diff `https://github.com/yongen777/im-task-review/commit/<full_hash>.patch`

每次推送后还要提醒用户，可把桌面的 `imtask_review_latest.patch` 直接拖给评审端审计。脚本会打印该文件的完整路径。文件包含本次提交的统计和源码 diff，按 UTF-8 无 BOM 保存，diff 正文排除 `.js`、`.css`、`.map` 文件与 `dist/` 目录。

---

## 九、快速上手checklist

第一次接手时按顺序做：

```bash
# 1. 看历史（唯一有历史的地方）
cd D:\im-task-review && git log --oneline -20

# 2. 看项目边界与结构
type D:\im-task\README.md                     # 注意：部分内容已过时，见第五节

# 3. 确认四地状态
cd D:\im-task      && git log 2>&1 | head -2    # 应该报「没有提交」——正常，别修它
cd D:\im-task-review && git status --short      # 应该干净

# 4. 确认基线测试全绿（⛔ 只做一次：首次接手的体检，之后按第六节分级，别再随手跑全量）
#    需要：本机 MySQL 服务在线（im_task_test 自动建库）
cd D:\im-task && .venv\Scripts\python.exe -m pytest

# 5. 确认两地代码一致（部署闸门前提）
python scripts/sync_review.py --skip-build      # 期望输出「评审库无内容变更」
```

5 步都过 = 环境健康，可以开始改需求。
只读自检时，第 5 步用部署前的一致性检查函数和静态产物校验函数。同步脚本会写入评审库。

---

## 十、有疑问时

- 口径不确定 → **读代码**，不要按「看起来合理」推测
- 需求有歧义 → **问用户**，不要自己拍（尤其涉及数据删除、权限、口径变更）
- 改动跨多文件 → 先列**受影响清单**再动手
- 拿不准要不要动生产 → **不动**，先报告

---

## 十一、每日工作总结（OA 工时台账 · 必须按 oa-workload 技能执行）

> 用户说「总结今天的工作 / 记工时 / 记到 OA / 出周提交单 / 补录交付物」时，
> **必须**按本工作区自带的 oa-workload 技能执行——产出格式与 2026-11-02 起的历史记录
> 逐字一致（同一份脚本、同一份台账；格式由脚本机械保证，**不要手写任何台账文件**）。

- 技能位置：`D:\im-task\skills\oa-workload`
  动手前先读：`HANDOVER-codex.md`（交接、并发纪律、坑位）→ `SKILL.md`（全部口径与命令）→
  `references/deliverable-packaging.md`（交付物打包范式）
- 运行：`C:\Users\lenovo\anaconda3\python.exe D:\im-task\skills\oa-workload\scripts\oa.py <命令>`
- 台账真源：`C:\Users\lenovo\OA\_ledger\ledger.jsonl`；填报与交付物在
  `C:\Users\lenovo\OA\<年-月>\<项目>\W_xxxx-xxxx\<日期>\`
  （**两个目录都在工作区外**：沙箱不允许写时，向用户申请加入可写白名单，或批准一次写入）
- 硬规则（违反即事故）：
  - 计数唯一依据 = 当天**真实完成**的工作（无下限、无上限、不注水、不挪日期）；工时 ≥8h 是硬闸门
  - 交付内容只能选白名单 15 类；**不虚构交付物**；交付物 = 真实代码 / 测试 / 文档
    （⛔ diff / 验收报告 / 执行总结；README 只做索引）；打包前脱敏手机号与 IP
  - **先打包 zip，再 log**；同一日期不二次 log（改附件用 `rerender`）；
    `approved=false` 保持待审核，绝不代标
  - 多来源分开 `--source`（workbuddy / teleagent / ai_studio / other_tool / manual），
    工时只算一次；不猜 OA 字典值；OA 页面提交由用户人工完成
  - 台账**同一时刻只能一个 agent 写**（与 dsh / workbuddy 共享）：写前先 `status`，
    已有记录就 `rerender` / 汇报，不重复 log
- 汇报必带数字：件数 / 工时 / 效能分 / 待确认项。

> AI生成
