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
  return csrfToken;
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
    (opt.headers as Record<string, string>)['X-CSRF-Token'] = csrfToken;
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
  };
}

async function fillRoleCounts(items: RoleItem[]): Promise<RoleItem[]> {
  const filled = await Promise.all(
    items.map(async (role) => {
      try {
        const res = await rawFetch<{ items?: any[] }>(
          'GET',
          `/api/admin/roles/${encodeURIComponent(role.role_id)}/members?size=200`,
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
    usable: m.usable,
    external_id: m.external_id,
    activated_at: m.activated_at,
    created_at: m.created_at,
  } as RoleMemberItem;
}

function tplToMock(t: any): TemplateItem {
  dataKeysCache.set(t.template_id, t.data_keys || []);
  const spec: any = {};
  if ((t.kind || '') === 'notice') {
    spec.notice_items = [];
    spec.notice_items.push({ id: 'n_text', format: 'text', text_content: t.text_tpl || '' });
    if (t.attach_image) {
      spec.notice_items.push({
        id: 'n_file',
        format: 'file',
        file_name: '数据看板.png',
        file_caption: '当日数据看板（到点自动生成并推送）',
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
    text_tpl: t.text_tpl || '',
    spec,
    enabled: t.enabled !== false,
    created_at: t.created_at || '',
    updated_at: t.updated_at,
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
    if (t.notice_times !== undefined) out.notice_times = t.notice_times;
    if (t.text_tpl !== undefined || hasNoticeItems) out.text_tpl = text;
    if (t.enabled !== undefined) out.enabled = t.enabled !== false;
    if (t.attach_image !== undefined) out.attach_image = !!t.attach_image;
    if (t.attach_image_path !== undefined) out.attach_image_path = t.attach_image_path || null;
    if (t.data_keys !== undefined) out.data_keys = t.data_keys;
    applySpec();
    return out;
  }

  /* 新建：给出后端必填项的完整默认值 */
  const kind = t.kind === 'upload' || t.kind === 'form' ? 'form' : t.kind || 'notice';
  const isNotice = kind === 'notice';
  const times = isNotice ? [t.remind_at].filter((x: string) => /^\d{1,2}:\d{2}$/.test(x || '')) : [];
  out.name = t.name;
  out.kind = kind;
  out.role_ids = roleIds;
  out.remind_at = isNotice ? '' : String(t.remind_at || '').trim();
  out.text_tpl = text;
  out.notice_times = times;
  out.deadline = isNotice ? '' : t.deadline || '';
  out.period = t.period || 'day';
  out.attach_image = attach;
  out.attach_image_path = t.attach_image_path || null;
  out.data_keys = t.data_keys || [];
  out.enabled = t.enabled !== false;
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
    completion_rate: rate(by.submitted + by.approved, total),
    on_time_rate: rate(by.approved + by.submitted, total),
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

/* ---------- 已激活待归纳人员（2026-10-28 方案B：对接后端 pending_member 表） ---------- */
async function unactivatedMembers(): Promise<{ ok: boolean; items: any[] }> {
  const d = await rawFetch<{ items?: any[] }>('GET', '/api/admin/pending-members');
  return {
    ok: true,
    items: (d.items || []).map((m: any) => ({
      external_id: m.external_id,
      phone: m.phone || '',
      name: m.name || '',
      last_message: m.last_message || '',
      activation: 'pending' as const,
      created_at: m.activated_at || '',
    })),
  };
}

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

  /* 未激活成员聚合 */
  if (rawPath === '/api/admin/unactivated-members') {
    if (method === 'GET') return (await unactivatedMembers()) as unknown as T;
    throw new Error('未激活成员请在「角色与成员名册」中操作（添加成员后，让成员给机器人发手机号即完成激活）');
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
    if (rawPath === '/api/admin/templates' || rawPath.startsWith('/api/admin/templates/')) {
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
    } else if (rawPath.endsWith('/members/import')) {
      /* 前端 csv_text → 后端 csv_rows */
      reqBody = { ...(body || {}) };
      const text = String(reqBody.csv_text || '');
      delete reqBody.csv_text;
      reqBody.csv_rows = text
        .split(/\r?\n/)
        .map((l: string) => l.trim())
        .filter(Boolean)
        .map((line: string) => line.split(/[,，\t]/).map((c: string) => c.trim()));
      if (reqBody.dry_run === undefined) reqBody.dry_run = false;
    }
  }

  const d = await rawFetch<any>(method, url, reqBody);

  /* 响应映射（后端结构 → 前端结构） */
  if (rawPath === '/api/admin/me') {
    /* 会话恢复：boot 探测时从响应同步 csrf（修复旧 Cookie 直接进站后写操作 403） */
    if (d && d.csrf_token) setCsrfToken(d.csrf_token);
    return { ok: true, username: d.username, must_change_password: !!d.must_change, csrf_token: getCsrfToken() } as unknown as T;
  }
  if (rawPath === '/api/admin/health') return healthToMock(d) as unknown as T;
  if (rawPath === '/api/admin/roles' && method === 'GET') {
    const items = await fillRoleCounts((d.items || []).map(roleToMock));
    return { ok: true, items } as unknown as T;
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
      submission: d.submission || null,
      reminders: (d.reminders || d.items || []).map(remToMock),
    } as unknown as T;
  }
  return d as T;
}
