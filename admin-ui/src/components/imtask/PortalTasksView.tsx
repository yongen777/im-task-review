import React, { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../../lib/store';

export async function portalAdminRequest(path: string, method = 'GET', body?: unknown) {
  const response = await fetch('/api/admin/' + path, {
    method, credentials: 'same-origin', cache: 'no-store',
    signal: AbortSignal.timeout(25000),
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': localStorage.getItem('imt_csrf') || '' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '请求失败');
  return data;
}

type Department = { dept_id: string; name: string; has_children?: boolean };
type ScopeOptions = {
  roles: Array<{ role_id: string; name: string }>;
  grant: null | { enabled: number; scope_json: string };
};

function DepartmentBranch({ department, selected, toggle, load }: {
  department: Department;
  selected: string[];
  toggle: (id: string) => void;
  load: (id: string) => Promise<Department[]>;
}) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<Department[] | null>(null);
  const [warning, setWarning] = useState('');
  const show = async () => {
    setOpen(!open);
    if (open || children) return;
    setWarning('');
    try { setChildren(await load(department.dept_id)); }
    catch { setWarning('部门读取失败，请收起后重试。'); }
  };
  return <li className="space-y-1">
    <div className="flex min-h-9 items-center gap-2">
      {department.has_children !== false ? <button type="button" aria-expanded={open}
        aria-label={`${open ? '收起' : '展开'}${department.name}`} onClick={() => void show()}
        className="size-7 shrink-0 rounded border border-slate-200 text-slate-600">{open ? '−' : '+'}</button>
        : <span className="w-7" />}
      <label className="flex grow items-center gap-2 py-1"><input type="checkbox" checked={selected.includes(department.dept_id)}
        onChange={() => toggle(department.dept_id)} />{department.name}</label>
    </div>
    {open && <ul className="space-y-1 border-l border-slate-200 pl-4">
      {warning ? <li className="text-rose-700">{warning}</li> : !children ? <li role="status">正在读取下级部门…</li>
        : children.length ? children.map(child => <DepartmentBranch key={child.dept_id} department={child}
          selected={selected} toggle={toggle} load={load} />) : <li className="text-slate-500">无下级部门</li>}
    </ul>}
  </li>;
}

export function PortalGrantDialog({ phone, platform, onClose }: { phone: string; platform: string; onClose: () => void }) {
  const [options, setOptions] = useState<ScopeOptions | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [depts, setDepts] = useState<string[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [departments, setDepartments] = useState<Department[] | null>(null);
  const [departmentError, setDepartmentError] = useState('');
  const [departmentNames, setDepartmentNames] = useState<Record<string, string>>({});
  const [retry, setRetry] = useState(0);
  const defaultApplied = useRef(false);
  const path = `portal-grants/${encodeURIComponent(phone)}?platform=${encodeURIComponent(platform)}`;
  useEffect(() => {
    let live = true;
    portalAdminRequest(path).then((d: ScopeOptions) => {
      if (!live) return;
      const scope = d.grant?.enabled ? JSON.parse(d.grant.scope_json) : { dept_ids: [], role_ids: [] };
      setOptions(d); setEnabled(!!d.grant?.enabled);
      setDepts(scope.dept_ids); setRoles(scope.role_ids);
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [path]);
  useEffect(() => {
    if (!enabled || !options) return;
    let live = true;
    setDepartmentError('');
    portalAdminRequest(`${path}&directory=true`).then(d => {
      if (!live) return;
      setDepartments(d.departments);
      setDepartmentNames(current => ({ ...current, ...Object.fromEntries(d.selected_departments.map((item: Department) => [item.dept_id, item.name])) }));
      if (!options.grant?.enabled && !defaultApplied.current && d.default_dept_ids.length) {
        defaultApplied.current = true;
        setDepts(current => [...new Set([...current, ...d.default_dept_ids])]);
      }
      setDepartmentError(d.directory_notice || '');
    }).catch(e => { if (live) setDepartmentError(e.name === 'TimeoutError' ? '组织架构读取超时，请重试。' : e.message); });
    return () => { live = false; };
  }, [enabled, options, path, retry]);
  const load = async (id: string) => {
    const result = await portalAdminRequest(`${path}&directory=true&parent_id=${encodeURIComponent(id)}`);
    setDepartmentNames(current => ({ ...current, ...Object.fromEntries(result.departments.map((item: Department) => [item.dept_id, item.name])) }));
    return result.departments;
  };
  const toggleId = (current: string[], id: string) => current.includes(id) ? current.filter(x => x !== id) : [...current, id];
  return <div className="fixed inset-0 bg-black/35 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true" aria-label="H5 创建任务授权">
    <div className="bg-white rounded-xl p-6 w-full max-w-2xl max-h-[85vh] overflow-auto space-y-4 text-slate-900">
      <h2 className="font-bold text-lg">H5 创建任务权限</h2>
      <label className="flex gap-2 items-center"><input type="checkbox" checked={enabled} disabled={!options || saving}
        onChange={e => setEnabled(e.target.checked)} />开放创建任务权限</label>
      {!options && !error && <p role="status" className="text-sm text-slate-600">正在读取现有授权…</p>}
      {options && enabled && <>
        <fieldset disabled={saving} className="grid gap-3 sm:grid-cols-2 text-sm">
          <section className="rounded-lg border border-slate-200 p-3">
            <h3 className="font-semibold">组织部门</h3>
            <p className="mb-2 text-slate-500">首次授权默认选中当前部门。选中部门包含全部下级。</p>
            {!!depts.length && <ul className="mb-2 flex flex-wrap gap-1">
              {depts.map(id => <li key={id}><button type="button" aria-label={`移除 ${departmentNames[id] || id}`}
                onClick={() => setDepts(current => current.filter(did => did !== id))}
                className="rounded bg-blue-50 px-2 py-1 text-blue-800">{departmentNames[id] || id} ×</button></li>)}
            </ul>}
            {!departments && !departmentError && <p role="status">正在读取组织架构…</p>}
            {departmentError && <div role="status" className="my-2 rounded bg-amber-50 p-2 text-amber-900">
              {departmentError} <button type="button" onClick={() => setRetry(v => v + 1)} className="underline">重试</button>
            </div>}
            <ul className="max-h-52 overflow-auto">
              {departments?.map(d => <DepartmentBranch key={d.dept_id} department={d} selected={depts}
                toggle={id => setDepts(current => toggleId(current, id))} load={load} />)}
            </ul>
          </section>
          <section className="rounded-lg border border-slate-200 p-3">
            <h3 className="font-semibold">角色分组</h3>
            <div className="mt-2 max-h-52 overflow-auto">
              {options.roles.length ? options.roles.map(r => <label key={r.role_id} className="flex gap-2 py-2">
                <input type="checkbox" checked={roles.includes(r.role_id)} onChange={() => setRoles(current => toggleId(current, r.role_id))} />{r.name}</label>)
                : <p className="text-slate-500">暂无角色分组</p>}
            </div>
          </section>
        </fieldset>
      </>}
      {error && <p className="text-red-600 text-sm" role="alert">{error}</p>}
      <div className="flex justify-end gap-3 border-t border-slate-200 pt-3"><button onClick={onClose} disabled={saving}>取消</button><button className="px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-40" disabled={saving || !options || (enabled && !depts.length && !roles.length)} onClick={async () => {
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
