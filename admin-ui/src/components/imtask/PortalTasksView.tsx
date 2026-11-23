import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Building2, ChevronDown, ChevronLeft, ChevronRight, Download, RefreshCw, Search, Users, X } from 'lucide-react';
import { plainError } from '../../lib/api';
import { ImageViewer } from '../../lib/ImageViewer';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { AdminScopeHeader } from './AdminScopeHeader';
import { DepartmentTree } from './DepartmentTree';
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
        onChange={() => toggle(department.dept_id)} />{department.name}
        {/* 2026-11-20：部门树按已激活可投递成员裁剪，这里标注该部门（含下级）可选人数 */}
        {typeof department.member_count === 'number' && <span className="text-slate-500">（{department.member_count} 人）</span>}
      </label>
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
            <p className="mb-2 text-slate-500">首次授权默认选中当前部门。选中部门包含全部下级；<strong>不勾选任何部门/角色时自动授权该成员当前所在部门（含全部下级）</strong>。</p>
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
      <div className="flex justify-end gap-3 border-t border-slate-200 pt-3"><button onClick={onClose} disabled={saving}>取消</button><button className="px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-40" disabled={saving || !options} onClick={async () => {
        /* 2026-11-20：不再在前端拦「什么都没勾」，后端会自动回退到成员当前部门（含下级）。 */
        setSaving(true); setError('');
        try { await portalAdminRequest(path, 'PUT', { enabled, dept_ids: depts, role_ids: roles }); onClose(); }
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
  return <PortalMonitorCatalog />;
}

function PortalMonitorCatalog() {
  const { platform, isSuperAdmin, setTabFocus } = useAppStore();
  const [data, setData] = useState<{ publishers: PublisherRow[]; tasks: PortalTaskMeta[] } | null>(null);
  const [grantCount, setGrantCount] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [publisherId, setPublisherId] = useState('');
  const [taskId, setTaskId] = useState('');

  useEffect(() => {
    let live = true;
    const load = () => portalAdminRequest(`portal-tasks?platform=${platform}`)
      .then((result: { publishers?: PublisherRow[]; tasks?: PortalTaskMeta[]; grant_count?: number }) => {
        if (!live) return;
        setData({ publishers: result.publishers || [], tasks: result.tasks || [] });
        setGrantCount(result.grant_count || 0);
        setError('');
      })
      .catch((err: Error) => { if (live) setError(err.message); })
      .finally(() => { if (live) setLoading(false); });
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 30000);
    return () => { live = false; window.clearInterval(timer); };
  }, [platform]);

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
      description={<>只读监管 · 按授权发布人筛选任务 · 每 30 秒自动刷新</>} />
    {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}
    {showEmpty
      ? <PortalMonitorEmpty authorized={grantCount} isSuperAdmin={isSuperAdmin} onConfigure={() => setTabFocus('roles')} />
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
      {task ? <PortalTaskMonitor key={task.task_id} task={task} /> : (
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
   分级管理员（sub_admin）底部给「前往成员名册配置授权」主操作，超管保持纯只读摘要。 */
function PortalMonitorEmpty({ authorized, isSuperAdmin, onConfigure }: {
  authorized: number;
  isSuperAdmin: boolean;
  onConfigure: () => void;
}) {
  const steps = [
    { title: '授权领导', desc: '在角色名册为指定成员开放 H5 建任务权限' },
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
          ? '超管只读：授权由成员所属分级管理员在名册内完成，领导在手机工作台发布后，这里会自动出现可监管的统计与报表。'
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
          <Users className="size-4" />前往成员名册配置授权
          {authorized > 0 && <span className="rounded-full bg-white/20 px-2 py-0.5 text-xs font-medium">已授权 {authorized} 人</span>}
        </button>
      )}
    </div>
  </section>;
}

function PortalTaskMonitor({ task }: { task: PortalTaskMeta }) {
  const toast = useToast();
  const [chosenPeriod, setChosenPeriod] = useState<string>();
  const [deptPath, setDeptPath] = useState('');
  const [deptOpen, setDeptOpen] = useState(false);
  const [status, setStatus] = useState<MonitorTab>('all');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState({ query: '', page: 1 });
  const [revision, setRevision] = useState(0);
  const [exporting, setExporting] = useState(false);
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

  /* 30 秒轮询刷新监管口径（只读，不写任何状态） */
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) setRevision((n) => n + 1); }, 30000);
    return () => window.clearInterval(timer);
  }, []);

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

  function refresh() {
    setRevision((n) => n + 1);
  }

  async function exportXlsx() {
    setExporting(true);
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
      setExporting(false);
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

    {/* 成员提交明细：全部/已完成/未完成 + 实时搜索 + 提交内容与附件全屏 */}
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
          <button type="button" onClick={refresh} disabled={refreshing} className={secondaryButton}>
            <RefreshCw className={'h-3.5 w-3.5 ' + (refreshing ? 'animate-spin' : '')} /> 刷新
          </button>
          <button type="button" onClick={() => void exportXlsx()} disabled={exporting || overview.loading || !!overview.error}
            title="导出当前任务、归属周期和部门范围内的全量数据（含部门汇总与成员明细）"
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
            <Download className="h-3.5 w-3.5" /> {exporting ? '导出中…' : '导出 Excel'}
          </button>
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
