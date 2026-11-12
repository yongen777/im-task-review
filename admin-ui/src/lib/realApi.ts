/* ================================================================== */
/* Real-Data API Adapter（2026-10-28 源码级根治）                        */
/* ------------------------------------------------------------------ */
/* 作用：把前端的 /api/admin/* 请求真实转发到 IMTask 后端（服务器落库）， */
/* 并做"后端字段 ↔ 前端组件字段"的双向映射。                             */
/* 映射逻辑已在 2026-10-27 于编译产物补丁上全流程验证通过（登录真校验、   */
/* 模板改时点真实落库、日志真数据、data_keys 缓存补回、stats by_status）。 */
/* UI 组件零改动：适配层输出与 lib/types.ts 原有结构保持一致。            */
/* ================================================================== */

import type {
  AdminAuditItem,
  AdminHealth,
  InstanceStats,
  ReminderLogItem,
  RoleItem,
  RoleMemberItem,
  TemplateItem,
  TaskInstanceItem,
} from './types';

/* ---------- CSRF 令牌（登录/会话恢复时写入，写操作携带；localStorage 持久化防刷新丢失） ---------- */
let csrfToken = (() => {
  try {
    return localStorage.getItem('imt_csrf') || '';
  } catch {
    return '';
  }
})();

export function setCsrfToken(token: string) {
  csrfToken = token || '';
  try {
    if (csrfToken) localStorage.setItem('imt_csrf', csrfToken);
    else localStorage.removeItem('imt_csrf');
  } catch {
    /* localStorage 不可用时退化为内存态 */
  }
}

export function getCsrfToken(): string {
  // 令牌是会话级的，同一会话的多个标签页共用一份；内存副本会在别的标签页
  // 重新登录（换会话）或退出（清令牌）后过期，所以每次都以 localStorage 为准。
  try {
    const stored = localStorage.getItem('imt_csrf') || '';
    csrfToken = stored;
    return stored;
  } catch {
    return csrfToken;
  }
}

function clearCsrf() {
  csrfToken = '';
  try {
    localStorage.removeItem('imt_csrf');
  } catch {
    /* ignore */
  }
}

/* 每条模板的数据看板指标缓存：前端编辑提交不带 data_keys，适配层自动补回， */
/* 避免"编辑一次模板就丢失看板指标配置"。 */
const dataKeysCache = new Map<string, Array<{ key: string; label: string; unit: string }>>();

/* ---------- 基础请求 ---------- */
async function rawFetch<T = any>(method: string, url: string, body?: any): Promise<T> {
  const opt: RequestInit = { method, credentials: 'same-origin', headers: {} as Record<string, string> };
  if (body !== undefined) {
    (opt.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  if (method !== 'GET') {
    (opt.headers as Record<string, string>)['X-CSRF-Token'] = getCsrfToken();
  }
  const res = await fetch(url, opt);
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (res.status === 401) {
    clearCsrf();
    throw new Error('UNAUTHORIZED');
  }
  if (!res.ok || (data && data.ok === false)) {
    const msg = (data && (data.message || (data.error && data.error.message))) || `HTTP_${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

/* ---------- 字段映射：后端 → 前端 ---------- */
function roleToMock(r: any): RoleItem {
  return {
    role_id: r.role_id,
    role_name: r.name,
    description: r.note || '',
    member_count: 0,
    active_count: 0,
    activated_count: 0,
    created_at: r.created_at || '',
    /* 2026-11-05：创建人字段链路补齐 —— 后端早已返回，此前白名单映射把它丢了，
       导致超管视角「创建人」标签永不显示（组件侧条件恒 false）。 */
    owner_id: r.owner_id,
    owner_username: r.owner_username || '',
    owner_display_name: r.owner_display_name || '',
  };
}

async function fillRoleCounts(items: RoleItem[], platform: string): Promise<RoleItem[]> {
  const filled = await Promise.all(
    items.map(async (role) => {
      try {
        const res = await rawFetch<{ items?: any[] }>(
          'GET',
          `/api/admin/roles/${encodeURIComponent(role.role_id)}/members?size=200${platform ? `&platform=${encodeURIComponent(platform)}` : ''}`,
        );
        const members = res.items || [];
        role.member_count = members.length;
        role.active_count = members.filter((m) => m.enabled !== false).length;
        role.activated_count = members.filter((m) => m.activated).length;
      } catch {
        /* 计数失败不阻塞列表展示 */
      }
      return role;
    }),
  );
  return filled;
}

function memberToMock(m: any): RoleMemberItem {
  return {
    phone: m.phone,
    name: m.name,
    dept: m.dept || '',
    status: m.enabled !== false ? 'active' : 'disabled',
    activation: m.activated ? 'activated' : 'pending',
    activations: m.activations,
    usable: m.usable,
    external_id: m.external_id,
    activated_at: m.activated_at,
    created_at: m.created_at,
    /* 2026-11-10：创建者判定（姓名/部门权限矩阵：超管或创建者可改） */
    owner_id: m.owner_id,
  } as RoleMemberItem;
}

function tplToMock(t: any): TemplateItem {
  dataKeysCache.set(t.template_id, t.data_keys || []);
  const spec: any = {};
  if ((t.kind || '') === 'notice') {
    /* 2026-10-28 修复：以前这里凭空造一条「数据看板.png + 当日数据看板（到点自动生成并推送）」，
       既不是真实上传的文件、也没有任何链接，纯假数据。现在只按真实配置还原：
       文本项来自 spec_json（没有就退回 text_tpl），图片项只在真的上传过 attach_image_path 时才出现。 */
    const savedItems = Array.isArray(t.spec_json?.notice_items) ? t.spec_json.notice_items : [];
    const textItems = savedItems.filter((it: any) => it && it.format === 'text');
    spec.notice_items = textItems.length
      ? textItems.map((it: any) => ({ id: it.id || 'n_text', format: 'text', text_content: it.text_content || '' }))
      : [{ id: 'n_text', format: 'text', text_content: t.text_tpl || '' }];
    /* 2026-10-29：file 项同样要从 spec_json 原样恢复 —— file_name 是上传时的**原始文件名**、
       file_caption 是管理员填的描述。没填就留空，绝不再造「数据看板.png / 当日数据看板（…）」。
       只有「存了 attach_image_path 但 spec 里没有 file 项」的老数据才兜底重建。 */
    const fileItems = savedItems.filter((it: any) => it && it.format === 'file');
    if (fileItems.length) {
      fileItems.forEach((it: any, idx: number) => {
        spec.notice_items.push({
          id: it.id || `n_file_${idx + 1}`,
          format: 'file',
          file_name: it.file_name || (it.image_path ? String(it.image_path).split('/').pop() || '' : ''),
          file_caption: it.file_caption || '',
          file_size: it.file_size,
          image_path: it.image_path || undefined,
        });
      });
    } else if (t.attach_image_path) {
      spec.notice_items.push({
        id: 'n_file',
        format: 'file',
        file_name: String(t.attach_image_path).split('/').pop() || '',
        file_caption: '',
        image_path: t.attach_image_path,
      });
    }
  } else {
    /* 2026-10-28 修复：优先从 spec_json 恢复完整前端配置，fallback schema_json */
    if (t.spec_json && typeof t.spec_json === 'object') {
      Object.assign(spec, t.spec_json);
    } else {
      spec.schema_json = t.schema_json;
    }
  }
  return {
    id: t.template_id as unknown as number,
    name: t.name,
    kind: t.kind === 'form' ? 'upload' : t.kind || 'notice',
    period: t.period || 'day',
    role_id: (t.role_ids || [])[0] || '',
    role_ids: (t.role_ids || []).map(String),
    /* 2026-10-28 修复：form 类模板后端存的是 remind_at（非 notice_times），双向兼容 */
    remind_at: (t.notice_times && t.notice_times[0]) || t.remind_at || '',
    deadline: t.deadline || '',
    /* 2026-11-12 任务E1 截止三件套（用户实锤修复：此前只回 deadline，
       deadline_type 永远 undefined → 列表徽章/编辑弹窗永远回落「今天」；
       老模板无 has_deadline 时按「有 deadline 时刻」推断） */
    has_deadline: t.has_deadline !== undefined ? !!t.has_deadline : !!t.deadline,
    deadline_type: (t.deadline_type || null) as TemplateItem['deadline_type'],
    deadline_time: t.deadline_time || null,
    /* 2026-11-12 晚 · 不定期任务（菜单自选）：此前同样漏映射，后台勾选保存/回显全部失效 */
    is_adhoc: !!t.is_adhoc,
    text_tpl: t.text_tpl || '',
    spec,
    enabled: t.enabled !== false,
    /* 2026-10-29 P1-5：审核开关与附图路径要随列表下发，
       否则编辑/快捷启停时前端拿不到真实值，回写就会把配置冲掉。 */
    need_review: !!t.need_review,
    attach_image_path: t.attach_image_path || null,
    /* 2026-11-02 修复：图片名称此前没随列表下发 —— 编辑弹窗回显空、快捷启停还会把它冲掉 */
    attach_image_name: t.attach_image_name || null,
    /* 2026-10-29 执行日：后端返回 list[int] 或 null；null/空 = 全周 */
    exec_weekdays: Array.isArray(t.exec_weekdays) && t.exec_weekdays.length
      ? t.exec_weekdays.map((n: unknown) => Number(n)).filter((n: number) => n >= 1 && n <= 7)
      : null,
    /* 2026-11-10 月度执行日：null/空 = 未启用月度模式（回落每周判定） */
    exec_dom_days: Array.isArray(t.exec_dom_days) && t.exec_dom_days.length
      ? t.exec_dom_days.map((n: unknown) => Number(n)).filter((n: number) => n >= 1 && n <= 31)
      : null,
    created_at: t.created_at || '',
    updated_at: t.updated_at,
    /* 2026-11-05：创建人字段链路补齐（同 roleToMock）—— 超管视角列表要显示创建人 */
    owner_id: t.owner_id,
    owner_username: t.owner_username || '',
    owner_display_name: t.owner_display_name || '',
  } as unknown as TemplateItem;
}

/**
 * 前端表单结构 → 后端 task_template 结构。
 *
 * ⚠️ partial=true（编辑 PUT）时**只提交调用方显式给出的字段**。原因：
 * 后端把 payload 里出现的 kind / period 视为「要改类型 / 改周期」，
 * 一旦与库里不一致就抛 KIND_IMMUTABLE / PERIOD_IMMUTABLE（400）。
 * 而编辑表单语义上根本不传 kind、period（界面上是「不可修改」的锁定项）。
 * 旧实现无条件补 kind='notice'、period='day'、notice_times=[remind_at]、attach_image=false，
 * 于是编辑任何上传类模板都会报「模板类型创建后不可修改」，
 * 还会顺手把「多提醒时刻 / 附件开关 / 数字指标」冲成默认值。
  * 2026-11-10 修复执行日保存不生效：partial 分支是白名单构造，此前漏了
  * exec_weekdays / exec_dom_days —— 组件提交了但字段被丢弃，后端按「部分更新
  * 保留原值」处理 → 保存「工作日」重开弹窗回到「每天」。
  */
function tplFromMock(t: any, partial = false) {
  t = t || {};
  let text = t.text_tpl || '';
  let attach = false;
  const hasNoticeItems = Boolean(t.spec && Array.isArray(t.spec.notice_items) && t.spec.notice_items.length);
  if (hasNoticeItems) {
    const texts = t.spec.notice_items.filter((x: any) => x.format === 'text').map((x: any) => x.text_content).filter(Boolean);
    if (texts.length) text = texts.join('\n');
    /* 2026-10-28：数据图片提醒改为「上传图片 → 到点直接推图」，正文里不再拼任何说明文字
       （原来会把「当日数据看板（到点自动生成并推送）」这类描述写进消息）。 */
    attach = t.spec.notice_items.some((x: any) => x.format === 'file' && !!x.image_path);
  }
  const roleIds = Array.isArray(t.role_ids) && t.role_ids.length ? t.role_ids : t.role_id ? [t.role_id] : [];
  const out: Record<string, unknown> = {};

  /* spec → spec_json / schema_json（仅上传类使用；通知类的 spec 只是 notice_items 的回显载体） */
  const applySpec = () => {
    if (t.spec === undefined) return;
    out.spec_json = t.spec || null;
    if (out.kind === 'notice') return;
    const specFields = t.spec && Array.isArray(t.spec.fields) ? t.spec.fields : [];
    if (specFields.length) {
      out.schema_json = specFields.map((f: any) => ({
        key: String(f.key || f.id || 'field').replace(/[^A-Za-z0-9_]/g, '_') || 'field',
        label: String(f.label || f.title || '填写项'),
        type: ['text', 'number', 'select'].includes(f.type) ? f.type : 'text',
        required: !!f.required,
        ...(f.unit ? { unit: f.unit } : {}),
        ...(f.options ? { options: f.options } : {}),
      }));
    }
    /* 2026-10-28 用户指令：不再自动注入默认字段——未配置字段时后端校验拦截，前端提示管理员必须填写 */
  };

  if (partial) {
    if (t.name !== undefined) out.name = t.name;
    if (roleIds.length) out.role_ids = roleIds;
    if (t.kind !== undefined && t.kind !== '') out.kind = t.kind === 'upload' || t.kind === 'form' ? 'form' : t.kind;
    if (t.period !== undefined && t.period !== '') out.period = t.period;
    if (t.remind_at !== undefined) out.remind_at = String(t.remind_at || '').trim();
    if (t.deadline !== undefined) out.deadline = String(t.deadline || '').trim();
    /* 2026-11-12 任务E1 截止三件套（用户实锤修复：partial 白名单此前漏了这三个字段，
       编辑保存被吞 → 后端收不到 deadline_type="month_end"，回落按 same_day 处理） */
    if (t.has_deadline !== undefined) out.has_deadline = !!t.has_deadline;
    if (t.deadline_type !== undefined) out.deadline_type = t.deadline_type || null;
    if (t.deadline_time !== undefined) out.deadline_time = t.deadline_time || null;
    /* 2026-11-12 晚 · 不定期任务（菜单自选）：同层补齐 */
    if (t.is_adhoc !== undefined) out.is_adhoc = !!t.is_adhoc;
    if (t.notice_times !== undefined) out.notice_times = t.notice_times;
    if (t.text_tpl !== undefined || hasNoticeItems) out.text_tpl = text;
    if (t.enabled !== undefined) out.enabled = t.enabled !== false;
    if (t.attach_image !== undefined) out.attach_image = !!t.attach_image;
    if (t.attach_image_path !== undefined) out.attach_image_path = t.attach_image_path || null;
    /* 2026-11-02 修复：编辑模板时图片名称此前被丢掉，导致直链文案与 H5 标题永远回退「数据图片」 */
    if (t.attach_image_name !== undefined) out.attach_image_name = t.attach_image_name || null;
    if (t.need_review !== undefined) out.need_review = !!t.need_review;
    if (t.data_keys !== undefined) out.data_keys = t.data_keys;
    /* 2026-11-10 执行日保存 bug：白名单补上这两个字段（只透传调用方显式给的值，
       与 partial 语义一致；编辑时组件总会显式带上） */
    if (t.exec_weekdays !== undefined) out.exec_weekdays = t.exec_weekdays;
    if (t.exec_dom_days !== undefined) out.exec_dom_days = t.exec_dom_days;
    applySpec();
    return out;
  }

  /* 新建：给出后端必填项的完整默认值 */
  const kind = t.kind === 'upload' || t.kind === 'form' ? 'form' : t.kind || 'notice';
  const isNotice = kind === 'notice';
  /* 2026-10-29 P1-7：多时点模板（notice_times 有多个）保存时不能被压成单时点 */
  const times = isNotice
    ? (Array.isArray(t.notice_times) && t.notice_times.length > 0
      ? t.notice_times.filter((x: string) => /^\d{1,2}:\d{2}$/.test(x || ''))
      : [t.remind_at].filter((x: string) => /^\d{1,2}:\d{2}$/.test(x || '')))
    : [];
  out.name = t.name;
  out.kind = kind;
  out.role_ids = roleIds;
  out.remind_at = isNotice ? '' : String(t.remind_at || '').trim();
  out.text_tpl = text;
  out.notice_times = times;
  out.deadline = isNotice ? '' : t.deadline || '';
  /* 2026-11-12 任务E1 截止三件套（用户实锤修复：新建分支此前同样只送 deadline） */
  out.has_deadline = isNotice ? false : t.has_deadline !== undefined ? !!t.has_deadline : !!t.deadline;
  out.deadline_type = isNotice || !out.has_deadline ? null : (t.deadline_type || 'same_day');
  out.deadline_time = isNotice || !out.has_deadline ? null : (t.deadline_time || t.deadline || null);
  /* 2026-11-12 晚 · 不定期任务（菜单自选） */
  out.is_adhoc = !isNotice && !!t.is_adhoc;
  out.period = t.period || 'day';
  out.attach_image = attach;
  out.attach_image_path = t.attach_image_path || null;
  out.attach_image_name = t.attach_image_name || null;
  out.need_review = t.need_review !== undefined ? !!t.need_review : false;
  out.data_keys = t.data_keys || [];
  out.enabled = t.enabled !== false;
  /* 2026-11-10 新建模板同样带上执行日（此前新建分支也漏）：每周七天全选存 null（=每天），
     每月模式存选中的几号 */
  out.exec_weekdays = Array.isArray(t.exec_weekdays) ? t.exec_weekdays : null;
  out.exec_dom_days = Array.isArray(t.exec_dom_days) && t.exec_dom_days.length ? t.exec_dom_days : null;
  applySpec();
  return out;
}

function remToMock(m: any): ReminderLogItem {
  return {
    id: m.id,
    instance_id: m.instance_id || 0,
    slot: m.kind === 'notice_img' ? '看板' : m.kind || '',
    retry_no: m.retry || 0,
    planned_at: m.planned_at || '',
    attempted_at: m.attempted_at || null,
    sent_at: m.sent_at || null,
    result: (m.result || 'skipped') as any,
    error: m.error || null,
    template_name: m.template_name || '',
    phone: m.phone || '',
    member_name: '',
  } as ReminderLogItem;
}

function auditToMock(a: any): AdminAuditItem {
  return {
    id: a.id,
    at: a.created_at,
    actor: a.username,
    action: a.action,
    target: `${a.target_type || ''}${a.target_type && a.target_id ? ':' : ''}${a.target_id || ''}`,
    detail: a.detail || '',
  } as AdminAuditItem;
}

function instToMock(i: any): TaskInstanceItem {
  return {
    ...i,
    member_name_snap: i.name_snap || i.member_name_snap || '',
  } as unknown as TaskInstanceItem;
}

function statsToMock(d: any): InstanceStats {
  const s = (d && d.stats) || d || {};
  const by: Record<string, number> = {
    pending: s.pending || 0,
    sent: s.sent || 0,
    submitted: s.submitted || 0,
    approved: s.approved || 0,
    rejected: s.rejected || 0,
    overdue: s.overdue || 0,
    expired: s.expired || 0,
  };
  const total = s.total || 0;
  const rate = (a: number, b: number) => (b > 0 ? Number(((a / b) * 100).toFixed(1)) : 0);
  return {
    ok: true,
    total,
    by_status: by as any,
    /* 通知类实例的终态是 sent，应和「审核通过」一起算作完成 */
    completion_rate: rate(by.submitted + by.approved + by.sent, total),
    on_time_rate: rate(by.approved + by.submitted + by.sent, total),
    overdue_rate: rate(by.overdue + by.expired, total),
  } as InstanceStats;
}

function healthToMock(d: any): AdminHealth {
  return {
    ok: d.status === 'ok',
    service: 'im-task-admin',
    channel: d.channel === 'ok' ? 'ready' : 'unconfigured',
    event_crypto: d.crypto === 'ok' ? 'ready' : 'plaintext',
    public_base_url: d.public_base_url || '',
    is_loopback_public_url: !!d.is_loopback_public_url,
  } as AdminHealth;
}

/* ---------- 已激活待归纳人员：2026-11-03 新激活模型下线（后端已返 410） ---------- */

/* ---------- 主入口 ---------- */
export async function realApi<T = any>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: any): Promise<T> {
  let rawPath = path;
  let queryText = '';
  const qi = path.indexOf('?');
  if (qi >= 0) {
    rawPath = path.slice(0, qi);
    queryText = path.slice(qi + 1);
  }
  const query = new URLSearchParams(queryText || '');

  /* 登录：映射响应字段并保存 CSRF */
  if (method === 'POST' && rawPath === '/api/admin/login') {
    const d = await rawFetch<any>('POST', '/api/admin/login', body);
    setCsrfToken(d.csrf_token || '');
    return {
      ok: true,
      username: d.username,
      must_change_password: !!d.must_change,
      csrf_token: d.csrf_token || '',
    } as unknown as T;
  }

  /* 未激活成员聚合：2026-11-03 新激活模型下线（后台先导入才能激活，无待归纳池） */
  if (rawPath === '/api/admin/unactivated-members' || rawPath === '/api/admin/pending-members') {
    throw new Error('待归纳机制已下线：请先在「角色与成员」中导入/添加成员，成员给机器人发消息后会收到登记链接完成激活');
  }

  /* 路径映射 */
  const qs = query.toString();
  let url = rawPath + (qs ? `?${qs}` : '');
  if (rawPath === '/api/admin/health') url = '/healthz';
  else if (rawPath === '/api/admin/logs/reminders') url = `/api/admin/reminder-logs${qs ? `?${qs}` : ''}`;
  else if (rawPath === '/api/admin/logs/audit') url = `/api/admin/audit-logs${qs ? `?${qs}` : ''}`;
  else if (rawPath === '/api/admin/instances/stats') url = `/api/admin/stats${qs ? `?${qs}` : ''}`;
  else if (rawPath === '/api/admin/username' || rawPath === '/api/admin/callback-url') {
    throw new Error('该功能暂未开放');
  } else if (/\/trigger$/.test(rawPath)) {
    throw new Error('在线触发暂未开放：请等待时点自动触发（或用「数据填报」后到点验收）');
  }

  /* 请求体映射（前端结构 → 后端结构） */
  let reqBody = body;
  if (method !== 'GET') {
    if (rawPath.endsWith('/members/import')) {
      /* csv_text → csv_rows 兼容转换（仅当调用方还在发 csv_text 时才做）
         2026-11-10 修复「批量导入失败」：组件层已改为 parseCsvRows 直发 csv_rows
         （11-09 wb 改动），但本分支原先无条件用 body.csv_text 重算 csv_rows ——
         csv_text 不存在时 String(undefined || '') = ''，把组件构造好的行**覆盖成空数组**。
         实测链路：组件正确发 rows → 此处覆盖为 [] → 后端 will_add=0 / invalid=[] 且
         前端读不到任何错误行 → 用户看到「导入了但什么都没发生」。 */
      reqBody = { ...(body || {}) };
      if (reqBody.csv_text !== undefined) {
        const text = String(reqBody.csv_text || '');
        delete reqBody.csv_text;
        const HEADER_WORDS = /^(手机号?|电话|号码|phone|mobile|tel|姓名|名字|name|部门|dept|department)$/i;
        reqBody.csv_rows = text
          .split(/\r?\n/)
          .map((l: string) => l.trim())
          .filter(Boolean)
          .map((line: string, idx: number) => {
            const parts = line.split(/[,，\t]/).map((c: string) => c.trim());
            return { row: idx + 1, phone: parts[0] || '', name: parts[1] || '', dept: parts[2] || '' };
          })
          /* 顺手跳过表头行，避免把“手机号,姓名,部门”当成一条错行 */
          .filter((r: { row: number; phone: string; name: string; dept: string }) => {
            const isHeader = HEADER_WORDS.test(r.phone) || HEADER_WORDS.test(r.name);
            return !isHeader && (r.phone || r.name);
          });
      }
      /* body 已带 csv_rows（组件新通道）→ 原样透传 */
      if (reqBody.dry_run === undefined) reqBody.dry_run = false;
    } else if (rawPath === '/api/admin/templates' || rawPath.startsWith('/api/admin/templates/')) {
      /* 编辑（PUT/DELETE 带模板 id）走 partial：只发显式字段，绝不臆造 kind/period，
         否则后端判「模板类型创建后不可修改」，且会把已有 data_keys 冲空。*/
      const mId = rawPath.match(/templates\/(.+)$/);
      const isEdit = Boolean(mId);
      reqBody = tplFromMock(body, isEdit);
      const cached = mId ? dataKeysCache.get(mId[1]) : null;
      if (isEdit) {
        if (cached && cached.length) reqBody.data_keys = cached;
      } else {
        reqBody.data_keys =
          cached && cached.length
            ? cached
            : reqBody.attach_image
              ? [{ key: 'kpi_1', label: '当日关键指标', unit: '' }]
              : [];
      }
    } else if (rawPath === '/api/admin/roles' || rawPath.startsWith('/api/admin/roles/')) {
      reqBody = { ...(body || {}) };
      if (reqBody.role_name !== undefined) {
        reqBody.name = reqBody.role_name;
        delete reqBody.role_name;
      }
      if (reqBody.description !== undefined) {
        reqBody.note = reqBody.description;
        delete reqBody.description;
      }
      if (reqBody.status !== undefined) {
        reqBody.enabled = reqBody.status === 'active';
        delete reqBody.status;
      }
      if (reqBody.activation !== undefined) {
        delete reqBody.activation;
      }
    }
  }

  const d = await rawFetch<any>(method, url, reqBody);

  /* 响应映射（后端结构 → 前端结构） */
  /* 2026-11-10 修复「批量导入失败」的第二处：导入接口旧映射把后端返回
     （will_add/invalid/blocked/global_reused_count/synced_global_rows）换成
     added/skipped/errors 旧字段 —— 而组件（11-09 新口径）读的全是后端原字段。
     后果：即使请求侧修好，组件仍读不到 invalid/blocked → 错误行永不显示。
     已核实全库无其它消费者依赖旧字段（added/skipped/errors 映射仅此处产出），
     故直接透传后端响应。 */
  if (rawPath.endsWith('/members/import')) {
    return { ok: true, ...d } as unknown as T;
  }
  if (rawPath === '/api/admin/me') {
    /* 会话恢复：boot 探测时从响应同步 csrf（修复旧 Cookie 直接进站后写操作 403） */
    if (d && d.csrf_token) setCsrfToken(d.csrf_token);
    /* 2026-11-04 多管理员分级：一并带回角色级别 / 显示名 / 授权平台
       2026-11-05：assumed = 当前会话是超管代登录出来的（前端显示代管徽章 + 切回入口） */
    return {
      ok: true,
      id: d.id,
      username: d.username,
      must_change: !!d.must_change,
      must_change_password: !!d.must_change,
      role_level: d.role_level === 'super_admin' ? 'super_admin' : 'sub_admin',
      display_name: d.display_name || d.username,
      allowed_platforms: Array.isArray(d.allowed_platforms) ? d.allowed_platforms : ['zdxlz', 'wecom'],
      is_super_admin: d.role_level === 'super_admin',
      assumed: !!d.assumed,
      csrf_token: getCsrfToken(),
    } as unknown as T;
  }
  if (rawPath === '/api/admin/health') return healthToMock(d) as unknown as T;
  if (rawPath === '/api/admin/roles' && method === 'GET') {
    const m = new URLSearchParams(queryText);
    const items = await fillRoleCounts((d.items || []).map(roleToMock), m.get('platform') || '');
    /* 2026-11-05：按人去重的在册人数由后端统一口径计算（一人多角色只算一次），
       替代前端把各角色人数加总的重复统计。 */
    return { ok: true, items, member_distinct_total: Number(d.member_distinct_total || 0) } as unknown as T;
  }
  if (rawPath.startsWith('/api/admin/roles/') && rawPath.endsWith('/members') && method === 'GET') {
    return { ok: true, items: (d.items || []).map(memberToMock) } as unknown as T;
  }
  if (rawPath === '/api/admin/templates' && method === 'GET') {
    return { ok: true, items: (d.items || []).map(tplToMock) } as unknown as T;
  }
  if (rawPath === '/api/admin/logs/reminders') {
    return { ok: true, total: d.total || 0, items: (d.items || []).map(remToMock) } as unknown as T;
  }
  if (rawPath === '/api/admin/logs/audit') {
    return { ok: true, total: d.total || 0, items: (d.items || []).map(auditToMock) } as unknown as T;
  }
  if (rawPath === '/api/admin/instances/stats') return statsToMock(d) as unknown as T;
  if (rawPath === '/api/admin/instances' && method === 'GET') {
    return {
      ok: true,
      total: d.total || 0,
      page: d.page || 1,
      size: d.size || 20,
      items: (d.items || []).map(instToMock),
    } as unknown as T;
  }
  if (/^\/api\/admin\/instances\/\d+$/.test(rawPath) && method === 'GET') {
    return {
      ok: true,
      instance: d.instance ? instToMock(d.instance) : instToMock(d),
      /* 2026-10-28：后端给的是归一化后的 submission（字段标签 + 附件清单），
         之前这里读 d.submission 但后端只返回 submissions 数组，详情页永远显示「尚未产生提交」。 */
      submission: d.submission || null,
      template: d.template || null,
      reminders: (d.reminders || d.items || []).map(remToMock),
    } as unknown as T;
  }
  return d as T;
}
