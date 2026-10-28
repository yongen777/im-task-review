# 通用 IM 办公任务调度平台（IMTask）

以量子密信为起点、可扩展至企业微信 / 飞书 / 钉钉的**定时点对点任务派发与提交统计平台**。

管理员在后台定义任务模板（谁、几点、提交什么、要不要审核），系统按时单聊推送到人，
用户在 H5 里提交，后台出完成率。

> **实现现状**：服务端在 `app/` 包内，按《后台开发规格书 v1.2》实现，测试 35 条通过 / 11 条跳过。
> 旧的 P0 实现（`src/imtask/`）已归档，不在本仓库内。

## 从这里开始

- **业务口径真源**：[`docs/交接包-20261023.md`](docs/交接包-20261023.md) —— 数据模型、触发链路、通道结论。
- **实现口径真源**：[`docs/后台开发规格书-20261023.md`](docs/后台开发规格书-20261023.md) —— 表结构、接口约定、状态机。
- **接口契约快照**：[`docs/api-openapi-20261026.json`](docs/api-openapi-20261026.json)。

## 边界

- 独立仓库，**不改** `D:\group-ops\`（线上生产，只读参考）
- 不引 LLM、不做群聊、不做聊天记录存储
- 没有「群」概念：不做 @、不做六命令解析、不做群绑定
- 密钥只走环境变量，不进仓库

## 目录

```
docs/                设计与交接文档
app/                 服务端（当前实现）
  config.py            配置（端口 P8 / 基址 scheme P9 / 同端口 P10 均在启动时校验）
  db.py                SQLite schema（10 业务表 + 3 支撑表 + schema_version）
  state.py             数据访问 + 业务规则（模板 / 角色成员 / 实例 / 提交 / 审核 / H5 令牌）
  scheduler.py         周期纯函数 + 调度循环（提醒 / 逾期 / 驳回重交 / 失败退避重试）
  channels/zdxlz.py    平台通道（token 缓存、发单聊、回调验签解密）
  api/events.py        回调入口（三条激活路径 + 三条防冒绑护栏）
  api/h5.py            H5 绑定与三类任务提交（魔数嗅探、流式限长、零残留）
  api/admin.py         后台 REST（会话 / 模板 / 角色成员 / 实例审核 / 日志 / 统计导出）
  admin_static/        后台 SPA 运行期产物（由 admin-ui/ 构建后拷入，不随仓库提供）
  h5/                  H5 页面（task / bind）
scripts/
  check_app.py         9 个模块的 import 探针 + 配置体检（每批落盘后先跑它）
  create_admin.py      管理员建号 / 重置密码
tests/                pytest（2A/2B/2C + 修正包 + XSS 运行时）
  tools/              Node 版渲染检查脚本
_legacy/              旧 src/imtask 实现与其测试（已归档，不在本仓库内）
data/                 本地库与上传文件（不入库）
```

## 跑起来（双端口）

两个监听口：**业务 9012**（平台回调 + H5）、**后台 9013**（管理界面），同进程两个监听。

```bash
cd backend
cp .env.example .env                                # 首次：填三个必填项（见下）
python scripts/check_app.py                         # 先确认各模块都能 import
python -m app.main
#   业务服务  http://<IMT_PUBLIC_BASE_URL>/healthz
#   后台管理  http://127.0.0.1:9013/login
```

`.env` 里**三个必填项没有默认值**（缺任一项启动直接失败，提示「未配置端口，请向运维申请后写入 env」）：

| 变量 | 说明 |
|---|---|
| `IMT_PUBLIC_PORT` | 业务端口（本机占位 9012） |
| `IMT_ADMIN_PORT` | 后台端口（本机占位 9013，⛔ 不能与业务端口相同） |
| `IMT_PUBLIC_BASE_URL` | 对外基址，**必须带 scheme**（如 `http://1.2.3.4:9012`），用来拼 H5 链接 |

其余变量：

| 变量 | 用途 |
|---|---|
| `IMT_APP_ID` / `IMT_APP_SECRET` | 取 token + 发单聊（缺任一个 → `/healthz` 的 channel=unconfigured） |
| `IMT_ENCRYPT_KEY` / `IMT_VERIFY_TOKEN` | 事件回调验签解密（缺 → crypto=unconfigured，平台事件解不了密） |
| `IMT_API_BASE` | 平台地址，默认 `https://imtwo.zdxlz.com` |
| `IMT_DATA_DIR` | 数据目录（默认 `./data`） |

接口契约快照：`docs/api-openapi-20261026.json`。

### 建号

```bash
.\.venv\Scripts\python.exe scripts\create_admin.py --username admin
# 会交互式要密码（≥8 位、含字母与数字）；加 --must-change 强制首登改密
```

### 测试

```bash
python -m pytest -o addopts="" -q
# 35 passed, 11 skipped（2A / 2B / 修正包 / 2C 分页与安全 / XSS 运行时；11 条为旧 HTML 结构断言，已跳过）
```

⚠️ Windows 上必须装 `tzdata`（`zoneinfo` 没有它直接 `ZoneInfoNotFoundError`），
`requirements.txt` 已包含；`TEMP/TMP` 建议指到 D 盘，C 盘满会出假故障。

## 端口职责

| 端口 | 默认 | 暴露 | 提供 |
|---|---|---|---|
| **业务** | `0.0.0.0:9012` | 公网可达（平台主动回调） | `POST /api/app-events`、`/h5/task`、`/h5/bind`、`/api/h5/*`、`/healthz` |
| **后台** | `0.0.0.0:9013` | 内网 / 白名单 | `/`、`/login`、`/api/admin/*`、`/healthz` |

⚠️ 后台目前是「账号密码 + HTTP 明文」一层保护，**上线前必须改掉初始密码**，建议套 HTTPS 反代。

## 已知未做

- 平台图片 / 文件消息发送（需先上传拿 fileId，未验）
- 通讯录 `user/search`（响应加密，回调算法不适用）
- 机器人进群入口（客户端只列「自定义消息推送机器人 / 自定义会话机器人」）
- 服务器部署（等运维给端口与公网基址）
