import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Building2, Check, ChevronDown, ChevronLeft, ChevronRight, Download, Plus, RefreshCw, Search, Users, X } from 'lucide-react';
import { plainError } from '../../lib/api';
import { ImageViewer } from '../../lib/ImageViewer';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { AdminScopeHeader } from './AdminScopeHeader';
import { DepartmentTree, type DeptTreeNode } from './DepartmentTree';
import {
  KpiCard, SubmissionPreview, fieldClass, makeTree, panelClass, parentPath,
  secondaryButton, usePanelQuery,
  type PeriodicKpis, type RowItem, type TreeNode,
} from './StatisticsView';

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

type Department = { dept_id: string; name: string; has_children?: boolean; member_count?: number };
type ScopeOptions = {
  roles: Array<{ role_id: string; name: string }>;
  grant: null | { enabled: number; scope_json: string };
};

/* ── 2026-11-24 授权部门树：复用「部门架构」同款视觉（> / v 线性折叠箭头 +
      蓝色楼宇图标 + 左侧缩进引导线 + 右对齐人数 + 复选框），替换旧「+」方框树。
      数据仍走 grant_options（懒加载下级），children 展开即拉取一次后缓存。 ───── */
function GrantScopeTree({ roots, selected, toggle, load }: {
  roots: Department[];
  selected: string[];
  toggle: (id: string) => void;
  load: (id: string) => Promise<Department[]>;
}) {
  const [childrenOf, setChildrenOf] = useState<Record<string, Department[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [failed, setFailed] = useState<Set<string>>(new Set());

  async function expand(node: Department) {
    const path = node.dept_id;
    const nextOpen = !expanded.has(path);
    setExpanded((current) => {
      const next = new Set(current);
      if (nextOpen) next.add(path);
      else next.delete(path);
      return next;
    });
    if (!nextOpen || childrenOf[path] || loading.has(path)) return;
    setLoading((current) => new Set(current).add(path));
    setFailed((current) => { const next = new Set(current); next.delete(path); return next; });
    try {
      const children = await load(path);
      setChildrenOf((current) => ({ ...current, [path]: children }));
    } catch {
      setFailed((current) => new Set(current).add(path));
    } finally {
      setLoading((current) => { const next = new Set(current); next.delete(path); return next; });
    }
  }

  const renderRow = (node: Department): React.ReactNode => {
    const hasChildren = node.has_children !== false;
    const isOpen = expanded.has(node.dept_id);
    const checked = selected.includes(node.dept_id);
    const busy = loading.has(node.dept_id);
    const rowBody = <>
      {hasChildren && <ChevronRight aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-slate-400 ${isOpen ? 'rotate-90' : ''}`} />}
      <Building2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-blue-500" />
      <span className="min-w-0 flex-1 break-words" title={node.dept_id}>{node.name}</span>
      {typeof node.member_count === 'number'
        && <span className="shrink-0 text-slate-500 tabular-nums">{node.member_count} 人</span>}
      <span className="grid size-4 shrink-0 place-items-center rounded border"
        aria-hidden="true"
        style={{ borderColor: checked ? '#2563eb' : '#cbd5e1', background: checked ? '#2563eb' : 'transparent' }}>
        {checked && <Check className="h-3 w-3 text-white" />}
      </span>
    </>;
    return (
      <li key={node.dept_id}>
        <div className="flex items-center gap-1">
          {hasChildren ? (
            <button type="button" aria-expanded={isOpen} aria-label={`${isOpen ? '收起' : '展开'}${node.name}`}
              onClick={() => void expand(node)} disabled={busy}
              className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs hover:bg-slate-50 cursor-pointer disabled:opacity-60">
              {rowBody}
            </button>
          ) : (
            <label className="flex min-w-0 flex-1 grow cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs hover:bg-slate-50">
              <input type="checkbox" className="sr-only" checked={checked} onChange={() => toggle(node.dept_id)} />
              {rowBody}
            </label>
          )}
          {hasChildren && (
            <label className="flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
              <input type="checkbox" aria-label={`勾选${node.name}（含全部下级）`} checked={checked} onChange={() => toggle(node.dept_id)} />
              含下级
            </label>
          )}
        </div>
        {isOpen && hasChildren && (
          <ul className="ml-4 border-l border-slate-100 pl-2">
            {failed.has(node.dept_id)
              ? <li className="py-1.5 text-xs text-rose-700">下级部门读取失败，收起后重试。</li>
              : busy && !childrenOf[node.dept_id]
                ? <li role="status" className="py-1.5 text-xs text-slate-500">正在读取下级部门…</li>
                : (childrenOf[node.dept_id] || []).length
                  ? (childrenOf[node.dept_id] || []).map(renderRow)
                  : <li className="py-1.5 text-xs text-slate-500">无下级部门</li>}
          </ul>
        )}
      </li>
    );
  };
  return <ul className="max-h-72 overflow-y-auto overflow-x-hidden py-1">{roots.map(renderRow)}</ul>;
}

export function PortalGrantDialog({ phone, platform, onClose, onSaved }: { phone: string; platform: string; onClose: () => void; onSaved?: () => void }) {
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
            <p className="mb-2 text-slate-500">首次授权默认选中当前部门。选中部门包含全部下级；<strong>不勾选任何部门/角色时自动授权该成员当前所在部门（含全部下级）</strong>。</p>
            {!!depts.length && <ul className="mb-2 flex flex-wrap gap-1">
              {depts.map(id => <li key={id}><button type="button" aria-label={`移除 ${departmentNames[id] || id}`}
                onClick={() => setDepts(current => current.filter(did => did !== id))}
                className="rounded bg-blue-50 px-2 py-1 text-blue-800">{departmentNames[id] || id} ×</button></li>)}
            </ul>}
            {!departments && !departmentError && <p role="status">正在读取部门架构…</p>}
            {departmentError && <div role="status" className="my-2 rounded bg-amber-50 p-2 text-amber-900">
              {departmentError} <button type="button" onClick={() => setRetry(v => v + 1)} className="underline">重试</button>
            </div>}
            {departments && <GrantScopeTree roots={departments} selected={depts}
              toggle={id => setDepts(current => toggleId(current, id))} load={load} />}
          </section>
          <section className="rounded-lg border border-slate-200 p-3">
            <h3 className="font-semibold">角色分组</h3>
            <div className="mt-2 max-h-72 overflow-auto">
              {options.roles.length ? options.roles.map(r => <label key={r.role_id} className="flex gap-2 py-2">
                <input type="checkbox" checked={roles.includes(r.role_id)} onChange={() => setRoles(current => toggleId(current, r.role_id))} />{r.name}</label>)
                : <p className="text-slate-500">暂无角色分组</p>}
            </div>
          </section>
        </fieldset>
      </>}
      {error && <p className="text-red-600 text-sm" role="alert">{error}</p>}
      <div className="flex justify-end gap-3 border-t border-slate-200 pt-3"><button onClick={onClose} disabled={saving}>取消</button><button className="px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-40" disabled={saving || !options} onClick={async () => {
        /* 2026-11-20：不再在前端拦「什么都没勾」，后端会自动回退到成员当前部门（含下级）。 */
        setSaving(true); setError('');
        try { await portalAdminRequest(path, 'PUT', { enabled, dept_ids: depts, role_ids: roles }); onSaved?.(); onClose(); }
        catch (e) { setError((e as Error).message); } finally { setSaving(false); }
      }}>{saving ? '保存中…' : '保存授权'}</button></div>
    </div>
  </div>;
}

/* ── 2026-11-20 只读监管面板重构：以「授权发布人（领导）」为核心筛选维度，
      选中任务后的 KPI / 部门下钻 / 成员明细 / 导出与「任务数据统计」同一套
      组件与口径（按整个归属周期按人去重，不展示批次）。全程只读。 ────────── */

interface PublisherRow {
  id: string;
  phone: string;
  name: string;
  dept_path: string;
  owner_id: number;
  task_count: number;
}

interface PortalTaskMeta {
  task_id: string;
  name: string;
  creator_phone: string;
  owner_id: number;
  platform?: string;
  kind: string;
  is_notice?: boolean;
  has_receipt?: boolean;
  period?: string;
  mode?: string;
  statistics_period: 'day' | 'week' | 'month';
  current_period_key: string;
  period_keys: string[];
  remind_at?: string | null;
  anchor_weekday?: number | null;
  anchor_dom?: number | null;
  exec_weekdays?: number[] | null;
  exec_dom_days?: number[] | null;
  has_deadline: boolean;
  deadline_type?: string | null;
  deadline_time?: string | null;
  created_at?: string;
}

interface PanelResultList {
  total: number;
  items: RowItem[];
}

interface PanelOverview {
  template: { template_id: string; name: string; kind?: string; is_notice?: boolean; has_receipt?: boolean; is_receipt?: boolean };
  dept_tree: TreeNode[];
  kpis: PeriodicKpis & {
    expected_replies?: number; replied?: number; pending_replies?: number; reply_rate?: number;
    expected_delivery?: number; delivered?: number; pending_delivery?: number; delivery_rate?: number;
  };
  dept_stats: Record<string, { covered: number; done: number }>;
}

const PAGE_SIZE = 50;
const monitorTabs = { all: '全部', done: '已完成', todo: '未完成' } as const;
type MonitorTab = keyof typeof monitorTabs;

const monitorPeriodLabel = (key: string) => (key === 'once' ? '一次性任务' : key);

function execCycleText(task: PortalTaskMeta): string {
  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  if (task.exec_dom_days?.length) return '每月' + task.exec_dom_days.join('、') + '号';
  if (task.period === 'week') return '每周' + weekdays[(task.anchor_weekday || 1) - 1];
  if (task.period === 'month') return '每月' + (task.anchor_dom || 1) + '号';
  if (task.exec_weekdays?.length && task.exec_weekdays.length < 7) {
    return '每周' + task.exec_weekdays.map((day) => weekdays[day - 1]).join('、');
  }
  return '每天';
}

export function PortalTasksView() {
  const [manageOpen, setManageOpen] = useState(false);
  return <>
    <PortalMonitorCatalog onManageGrants={() => setManageOpen(true)} />
    {manageOpen && <PortalGrantDrawer onClose={() => setManageOpen(false)} />}
  </>;
}

/* ── 2026-11-24 授权成员管理抽屉：已授权清单（搜索）+ 新增授权 + 修改范围 +
      取消授权（二次确认）。仅分级管理员可见入口；数据全部走本管理员归属接口。 ── */
type GrantedMember = {
  phone: string;
  name: string;
  dept_path: string;
  scope_dept_ids: string[];
  scope_role_ids: string[];
  scope_summary: string;
  role_names: string[];
  task_count: number;
  in_roster: boolean;
  deliverable: boolean;
};
type Candidate = { phone: string; name: string; dept_path: string };

function PortalGrantDrawer({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const { platform } = useAppStore();
  const [members, setMembers] = useState<GrantedMember[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [candidateQuery, setCandidateQuery] = useState('');
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [editPhone, setEditPhone] = useState<string | null>(null);
  const [cancelTarget, setCancelTarget] = useState<GrantedMember | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const query = search.trim();
  const load = async () => {
    try {
      const base = `portal-grants?platform=${encodeURIComponent(platform)}`
        + (query ? `&q=${encodeURIComponent(query)}` : '');
      const result = await portalAdminRequest(base) as { members: GrantedMember[] };
      setMembers(result.members);
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
  };
  useEffect(() => { void load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [platform, query]);

  const loadCandidates = async (keyword: string) => {
    setCandidateLoading(true);
    try {
      const result = await portalAdminRequest(
        `portal-grant-candidates?platform=${encodeURIComponent(platform)}`
        + (keyword ? `&q=${encodeURIComponent(keyword)}` : '')) as { members: Candidate[] };
      setCandidates(result.members);
    } catch (err) {
      toast.error((err as Error).message);
      setCandidates([]);
    } finally {
      setCandidateLoading(false);
    }
  };
  useEffect(() => { const timer = window.setTimeout(() => void loadCandidates(candidateQuery.trim()), 220);
    return () => window.clearTimeout(timer); }, [platform, candidateQuery]);

  const granted = new Set((members ?? []).map((row) => row.phone));

  async function confirmCancel() {
    if (!cancelTarget) return;
    setCancelling(true);
    try {
      await portalAdminRequest(
        `portal-grants/${encodeURIComponent(cancelTarget.phone)}?platform=${encodeURIComponent(platform)}`,
        'PUT', { enabled: false });
      toast.success(`已取消 ${cancelTarget.name} 的创建任务权限，历史任务统计仍完整保留`);
      setCancelTarget(null);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setCancelling(false);
    }
  }

  return <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/50 backdrop-blur-xs" role="dialog"
    aria-modal="true" aria-label="授权成员管理" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <section className="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
      <header className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
        <div className="flex items-center gap-2">
          <Users className="h-5 w-5 text-blue-600" />
          <h2 className="text-base font-bold text-slate-900">授权成员管理</h2>
          {members && <span className="text-xs text-slate-500">{members.length} 人已授权</span>}
        </div>
        <button type="button" aria-label="关闭授权成员管理" onClick={onClose}
          className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"><X className="h-5 w-5" /></button>
      </header>

      <div className="space-y-4 overflow-y-auto px-5 py-4">
        {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

        {/* 已授权成员清单 */}
        <section aria-labelledby="grant-list-title">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id="grant-list-title" className="text-sm font-semibold text-slate-900">已授权成员</h3>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input aria-label="搜索已授权成员" placeholder="搜索姓名或手机号" value={search}
                onChange={(e) => setSearch(e.target.value)} className={fieldClass + ' w-48 pl-8 pr-8'} />
              {search && <button type="button" aria-label="清空搜索" onClick={() => setSearch('')}
                className="absolute right-2 top-2 rounded p-0.5 text-slate-500 hover:bg-slate-100"><X className="h-3.5 w-3.5" /></button>}
            </div>
          </div>
          <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
            <table className="w-full table-fixed text-left text-xs">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="w-[42%] px-3 py-2.5 font-medium">成员</th>
                  <th className="px-3 py-2.5 font-medium">管辖范围</th>
                  <th className="w-28 px-3 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {members === null ? (
                  <tr><td colSpan={3} className="px-3 py-8 text-center text-slate-500">{error ? '加载失败' : '正在读取授权清单…'}</td></tr>
                ) : !members.length ? (
                  <tr><td colSpan={3} className="px-3 py-8 text-center text-slate-500">{query ? '没有匹配的已授权成员' : '还没有授权成员，从下方添加'}</td></tr>
                ) : members.map((row) => (
                  <tr key={row.phone} className="align-top hover:bg-slate-50/70">
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5 font-semibold text-slate-900">
                        <span className="min-w-0 truncate" title={row.name}>{row.name}</span>
                        {!row.deliverable && <span className="shrink-0 rounded border border-amber-200 bg-amber-50 px-1 py-0.5 text-[10px] font-semibold text-amber-700">不可投递</span>}
                      </div>
                      <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">{row.phone}</div>
                      <div className="mt-0.5 truncate text-[11px] text-slate-500" title={row.dept_path}>{row.dept_path || '部门未分配'}</div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="break-words text-slate-700" title={row.scope_dept_ids.join('、')}>
                        {row.scope_summary ? <span>部门：{row.scope_summary}</span> : <span className="text-slate-400">未勾选部门</span>}
                      </div>
                      {row.role_names.length > 0 && (
                        <div className="mt-0.5 break-words" title={row.role_names.join('、')}>
                          角色：{row.role_names.join('、')}
                        </div>
                      )}
                      <div className="mt-0.5 text-[11px] text-slate-500">已发布 {row.task_count} 个任务</div>
                    </td>
                    <td className="px-3 py-3 text-right">
                      <div className="flex flex-col items-end gap-1">
                        <button type="button" onClick={() => setEditPhone(row.phone)}
                          className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100">修改范围</button>
                        <button type="button" onClick={() => setCancelTarget(row)}
                          className="rounded-lg bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100">取消授权</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1.5 text-[11px] text-slate-500">取消授权后该成员将无法新建任务、不再定时推送；其历史任务与本面板统计完整保留。</p>
        </section>

        {/* 新增授权：搜索可授权成员 */}
        <section aria-labelledby="grant-add-title" className="rounded-xl border border-slate-200 p-4">
          <h3 id="grant-add-title" className="text-sm font-semibold text-slate-900">新增授权成员</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">仅列出您名下已启用且已激活可投递的成员；选中后配置管辖范围并保存开通。</p>
          <div className="relative mt-2">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input aria-label="搜索可授权成员" placeholder="搜索姓名或手机号" value={candidateQuery}
              onChange={(e) => setCandidateQuery(e.target.value)} className={fieldClass + ' pl-8'} />
          </div>
          <ul className="mt-2 max-h-52 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-100">
            {candidates === null || candidateLoading ? (
              <li role="status" className="px-3 py-6 text-center text-xs text-slate-500">正在搜索可授权成员…</li>
            ) : !candidates.length ? (
              <li className="px-3 py-6 text-center text-xs text-slate-500">
                {candidateQuery.trim() ? '没有匹配的可授权成员' : '您名下暂无已激活可投递成员'}
              </li>
            ) : candidates.map((row) => (
              <li key={row.phone} className="flex items-center gap-2 px-3 py-2.5 hover:bg-slate-50/70">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-semibold text-slate-900">{row.name}</span>
                    {granted.has(row.phone) && <span className="shrink-0 rounded border border-blue-200 bg-blue-50 px-1 py-0.5 text-[10px] font-semibold text-blue-700">已授权</span>}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] tabular-nums text-slate-500" title={row.dept_path}>
                    {row.phone} · {row.dept_path || '部门未分配'}
                  </div>
                </div>
                <button type="button" onClick={() => setEditPhone(row.phone)}
                  className="shrink-0 rounded-lg bg-blue-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700">
                  {granted.has(row.phone) ? '修改范围' : '配置授权'}
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-slate-500">
            <Plus className="h-3.5 w-3.5" />搜索并在结果里点「配置授权」即可开通
          </div>
        </section>
      </div>

      {editPhone && (
        <PortalGrantDialog key={platform + editPhone} phone={editPhone} platform={platform}
          onClose={() => setEditPhone(null)}
          onSaved={() => { void load(); void loadCandidates(candidateQuery.trim()); }} />
      )}

      {/* 取消授权二次确认 */}
      {cancelTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-xs"
          role="dialog" aria-modal="true" aria-labelledby="grant-cancel-title"
          onClick={(e) => { if (e.target === e.currentTarget) setCancelTarget(null); }}>
          <section className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
            <h3 id="grant-cancel-title" className="text-base font-bold text-slate-900">确认取消授权？</h3>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              取消后，<strong className="font-semibold">{cancelTarget.name}（{cancelTarget.phone}）</strong>
              将<strong className="font-semibold">无法再新建任务，已发布的周期任务不再定时推送</strong>；
              但其<strong className="font-semibold">历史已发布任务与统计数据仍完整保留</strong>，本面板可继续只读查看与导出。
            </p>
            <div className="mt-5 flex justify-end gap-2.5">
              <button type="button" onClick={() => setCancelTarget(null)} disabled={cancelling}
                className="rounded-xl px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100">再想想</button>
              <button type="button" onClick={() => void confirmCancel()} disabled={cancelling}
                className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-700 disabled:opacity-50">
                <X className="h-3.5 w-3.5" />{cancelling ? '取消中…' : '确认取消授权'}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  </div>;
}

function PortalMonitorCatalog({ onManageGrants }: { onManageGrants: () => void }) {
  const { platform, isSuperAdmin } = useAppStore();
  const [data, setData] = useState<{ publishers: PublisherRow[]; tasks: PortalTaskMeta[] } | null>(null);
  const [grantCount, setGrantCount] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [publisherId, setPublisherId] = useState('');
  const [taskId, setTaskId] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  /* 2026-11-24：页头按钮与 PortalTaskMonitor 的同步信号（正经 props/回调数据流）：
     refreshSignal → Catalog 列表 + Monitor 的 KPI 概览与成员明细一起重拉；
     exportSignal → Monitor 导出，exporting 由 Monitor 经回调上报，页头真实置灰。 */
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [exportSignal, setExportSignal] = useState(0);
  const [exporting, setExporting] = useState(false);

  /* 2026-11-24：彻底移除 30 秒自动刷新（头部提示与定时器），仅保留手动「刷新」。 */
  const load = async () => {
    setRefreshing(true);
    try {
      const result = await portalAdminRequest(`portal-tasks?platform=${platform}`) as {
        publishers?: PublisherRow[]; tasks?: PortalTaskMeta[]; grant_count?: number;
      };
      setData({ publishers: result.publishers || [], tasks: result.tasks || [] });
      setGrantCount(result.grant_count || 0);
      setError('');
      setLastUpdated(new Date());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };
  useEffect(() => { void load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [platform]);

  const publishers = data?.publishers ?? [];
  const tasks = useMemo(
    () => (data?.tasks ?? []).filter((item) => !publisherId || item.creator_phone === publisherId),
    [data, publisherId],
  );

  useEffect(() => {
    if (taskId && !tasks.some((item) => item.task_id === taskId)) setTaskId(tasks[0]?.task_id || '');
    else if (!taskId && tasks.length) setTaskId(tasks[0].task_id);
  }, [tasks, taskId]);

  const task = tasks.find((item) => item.task_id === taskId);
  const hasAnyTask = (data?.tasks?.length ?? 0) > 0;
  /* 2026-11-23：彻底没有任务时改渲染精致空状态卡片（三步引导 + 分级管理员配置入口）。 */
  const showEmpty = !loading && !error && !hasAnyTask;
  return <section className="space-y-4">
    <AdminScopeHeader title="授权成员发布任务"
      description={<>
        只读监管 · 按授权发布人筛选任务
        {lastUpdated && <span className="ml-2 whitespace-nowrap text-slate-500">更新于 {lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}</span>}
      </>}
      actions={<>
        {/* 2026-11-24：分级管理员专属授权管理入口（超管严格隐藏，纯只读）。 */}
        {!isSuperAdmin && (
          <button type="button" onClick={onManageGrants}
            title="新增、修改或取消成员的 H5 创建任务权限"
            className="inline-flex items-center gap-1.5 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-100">
            <Users className="h-3.5 w-3.5" />授权成员管理
            {grantCount > 0 && <span className="rounded-full bg-blue-600 px-1.5 py-0.5 text-[10px] font-bold text-white">{grantCount}</span>}
          </button>
        )}
        <button type="button" onClick={() => { void load(); setRefreshSignal((n) => n + 1); }} disabled={refreshing}
          title="刷新发布人任务列表与下方概览、成员明细"
          className={secondaryButton}>
          <RefreshCw className={'h-3.5 w-3.5 ' + (refreshing ? 'animate-spin' : '')} /> 刷新
        </button>
        <button type="button" onClick={() => setExportSignal((n) => n + 1)}
          disabled={!task || exporting}
          title="导出当前选中任务、归属周期和部门范围内的全量数据"
          className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
          <Download className="h-3.5 w-3.5" /> {exporting ? '导出中…' : '导出 Excel'}
        </button>
      </>} />
    {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}
    {showEmpty
      ? <PortalMonitorEmpty authorized={grantCount} isSuperAdmin={isSuperAdmin} onConfigure={onManageGrants} />
      : <div className={panelClass + ' grid items-start gap-3 p-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.35fr)]'}>
      <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
        发布人（授权领导）
        <select className={fieldClass} value={publisherId} onChange={(e) => setPublisherId(e.target.value)}>
          <option value="">全部授权成员（{publishers.length} 位 · {data?.tasks?.length ?? 0} 个任务）</option>
          {publishers.map((item) => (
            <option key={item.phone} value={item.phone}>
              {item.name} · {item.dept_path || '部门未分配'} · 已发布 {item.task_count} 个任务
            </option>
          ))}
        </select>
      </label>
      <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
        该领导发布的任务
        <select className={fieldClass} value={taskId} onChange={(e) => setTaskId(e.target.value)} disabled={!tasks.length}>
          {!tasks.length && <option value="">该发布人暂无任务</option>}
          {tasks.map((item) => {
            const creator = publishers.find((pub) => pub.phone === item.creator_phone);
            const kindTag = item.is_notice ? (item.has_receipt ? ' · 通知（回执）' : ' · 通知') : '';
            return <option key={item.task_id} value={item.task_id}>
              {item.name}{kindTag}{!publisherId && creator ? ' · ' + creator.name : ''}
            </option>;
          })}
        </select>
        {task && <span className="font-normal text-slate-500">
          {task.mode === 'once' ? '一次性推送' : <>执行周期 {execCycleText(task)}{task.remind_at ? ' · 执行时间 ' + task.remind_at : ''}</>}
        </span>}
      </label>
      {task ? <PortalTaskMonitor key={task.task_id} task={task}
        refreshSignal={refreshSignal} exportSignal={exportSignal} onExportingChange={setExporting} /> : (
        <div className="md:col-span-2">
          {!loading && !error && !!data?.tasks?.length && !tasks.length && (
            <div className={panelClass + ' px-5 py-10 text-center text-sm text-slate-500'} role="status">该发布人暂无任务</div>
          )}
          {loading && <div className={panelClass + ' px-5 py-10 text-center text-sm text-slate-500'} role="status">正在加载任务清单…</div>}
        </div>
      )}
    </div>}
  </section>;
}

/* 2026-11-23：空状态卡片——柔和蓝灰双色线性图标 + 主副标题 + 三步引导条；
   2026-11-24：分级管理员主操作改为就地把用户引导至「授权管理入口」（管理弹窗在父级打开）。 */
function PortalMonitorEmpty({ authorized, isSuperAdmin, onConfigure }: {
  authorized: number;
  isSuperAdmin: boolean;
  onConfigure: () => void;
}) {
  const steps = [
    { title: '授权领导', desc: '用右上角「授权成员管理」为指定成员开通 H5 建任务权限' },
    { title: '手机发布', desc: '授权领导在个人工作台下发上传或通知任务' },
    { title: '实时监管', desc: '此处按领导与部门实时查看统计并导出报表' },
  ];
  return <section className={panelClass + ' overflow-hidden'} aria-labelledby="portal-empty-title">
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="grid size-20 place-items-center rounded-2xl bg-gradient-to-br from-blue-50 via-slate-50 to-white ring-1 ring-blue-100">
        <svg viewBox="0 0 48 48" fill="none" aria-hidden="true" className="size-11">
          <path d="M15 9.5A3.5 3.5 0 0 1 18.5 6h11A3.5 3.5 0 0 1 33 9.5V35a3.5 3.5 0 0 1-3.5 3.5h-11A3.5 3.5 0 0 1 15 35V9.5Z"
            stroke="#1d4e89" strokeWidth="2" strokeLinejoin="round" />
          <path d="M21 6.5h6v5.5h-6z" stroke="#93b4d8" strokeWidth="2" strokeLinejoin="round" />
          <path d="M19.5 20h9M19.5 26h6" stroke="#93b4d8" strokeWidth="2" strokeLinecap="round" />
          <circle cx="33.5" cy="33.5" r="7" fill="#f6fafd" stroke="#1d4e89" strokeWidth="2" />
          <path d="m30.6 33.6 2 2 3.6-4" stroke="#1d4e89" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <h3 id="portal-empty-title" className="mt-5 text-lg font-bold text-slate-900">还没有授权成员发布任务</h3>
      <p className="mt-1.5 max-w-xl text-sm leading-6 text-slate-500">
        {isSuperAdmin
          ? '超管只读：授权由成员所属分级管理员完成，领导在手机工作台发布后，这里会自动出现可监管的统计与报表。'
          : '按三步开通：先给领导开放 H5 建任务权限，领导在手机工作台发布上传或通知任务，发布后这里实时汇总统计。'}
      </p>
      <ol className="mt-7 grid w-full max-w-3xl gap-3 text-left sm:grid-cols-3">
        {steps.map((step, index) => (
          <li key={step.title} className="rounded-xl border border-slate-100 bg-slate-50/70 px-4 py-3.5">
            <div className="flex items-center gap-2">
              <span className="grid size-6 place-items-center rounded-full bg-blue-600 text-[11px] font-bold text-white">{index + 1}</span>
              <strong className="text-sm text-slate-900">{step.title}</strong>
            </div>
            <p className="mt-1.5 text-xs leading-5 text-slate-500">{step.desc}</p>
          </li>
        ))}
      </ol>
      {!isSuperAdmin && (
        <button type="button" onClick={onConfigure}
          className="mt-7 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700">
          <Users className="size-4" />授权成员管理
          {authorized > 0 && <span className="rounded-full bg-white/20 px-2 py-0.5 text-xs font-medium">已授权 {authorized} 人</span>}
        </button>
      )}
    </div>
  </section>;
}

/* 2026-11-24：页头与明细区的刷新/导出全部走 props+回调的正经数据流
   （废弃 getElementById 隐藏按钮转发反模式）：
   · refreshSignal：页头「刷新」→ Catalog 自身列表 + 本组件 revision（KPI 概览与成员明细一起重拉）；
   · exportSignal：页头「导出 Excel」→ 触发本组件导出逻辑；
   · onExportingChange：导出中状态回传页头，按钮真实置灰并显示「导出中…」。 */
function PortalTaskMonitor({ task, refreshSignal, exportSignal, onExportingChange }: {
  task: PortalTaskMeta;
  refreshSignal: number;
  exportSignal: number;
  onExportingChange: (busy: boolean) => void;
}) {
  const toast = useToast();
  const [chosenPeriod, setChosenPeriod] = useState<string>();
  const [deptPath, setDeptPath] = useState('');
  const [deptOpen, setDeptOpen] = useState(false);
  const [status, setStatus] = useState<MonitorTab>('all');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState({ query: '', page: 1 });
  const [revision, setRevision] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  /* 2026-11-20 需求 C：附件图片点开走原生全屏看图层（缩放/拖拽/Esc） */
  const [viewerSource, setViewerSource] = useState<{ url: string; alt?: string } | null>(null);

  /* 归属周期：一次性任务固定 once；周期任务 = 当前归属周期 + 全部历史周期（无批次） */
  const periodOptions = useMemo(() => {
    const keys = new Set<string>([task.current_period_key, ...(task.period_keys || [])].filter(Boolean));
    return [...keys].sort().reverse();
  }, [task]);
  const isOnce = task.current_period_key === 'once' || task.mode === 'once';
  const periodKey = isOnce ? 'once'
    : (chosenPeriod && periodOptions.includes(chosenPeriod) ? chosenPeriod : task.current_period_key);

  const query = search.trim();
  const searching = query !== filter.query;
  useEffect(() => {
    if (!searching) return;
    if (!query) { setFilter({ query: '', page: 1 }); return; }
    const timeout = window.setTimeout(() => setFilter({ query, page: 1 }), 180);
    return () => window.clearTimeout(timeout);
  }, [query, searching]);

  const base = `/api/admin/portal-tasks/${task.task_id}`;
  const scope = new URLSearchParams({ period_key: periodKey, dept_path: deptPath });
  const overview = usePanelQuery<PanelOverview>(`${base}/overview?` + scope, revision);
  const listParams = new URLSearchParams(scope);
  listParams.set('status_group', status);
  listParams.set('q', filter.query);
  listParams.set('page', String(filter.page));
  listParams.set('size', String(PAGE_SIZE));
  const roster = usePanelQuery<PanelResultList>(`${base}/instances?` + listParams, revision);
  const { data: overviewData } = overview;
  const { data: list } = roster;
  /* 2026-11-23：通知任务口径——回执通知「已回复/待回复」，纯广播通知「已送达/未送达」。 */
  const monitorMeta = overviewData?.template;
  const hasReceipt = Boolean(monitorMeta?.has_receipt);
  const isNotice = Boolean(monitorMeta?.is_notice || monitorMeta?.is_receipt || hasReceipt);
  const tabLabels: Record<MonitorTab, string> = hasReceipt
    ? { all: '全部', done: '已回复', todo: '待回复' }
    : isNotice ? { all: '全部', done: '已送达', todo: '未送达' } : monitorTabs;
  const timeHeader = isNotice ? (hasReceipt ? '回复时间' : '送达时间') : '提交时间';
  const contentHeader = isNotice ? (hasReceipt ? '回复内容' : '送达情况') : '上报内容';
  const totalPages = Math.max(1, Math.ceil((list?.total ?? 0) / PAGE_SIZE));
  const page = filter.page;
  const refreshing = overview.loading || roster.loading;
  const error = overview.error || roster.error;
  const deptNodes = overviewData?.dept_tree;
  const tree = useMemo(() => makeTree(deptNodes ?? []), [deptNodes]);
  const deptName = deptPath.split('/').pop() || '全部覆盖部门';
  const deptChildren = overviewData
    ? (deptNodes ?? []).filter((node) => parentPath(node.path) === deptPath).map((node) => {
        const counts = overviewData.dept_stats[node.path] ?? { covered: node.count, done: 0 };
        return { ...node, ...counts, rate: counts.covered ? 100 * counts.done / counts.covered : 0 };
      }).sort((a, b) => b.rate - a.rate || b.done - a.done || a.name.localeCompare(b.name, 'zh-CN'))
    : [];

  /* 2026-11-24：移除 30 秒轮询定时器，仅保留页头手动「刷新」。 */

  useEffect(() => {
    if (list && page > totalPages) setFilter((current) => ({ ...current, page: totalPages }));
  }, [list, page, totalPages]);

  useEffect(() => {
    if (deptPath && deptNodes && !deptNodes.some((node) => node.path === deptPath)) {
      setDeptPath('');
      setFilter((current) => ({ ...current, page: 1 }));
    }
  }, [deptPath, deptNodes]);

  useEffect(() => {
    if (!refreshing && !error && overviewData && list) setLastUpdated(new Date());
  }, [refreshing, error, overviewData, list]);

  function selectDept(path: string) {
    setDeptPath(path);
    setDeptOpen(false);
    setFilter((current) => ({ ...current, page: 1 }));
  }

  /* 2026-11-24：页头驱动的刷新/导出信号（跳过首次挂载，避免与 usePanelQuery 的
     初次自动拉取重复请求）。exportXlsx 为函数声明，存在提升，可在此前置引用。 */
  const firstRefreshSignal = useRef(true);
  useEffect(() => {
    if (firstRefreshSignal.current) { firstRefreshSignal.current = false; return; }
    setRevision((n) => n + 1);
  }, [refreshSignal]);
  const firstExportSignal = useRef(true);
  useEffect(() => {
    if (firstExportSignal.current) { firstExportSignal.current = false; return; }
    void exportXlsx();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exportSignal]);

  const exportBusy = useRef(false);
  async function exportXlsx() {
    if (exportBusy.current) return;
    exportBusy.current = true;
    onExportingChange(true);
    try {
      const response = await fetch(`${base}/export?` + scope, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(result?.message || ('导出失败（HTTP ' + response.status + '）'));
      }
      const file = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition') || '');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = file ? decodeURIComponent(file[1]) : '授权任务统计.xlsx';
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      toast.error(plainError(String(err)));
    } finally {
      exportBusy.current = false;
      onExportingChange(false);
    }
  }

  return <>
    {/* 归属周期（按周期按人去重，不展示批次下拉） */}
    <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
      归属周期
      {isOnce
        ? <span className={fieldClass}>一次性任务（全部数据）</span>
        : <select className={fieldClass} value={periodKey} onChange={(e) => {
            setChosenPeriod(e.target.value);
            setFilter((current) => ({ ...current, page: 1 }));
          }}>
            {periodOptions.map((key) => <option key={key} value={key}>{monitorPeriodLabel(key)}</option>)}
          </select>}
      {lastUpdated && <span className="font-normal text-slate-500">更新于 {lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}</span>}
    </label>

    {/* 覆盖部门（点击部门行下钻筛选下方明细） */}
    <div className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
      <span id="portal-dept-label">覆盖部门</span>
      <div className="flex min-w-0 gap-1.5">
        {deptPath && <button type="button" onClick={() => selectDept(parentPath(deptPath))}
          title="返回上一级部门" aria-label="返回上一级部门" className={secondaryButton + ' shrink-0 !px-2.5'}>
          <ChevronLeft className="h-4 w-4" />
        </button>}
        <button type="button" aria-labelledby="portal-dept-label portal-dept-value" aria-haspopup="dialog" onClick={() => setDeptOpen(true)}
          disabled={!tree.length} className={fieldClass + ' flex min-w-0 items-center gap-2 text-left disabled:opacity-50'} title={deptPath || deptName}>
          <Building2 className="h-4 w-4 shrink-0 text-blue-500" />
          <span id="portal-dept-value" className="min-w-0 flex-1 truncate">{deptName}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        </button>
      </div>
      {deptPath && <span className="truncate font-normal text-slate-500" title={deptPath}>{deptPath.split('/').join(' / ')}</span>}
    </div>

    {/* KPI：上传任务 = 应参与/已完成/未完成/完成率；回执通知 = 应回复/已回复/待回复/回复率；
        纯广播通知 = 应送达/已送达/未送达/送达率（2026-11-23 与领导 H5 面板同一口径） */}
    {overviewData && (() => {
      const k = overviewData.kpis;
      const meta = overviewData.template || {};
      const hasReceipt = Boolean(meta.has_receipt);
      const isNotice = Boolean(meta.is_notice || meta.is_receipt || hasReceipt);
      if (hasReceipt) {
        const expected = k.expected_replies ?? k.total ?? 0;
        const replied = k.replied ?? k.done ?? 0;
        const rate = k.reply_rate ?? k.rate ?? 0;
        return <div className="md:col-span-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
          <KpiCard label="应回复" value={expected} />
          <KpiCard label="已回复" value={replied} color="text-emerald-700" hint="链接确认 / 文本回复" />
          <KpiCard label="待回复" value={k.pending_replies ?? expected - replied} color="text-amber-700" hint="已送达未回执" />
          <KpiCard label="回复率" value={expected ? rate + '%' : '—'} color="text-blue-700"
            hint={<div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-blue-500" style={{ width: rate + '%' }} /></div>} />
        </div>;
      }
      if (isNotice) {
        const expected = k.expected_delivery ?? k.total ?? 0;
        const delivered = k.delivered ?? k.done ?? 0;
        const rate = k.delivery_rate ?? k.rate ?? 0;
        return <div className="md:col-span-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
          <KpiCard label="应送达" value={expected} />
          <KpiCard label="已送达" value={delivered} color="text-emerald-700" hint="正文 + 附件已下发" />
          <KpiCard label="未送达" value={k.pending_delivery ?? expected - delivered} color="text-amber-700" hint="含发送失败 / 仍在队列" />
          <KpiCard label="送达率" value={expected ? rate + '%' : '—'} color="text-blue-700"
            hint={<div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-blue-500" style={{ width: rate + '%' }} /></div>} />
        </div>;
      }
      return <div className="md:col-span-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard label="应参与" value={k.total} />
        <KpiCard label="已完成" value={k.done} color="text-emerald-700"
          hint="免审任务 · 提交即完成" />
        <KpiCard label="未完成" value={k.not_done} color="text-amber-700"
          hint={'待提交 ' + k.pending + ' · 逾期 ' + k.overdue + ' · 驳回 ' + k.rejected} />
        <KpiCard label="完成率" value={k.total ? k.rate + '%' : '—'} color="text-blue-700"
          hint={<div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-blue-500" style={{ width: k.rate + '%' }} /></div>} />
      </div>;
    })()}

    {/* 部门完成情况：按部门完成率进度条（点击下钻） */}
    {deptChildren.length > 0 && (
      <section className={panelClass + ' overflow-hidden md:col-span-4'} aria-labelledby="portal-dept-progress-title">
        <div className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-4">
          <h3 id="portal-dept-progress-title" className="text-base font-bold text-slate-900">部门完成情况</h3>
          <span className="text-xs text-slate-500">按完成率从高到低 · 点击部门查看明细</span>
        </div>
        <div className="max-h-[26rem] overflow-auto">
          <table className="w-full min-w-[560px] table-fixed text-sm">
            <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-600">
              <tr><th className="w-[26%] px-5 py-2.5">部门</th><th className="px-4 py-2.5">完成率</th>
                <th className="w-[19%] px-4 py-2.5 text-right">已提交 / 应提交</th>
                <th className="w-[15%] px-5 py-2.5 text-right">未提交</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {deptChildren.map((node) => (
                <tr key={node.path} className="hover:bg-blue-50/60">
                  <td className="px-5 py-3">
                    <button type="button" onClick={() => selectDept(node.path)} title={node.name}
                      className="group flex max-w-full items-center gap-1 text-left font-semibold text-slate-900 hover:text-blue-700">
                      <span className="min-w-0 truncate">{node.name}</span><ChevronRight className="h-4 w-4 shrink-0 text-blue-500" />
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="w-14 shrink-0 text-right text-base font-bold tabular-nums text-blue-800">{node.rate.toFixed(1)}%</span>
                      <div className="h-3 flex-1 overflow-hidden rounded bg-slate-100" aria-hidden="true">
                        <div className="h-full rounded bg-blue-600" style={{ width: node.rate + '%' }} />
                      </div>
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-500"><strong className="text-base text-slate-900">{node.done}</strong> / {node.covered}</td>
                  <td className="px-5 py-3 text-right font-semibold tabular-nums text-amber-800">{node.covered - node.done}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    )}

    {/* 成员提交明细：全部/已完成/未完成 + 实时搜索 + 提交内容与附件全屏
        （2026-11-24：刷新与导出全部由页头驱动（StatisticsView 同款位置），
          经 props/信号 +回调同步，不再有 DOM 转发锚点） */}
    <section className={panelClass + ' overflow-hidden md:col-span-4'} aria-busy={roster.loading || searching}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-900">成员提交明细</h3>
          {list && !searching && <span className="text-xs tabular-nums text-slate-500">{list.total} 人</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input aria-label="搜索姓名或手机号" placeholder="搜索姓名或手机号" value={search} onChange={(e) => setSearch(e.target.value)}
              className={fieldClass + ' w-44 pl-8 pr-8'} />
            {search && <button type="button" aria-label="清除搜索" onClick={() => setSearch('')}
              className="absolute right-2 top-2 rounded p-0.5 text-slate-500 hover:bg-slate-100"><X className="h-3.5 w-3.5" /></button>}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-slate-100 px-4 py-2.5">
        {(Object.keys(monitorTabs) as MonitorTab[]).map((tab) => (
          <button type="button" key={tab} aria-pressed={tab === status} onClick={() => { setStatus(tab); setFilter((current) => ({ ...current, page: 1 })); }}
            className={'rounded-lg px-2.5 py-1.5 text-xs font-medium ' + (tab === status ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50')}>
            {tabLabels[tab]}
          </button>
        ))}
      </div>
      {error && <div role="alert" className="border-b border-slate-100 bg-rose-50/60 px-4 py-2 text-xs text-rose-700">{error}</div>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] table-fixed text-left text-xs">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="w-28 px-4 py-3 font-medium">成员</th>
              <th className="w-[17%] px-3 py-3 font-medium">所属部门</th>
              <th className="w-24 px-3 py-3 font-medium">状态</th>
              <th className="w-36 px-3 py-3 font-medium">{timeHeader}</th>
              <th className="px-3 py-3 font-medium">{contentHeader}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {(!list?.items.length || searching) ? (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-500">
                {searching || roster.loading ? '正在加载明细…' : roster.error ? '明细加载失败' : filter.query ? '没有匹配的记录' : '当前范围暂无记录'}
              </td></tr>
            ) : list.items.map((row) => (
              <tr key={row.instance_id ?? row.id ?? row.phone} className="align-top hover:bg-slate-50/70">
                <td className="px-4 py-3">
                  <div className="break-words font-semibold text-slate-900">{row.name}</div>
                  <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">{row.phone}</div>
                </td>
                <td className="px-3 py-3 text-slate-700" title={row.dept_path}>
                  <span className="line-clamp-2 break-words">{row.dept_path.split('/').pop() || '—'}</span>
                </td>
                <td className="px-3 py-3">
                  <span className={'inline-flex whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold '
                    + (row.status === 'approved' ? 'border-emerald-200 text-emerald-700'
                      : row.status === 'rejected' ? 'border-rose-200 text-rose-700'
                      : row.status === 'overdue' || row.status === 'expired' ? 'border-amber-200 text-amber-700'
                      : 'border-slate-200 text-slate-600')}>
                    {isNotice
                      ? (row.status === 'approved' ? (hasReceipt ? '已回复' : '已送达') : (hasReceipt ? '待回复' : '未送达'))
                      : row.status === 'approved' ? '已完成' : row.status === 'rejected' ? '已驳回'
                      : row.status === 'overdue' || row.status === 'expired' ? '已逾期' : '未提交'}
                  </span>
                </td>
                <td className="px-3 py-3 tabular-nums text-slate-600">
                  {(row.submitted_at ?? (hasReceipt ? row.receipt_at : null))?.slice(5, 16) || '—'}
                  {row.late_submit && <div className="mt-0.5 text-amber-700">迟交</div>}
                </td>
                <td className="px-3 py-3">
                  {isNotice
                    ? <span className="text-slate-600">{hasReceipt ? (row.receipt_text || '—') : (row.status === 'approved' ? '正文与附件已下发' : '尚未送达')}</span>
                    : <SubmissionPreview content={row.content} onImageClick={setViewerSource} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/60 px-4 py-2 text-xs text-slate-600">
        <button type="button" onClick={() => setFilter((current) => ({ ...current, page: page - 1 }))}
          aria-label="上一页" disabled={page <= 1 || roster.loading || searching} className={secondaryButton + ' !p-1.5'}><ChevronLeft className="h-3.5 w-3.5" /></button>
        <span className="tabular-nums">第 {page} / {totalPages} 页</span>
        <button type="button" onClick={() => setFilter((current) => ({ ...current, page: page + 1 }))}
          aria-label="下一页" disabled={page >= totalPages || roster.loading || searching} className={secondaryButton + ' !p-1.5'}><ChevronRight className="h-3.5 w-3.5" /></button>
      </div>
    </section>

    {deptOpen && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-xs">
        <section role="dialog" aria-modal="true" aria-labelledby="portal-dept-tree-title" className={panelClass + ' w-full max-w-lg p-6 shadow-2xl'}>
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <h3 id="portal-dept-tree-title" className="font-bold text-slate-900">覆盖部门</h3>
            <button type="button" aria-label="关闭覆盖部门" className="rounded-lg p-1 text-slate-500 hover:bg-slate-100" onClick={() => setDeptOpen(false)}><X className="h-5 w-5" /></button>
          </div>
          <div className="mt-3 max-h-[60vh] overflow-y-auto">
            <button type="button" onClick={() => selectDept('')} aria-pressed={!deptPath}
              className={'mb-2 w-full rounded-lg px-2.5 py-2 text-left text-xs font-semibold ' + (!deptPath ? 'bg-blue-50 text-blue-700' : 'text-slate-700 hover:bg-slate-50')}>全部覆盖部门</button>
            <DepartmentTree nodes={tree} selected={deptPath} onSelect={selectDept} />
          </div>
        </section>
      </div>
    )}
    <ImageViewer source={viewerSource} onClose={() => setViewerSource(null)} />
  </>;
}
