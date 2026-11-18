import React, { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';

export async function portalAdminRequest(path: string, method = 'GET', body?: unknown) {
  const response = await fetch('/api/admin/' + path, {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': localStorage.getItem('imt_csrf') || '' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '请求失败');
  return data;
}

type ScopeOptions = {
  departments: Array<{ dept_id: string; name: string; parent_id: string }>;
  roles: Array<{ role_id: string; name: string }>;
  default_dept_ids: string[];
  grant: null | { enabled: number; scope_json: string };
};

export function PortalGrantDialog({ phone, platform, onClose }: { phone: string; platform: string; onClose: () => void }) {
  const [options, setOptions] = useState<ScopeOptions | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [depts, setDepts] = useState<string[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const path = `portal-grants/${encodeURIComponent(phone)}?platform=${encodeURIComponent(platform)}`;
  useEffect(() => {
    let live = true;
    portalAdminRequest(path).then((d: ScopeOptions) => {
      if (!live) return;
      setOptions(d); setEnabled(!!d.grant?.enabled);
      const scope = d.grant?.enabled ? JSON.parse(d.grant.scope_json) : { dept_ids: d.default_dept_ids, role_ids: [] };
      setDepts(scope.dept_ids); setRoles(scope.role_ids);
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [path]);
  const toggle = (id: string, current: string[], setter: (v: string[]) => void) => setter(current.includes(id) ? current.filter(x => x !== id) : [...current, id]);
  return <div className="fixed inset-0 bg-black/35 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true" aria-label="H5 创建任务授权">
    <div className="bg-white rounded-xl p-6 w-full max-w-xl max-h-[85vh] overflow-auto space-y-4">
      <h2 className="font-bold text-lg">H5 创建任务权限</h2>
      <p className="text-sm text-slate-500">默认管辖当前部门及全部下级部门；可追加最新组织部门或自己的角色分组。</p>
      <label className="flex gap-2 items-center"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />开放创建任务权限</label>
      {!options && !error && <p>正在读取最新组织架构…</p>}
      {options && enabled && <>
        <h3 className="font-semibold">组织部门（包含全部下级）</h3>
        {options.departments.map(d => <label key={d.dept_id} className="flex gap-2 text-sm py-1.5"><input type="checkbox" checked={depts.includes(d.dept_id)} onChange={() => toggle(d.dept_id, depts, setDepts)} />{d.parent_id ? '↳ ' : ''}{d.name}</label>)}
        <h3 className="font-semibold">角色分组</h3>
        {options.roles.map(r => <label key={r.role_id} className="flex gap-2 text-sm py-1.5"><input type="checkbox" checked={roles.includes(r.role_id)} onChange={() => toggle(r.role_id, roles, setRoles)} />{r.name}</label>)}
      </>}
      {error && <p className="text-red-600 text-sm" role="alert">{error}</p>}
      <div className="flex justify-end gap-3"><button onClick={onClose} disabled={saving}>取消</button><button className="px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-40" disabled={saving || (enabled && !options)} onClick={async () => {
        setSaving(true); setError('');
        try { await portalAdminRequest(path, 'PUT', { enabled, dept_ids: depts, role_ids: roles }); onClose(); }
        catch (e) { setError((e as Error).message); } finally { setSaving(false); }
      }}>{saving ? '保存中…' : '保存授权'}</button></div>
    </div>
  </div>;
}

type Submission = { note?: string; refs_json?: string; fields_json?: string };
type Member = { id: number; name_snap: string; phone: string; status: string; submission?: Submission };
type Period = { period_key: string; expected: number; submitted: number; pending: number; members: Member[] };
type Task = { task_id: string; name: string; creator_phone: string; periods: Period[] };

export function PortalTasksView() {
  const { platform } = useAppStore();
  const [tasks, setTasks] = useState<Task[]>([]), [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setTasks([]); setError('');
    const refresh = () => portalAdminRequest(`portal-tasks?platform=${platform}`).then(d => { if (live) { setTasks(d.items); setError(''); } }).catch(e => { if (live) setError(e.message); });
    void refresh(); const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
    return () => { live = false; clearInterval(timer); };
  }, [platform]);
  return <section className="space-y-4"><div><h1 className="text-xl font-bold">授权成员发布任务</h1><p className="text-sm text-slate-500 mt-2">只读监管 · 每 30 秒刷新收集统计</p></div>
    {error && <p className="text-red-600">{error}</p>}
    {!tasks.length && !error && <div className="bg-white rounded-xl border p-8 text-slate-500">当前没有授权成员发布的任务</div>}
    {tasks.map(t => <article key={t.task_id} className="bg-white border rounded-xl p-5 space-y-3"><h2 className="font-bold">{t.name}</h2><p className="text-xs text-slate-500">发布成员：{t.creator_phone.slice(0, 3)}****{t.creator_phone.slice(-4)}</p>
      {!t.periods.length && <p className="text-sm text-slate-500">等待首个执行周期</p>}
      {t.periods.map(p => <details key={p.period_key} open><summary className="cursor-pointer text-sm font-semibold">{p.period_key} · 应交 {p.expected} / 已交 {p.submitted} / 未交 {p.pending}</summary><div className="divide-y mt-3">{p.members.map(m => <details key={m.id} className="py-2 text-sm"><summary className="cursor-pointer">{m.name_snap} · {m.status === 'approved' ? '已完成' : '未提交'}</summary>{m.submission ? <div className="pl-4 py-2 space-y-2"><p className="whitespace-pre-wrap">{m.submission.note}</p>{(JSON.parse(m.submission.refs_json || '[]') as string[]).map(ref => <a key={ref} className="text-blue-600 mr-3" target="_blank" rel="noreferrer" href={`/api/admin/portal-tasks/${t.task_id}/attachments/${ref}`}>查看图片</a>)}</div> : <p className="text-slate-400 pl-4">暂无提交内容</p>}</details>)}</div></details>)}
    </article>)}
  </section>;
}
