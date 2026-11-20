import React, { useEffect, useMemo, useState } from 'react';
import {
  BarChart3, Building2, ChevronDown, ChevronLeft, ChevronRight, Copy, Download,
  Eye, Paperclip, RefreshCw, Search, X,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { copyText } from '../../lib/clipboard';
import { ImageViewer } from '../../lib/ImageViewer';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import type { TaskInstanceItem } from '../../lib/types';
import { AdminScopeHeader } from './AdminScopeHeader';
import { DepartmentTree, type DeptTreeNode } from './DepartmentTree';
import { getInstanceStatusMeta, InstanceDetailDialog } from './InstanceDetailDialog';

const PAGE_SIZE = 50;
const periodLabel = { day: '日期', week: '周', month: '月份' };
const statusLabels = { all: '全部', done: '已完成', pending: '待提交', overdue: '已逾期', rejected: '已驳回' };
const receiptLabels = { all: '应回复', done: '已回复', pending: '待回复' };
type PeriodMode = keyof typeof periodLabel;
type StatusTab = keyof typeof statusLabels;
type DetailTarget = { instanceId: number; submissionId?: number };

interface PanelTask {
  template_id: string;
  name: string;
  kind: TaskInstanceItem['kind'];
  period: PeriodMode;
  statistics_period: PeriodMode;
  current_period_key: string;
  anchor_weekday?: number | null;
  anchor_dom?: number | null;
  remind_at?: string | null;
  exec_weekdays?: number[] | null;
  exec_dom_days?: number[] | null;
  is_adhoc: boolean;
  is_manual?: boolean;
  is_receipt?: boolean;
  need_review: boolean;
}

interface TreeNode {
  path: string;
  name: string;
  depth: number;
  count: number;
}

interface PeriodicKpis {
  total: number;
  done: number;
  not_done: number;
  rate: number;
  pending_review: number;
  approved: number;
  pending: number;
  rejected: number;
  overdue: number;
}

interface AdhocKpis {
  day_count: number;
  day_unique: number;
  total_count: number;
  total_unique: number;
}

type Overview = { dept_tree: TreeNode[] } & (
  | { mode: 'periodic'; kpis: PeriodicKpis; dept_stats: Record<string, { covered: number; done: number }> }
  | { mode: 'adhoc'; kpis: AdhocKpis; trend: Array<{ date: string; count: number; unique: number }> }
);

interface SubmissionContent {
  fields: Array<{ key: string; label: string; value: string }>;
  note: string;
  attachments: Array<{ ref: string; file_name: string; is_image: boolean; url: string }>;
}

interface RowItem {
  id?: number;
  instance_id?: number;
  submission_id?: number;
  phone: string;
  name: string;
  dept_path: string;
  status?: TaskInstanceItem['status'];
  instance_status?: TaskInstanceItem['status'];
  submitted_at: string | null;
  receipt_at?: string | null;
  receipt_text?: string | null;
  receipt_mode?: 'link' | 'text';
  content: SubmissionContent | null;
  late_submit?: boolean;
}

interface ResultList {
  total: number;
  items: RowItem[];
}

function execCycleText(task: PanelTask): string {
  if (task.is_adhoc) return '不定期';
  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  if (task.exec_dom_days?.length) return '每月' + task.exec_dom_days.join('、') + '号';
  if (task.period === 'week') return '每周' + weekdays[(task.anchor_weekday || 1) - 1];
  if (task.period === 'month') return '每月' + (task.anchor_dom || 1) + '号';
  if (task.exec_weekdays?.length && task.exec_weekdays.length < 7) {
    return '每周' + task.exec_weekdays.map((day) => weekdays[day - 1]).join('、');
  }
  return '每天';
}

function fmtDate(date: Date): string {
  return date.toISOString().slice(5, 10).replace('-', '/');
}

function weekOptions(currentKey: string) {
  const [year, week] = currentKey.split('-W').map(Number);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(Date.UTC(year, 0, 4 - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7));
  return Array.from({ length: 53 }, (_, index) => {
    const mon = new Date(monday);
    mon.setUTCDate(mon.getUTCDate() - index * 7);
    const sun = new Date(mon);
    sun.setUTCDate(sun.getUTCDate() + 6);
    const thursday = new Date(mon);
    thursday.setUTCDate(thursday.getUTCDate() + 3);
    const isoYear = thursday.getUTCFullYear();
    const isoWeek = Math.ceil(((thursday.getTime() - Date.UTC(isoYear, 0, 1)) / 86400000 + 1) / 7);
    return {
      key: isoYear + '-W' + String(isoWeek).padStart(2, '0'),
      label: isoYear + '年第' + isoWeek + '周 (' + fmtDate(mon) + ' - ' + fmtDate(sun) + ')',
    };
  });
}

function parentPath(path: string): string {
  return path.slice(0, Math.max(0, path.lastIndexOf('/')));
}

function makeTree(nodes: TreeNode[]): DeptTreeNode[] {
  const branches = new Map<string, DeptTreeNode>();
  for (const node of nodes) branches.set(node.path, { ...node, children: [] });
  const roots: DeptTreeNode[] = [];
  for (const branch of branches.values()) {
    const parent = branches.get(parentPath(branch.path));
    (parent ? parent.children : roots).push(branch);
  }
  return roots;
}

function usePanelQuery<T>(url: string, revision: number) {
  const [result, setResult] = useState<{ url: string; revision: number; data?: T; error?: string } | null>(null);
  useEffect(() => {
    if (!url) {
      setResult(null);
      return;
    }
    let active = true;
    api.get<T>(url).then(
      (data) => { if (active) setResult({ url, revision, data }); },
      (error) => { if (active) setResult({ url, revision, error: plainError(error.message) }); },
    );
    return () => { active = false; };
  }, [url, revision]);
  const current = result?.url === url ? result : null;
  return { data: current?.data, error: current?.revision === revision ? current?.error : undefined, loading: current?.revision !== revision };
}

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500';
const secondaryButton = 'inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50';
const panelClass = 'rounded-2xl border border-slate-200/90 bg-white shadow-2xs';

function KpiCard({ label, value, hint, color = 'text-slate-900' }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; color?: string;
}) {
  return (
    <div className={panelClass + ' px-4 py-3.5'}>
      <div className="text-xs font-medium text-slate-600">{label}</div>
      <div className={'mt-1.5 text-2xl font-bold tracking-tight tabular-nums ' + color}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

function SubmissionPreview({ content, onImageClick }: {
  content: SubmissionContent | null; onImageClick?: (source: { url: string; alt?: string }) => void;
}) {
  if (!content || !(content.fields.length || content.note || content.attachments.length)) {
    return <span className="text-slate-400">—</span>;
  }
  return (
    <div className="space-y-1.5">
      {content.fields.length > 0 && (
        <div className="line-clamp-2 break-all" title={content.fields.map((f) => f.label + ' ' + f.value).join('\n')}>
          {content.fields.map((f, index) => (
            <React.Fragment key={f.key}>
              {index > 0 && <span className="mx-1 text-slate-300">·</span>}
              <span className="text-slate-500">{f.label} </span><span className="font-medium text-slate-800">{f.value}</span>
            </React.Fragment>
          ))}
        </div>
      )}
      {content.note && <p className="line-clamp-2 whitespace-pre-wrap break-words text-slate-700" title={content.note}>{content.note}</p>}
      {content.attachments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {content.attachments.slice(0, 4).map((a) => a.is_image ? (
            <button type="button" key={a.ref} title={a.file_name}
              onClick={() => onImageClick?.({ url: a.url, alt: a.file_name })}
              className="cursor-pointer">
              <img src={a.url} alt={a.file_name} loading="lazy" className="h-10 w-12 rounded border border-slate-200 object-cover hover:border-blue-500" />
            </button>
          ) : (
            <a key={a.ref} href={a.url + '?dl=1'} download={a.file_name} title={a.file_name}
              className="inline-flex max-w-full items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-blue-700 hover:bg-blue-50">
              <Paperclip className="h-3 w-3 shrink-0" /><span className="max-w-36 truncate">{a.file_name}</span>
            </a>
          ))}
          {content.attachments.length > 4 && <span className="text-slate-500">+{content.attachments.length - 4} 个附件</span>}
        </div>
      )}
    </div>
  );
}

export function StatisticsView() {
  const { me, platform, statsFocus, setStatsFocus } = useAppStore();
  const storageKey = `imtask:stats-task:${me.id ?? me.username}:${platform}`;
  return <StatisticsCatalog key={storageKey} storageKey={storageKey} platform={platform}
    focusId={statsFocus.id} focusVersion={statsFocus.seq} onTaskChange={setStatsFocus} />;
}

function StatisticsCatalog({ platform, focusId, focusVersion, storageKey, onTaskChange }: {
  platform: string; focusId: string; focusVersion: number; storageKey: string; onTaskChange: (id: string) => void;
}) {
  const [rememberedId] = useState(() => sessionStorage.getItem(storageKey));
  const [revision, setRevision] = useState(0);
  const catalog = usePanelQuery<{ items: PanelTask[] }>('/api/admin/stats-panel/tasks?platform=' + encodeURIComponent(platform), revision);
  const tasks = catalog.data?.items ?? [];
  const task = tasks.find((item) => item.template_id === focusId)
    ?? tasks.find((item) => item.template_id === rememberedId) ?? tasks[0];
  const taskId = task?.template_id;
  useEffect(() => {
    if (taskId) sessionStorage.setItem(storageKey, taskId);
  }, [storageKey, taskId]);
  if (!task) {
    return (
      <div className="space-y-4">
        <AdminScopeHeader title="任务数据统计" description="查看任务上报及通知回执" />
        <div className={panelClass + ' px-5 py-12 text-center text-sm text-slate-600'} role={catalog.error ? 'alert' : 'status'}>
          <BarChart3 className="mx-auto mb-3 h-8 w-8 text-slate-400" />
          {catalog.error || (catalog.loading ? '正在加载任务…' : '当前平台暂无可统计任务')}
          {catalog.error && <button type="button" className={secondaryButton + ' ml-3'} onClick={() => setRevision((n) => n + 1)}>重试</button>}
        </div>
      </div>
    );
  }
  return <StatisticsTask key={task.template_id + '/' + task.statistics_period + '/' + focusVersion}
    task={task} tasks={tasks} onTaskChange={onTaskChange}
    onRefreshTasks={() => setRevision((n) => n + 1)} />;
}

function StatisticsTask({ task, tasks, onTaskChange, onRefreshTasks }: {
  task: PanelTask; tasks: PanelTask[]; onTaskChange: (id: string) => void; onRefreshTasks: () => void;
}) {
  const isReceipt = task.is_receipt;
  const tabs = isReceipt ? receiptLabels : statusLabels;
  const toast = useToast();
  const [chosenPeriod, setChosenPeriod] = useState<string>();
  const canonicalKey = chosenPeriod ?? task.current_period_key;
  const [deptPath, setDeptPath] = useState('');
  const [deptOpen, setDeptOpen] = useState(false);
  const [status, setStatus] = useState<StatusTab>('all');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState({ query: '', page: 1 });
  const [revision, setRevision] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [copying, setCopying] = useState(false);
  const [detail, setDetail] = useState<DetailTarget | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  /* 2026-11-20 需求 C：缩略图点开走原生全屏看图层 */
  const [viewerSource, setViewerSource] = useState<{ url: string; alt?: string } | null>(null);
  const query = search.trim();
  const searching = query !== filter.query;

  useEffect(() => {
    if (!searching) return;
    if (!query) {
      setFilter({ query: '', page: 1 });
      return;
    }
    const timeout = window.setTimeout(() => setFilter({ query, page: 1 }), 180);
    return () => window.clearTimeout(timeout);
  }, [query, searching]);

  const base = new URLSearchParams({ template_id: task.template_id, dept_path: deptPath });
  /* 2026-11-20 切期改造：统计一律按「整个归属周期」聚合（服务端 period_key 已并入
     全部重推批次并按成员手机号去重），批次下拉整体移除。
     · 一次性任务固定 'once'（覆盖 once#… 全部批次）；
     · 周期任务用当前归属周期键（YYYY-MM-DD / YYYY-Www / YYYY-MM）。 */
  const periodKey = task.is_manual ? 'once' : canonicalKey;
  const scope = new URLSearchParams(base);
  scope.set('period_key', periodKey);
  const periodTitle = periodKey;
  const overview = usePanelQuery<Overview>('/api/admin/stats-panel/overview?' + scope, revision);
  const listParams = new URLSearchParams(task.is_adhoc ? base : scope);
  if (task.is_adhoc) listParams.set('date', periodKey);
  else listParams.set('status_group', status);
  listParams.set('q', filter.query);
  listParams.set('page', String(filter.page));
  listParams.set('size', String(PAGE_SIZE));
  const endpoint = task.is_adhoc ? 'submissions-day' : 'instances';
  const roster = usePanelQuery<ResultList>('/api/admin/stats-panel/' + endpoint + '?' + listParams, revision);
  const { data: overviewData } = overview;
  const { data: list } = roster;
  const totalPages = Math.max(1, Math.ceil((list?.total ?? 0) / PAGE_SIZE));
  const page = filter.page;
  const refreshing = overview.loading || roster.loading;
  const error = overview.error || roster.error;
  const deptNodes = overviewData?.dept_tree;
  const tree = useMemo(() => makeTree(deptNodes ?? []), [deptNodes]);
  const deptName = deptPath.split('/').pop() || '全部覆盖部门';
  const deptChildren = overviewData?.mode === 'periodic'
    ? (deptNodes ?? []).filter((node) => parentPath(node.path) === deptPath).map((node) => {
        const counts = overviewData.dept_stats[node.path] ?? { covered: node.count, done: 0 };
        return { ...node, ...counts, rate: counts.covered ? 100 * counts.done / counts.covered : 0 };
      }).sort((a, b) => b.rate - a.rate || b.done - a.done || a.name.localeCompare(b.name, 'zh-CN'))
    : [];

  useEffect(() => {
    if (list && page > totalPages) setFilter((current) => ({ ...current, page: totalPages }));
  }, [list, page, totalPages]);

  useEffect(() => {
    if (deptPath && deptNodes && !deptNodes.some((node) => node.path === deptPath)) {
      // 日期变化时部门覆盖范围也可能变化。
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

  function selectPeriod(key: string) {
    if (!key) return;
    setChosenPeriod(key);
    selectDept(deptPath);
  }

  function refresh() {
    onRefreshTasks();
    setRevision((n) => n + 1);
  }

  async function exportXlsx() {
    setExporting(true);
    try {
      const response = await fetch('/api/admin/stats-panel/export?' + scope, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(result?.error?.message || ('导出失败（HTTP ' + response.status + '）'));
      }
      const file = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition') || '');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = file ? decodeURIComponent(file[1]) : '统计数据.xlsx';
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

  async function copyTodoList() {
    setCopying(true);
    try {
      const params = new URLSearchParams(scope);
      params.set('status_group', 'todo');
      params.set('size', '200');
      const path = '/api/admin/stats-panel/instances?' + params;
      const first = await api.get<ResultList>(path + '&page=1');
      const rest = await Promise.all(Array.from({ length: Math.max(0, Math.ceil(first.total / 200) - 1) },
        (_, index) => api.get<ResultList>(path + '&page=' + (index + 2))));
      const rows = [first, ...rest].flatMap((result) => result.items);
      if (!rows.length) {
        toast.warning('当前部门范围内没有未交人员。');
        return;
      }
      const lines = rows.map((row) => [row.name, row.phone, row.dept_path].join('\t'));
      const title = task.is_manual ? task.name : task.name + ' · ' + periodTitle;
      if (await copyText(['【未交名单】' + title, ...lines].join('\n'))) {
        toast.success('已复制 ' + lines.length + ' 名未交人员。');
      } else toast.error('复制失败，请重试。');
    } catch (err) {
      toast.error(plainError(String(err)));
    } finally {
      setCopying(false);
    }
  }

  return (
    <div className="space-y-4">
      <AdminScopeHeader title="任务数据统计"
        description={<>
          {isReceipt ? '按周期和部门查看通知回执并导出明细。' : '按任务、周期和部门查看上报进度、核对提交内容并导出明细。'}
          {lastUpdated && <span className="ml-2 whitespace-nowrap text-slate-500">更新于 {lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}</span>}
        </>}
        actions={<>
          <button type="button" onClick={refresh} disabled={refreshing} className={secondaryButton}>
            <RefreshCw className={'h-3.5 w-3.5 ' + (refreshing ? 'animate-spin' : '')} /> 刷新
          </button>
          <button type="button" onClick={() => void exportXlsx()} disabled={exporting || overview.loading || !!overview.error}
            title="导出当前任务、统计周期和部门范围内的全量数据"
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
            <Download className="h-3.5 w-3.5" /> {exporting ? '导出中…' : '导出 Excel'}
          </button>
        </>} />

      <div className={panelClass + ' grid items-start gap-3 p-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.35fr)]'}>
        <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
          任务
          <select className={fieldClass} value={task.template_id} onChange={(e) => onTaskChange(e.target.value)}>
            {tasks.map((item) => <option key={item.template_id} value={item.template_id}>{item.name}</option>)}
          </select>
          <span className="font-normal text-slate-500">{task.is_manual ? '立即推送' : <>执行周期 {execCycleText(task)}{task.remind_at && !task.is_adhoc ? ' · 执行时间 ' + task.remind_at : ''}</>}</span>
        </label>
        <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
          {task.is_manual ? '推送批次' : periodLabel[task.statistics_period]}
          {/* 2026-11-20 切期改造：一次性任务整族统计（once + 全部重推批次按人去重），不再切批次 */}
          {task.is_manual ? <span className={fieldClass}>一次性推送（全部批次）</span> : task.statistics_period === 'week' ? (
            <select className={fieldClass} value={canonicalKey} onChange={(e) => selectPeriod(e.target.value)}>
              {weekOptions(task.current_period_key).map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          ) : (
            <input className={fieldClass} type={task.statistics_period === 'month' ? 'month' : 'date'}
              value={canonicalKey} onChange={(e) => selectPeriod(e.target.value)} />
          )}
        </label>
        <div className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">
          <span id="stats-dept-label">覆盖部门</span>
          <div className="flex min-w-0 gap-1.5">
            {deptPath && <button type="button" onClick={() => selectDept(parentPath(deptPath))}
              title="返回上一级部门" aria-label="返回上一级部门" className={secondaryButton + ' shrink-0'}>
              <ChevronLeft className="h-4 w-4" />
            </button>}
            <button type="button" aria-labelledby="stats-dept-label stats-dept-value" aria-haspopup="dialog" onClick={() => setDeptOpen(true)}
              disabled={!tree.length} className={fieldClass + ' flex min-w-0 items-center gap-2 text-left disabled:opacity-50'} title={deptPath || deptName}>
              <Building2 className="h-4 w-4 shrink-0 text-blue-500" />
              <span id="stats-dept-value" className="min-w-0 flex-1 truncate">{deptName}</span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0" />
            </button>
          </div>
          {deptPath && <span className="truncate font-normal text-slate-500" title={deptPath}>{deptPath.split('/').join(' / ')}</span>}
        </div>
      </div>

      {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}
      {overviewData?.mode === 'periodic' && (
        <>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <KpiCard label={isReceipt ? '应回复' : '应参与人数'} value={overviewData.kpis.total} />
            <KpiCard label={isReceipt ? '已回复' : '已完成人数'} value={overviewData.kpis.done} color="text-emerald-700"
              hint={!isReceipt && task.need_review ? '待审核 ' + overviewData.kpis.pending_review + ' · 审核通过 ' + overviewData.kpis.approved : undefined} />
            <KpiCard label={isReceipt ? '待回复' : '未完成人数'} value={overviewData.kpis.not_done} color="text-amber-700"
              hint={isReceipt ? undefined : '待提交 ' + overviewData.kpis.pending + ' · 逾期 ' + overviewData.kpis.overdue + ' · 驳回 ' + overviewData.kpis.rejected} />
            <KpiCard label={isReceipt ? '回复率' : '完成率'} value={overviewData.kpis.total ? overviewData.kpis.rate + '%' : '—'} color="text-blue-700"
              hint={<div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-blue-500" style={{ width: overviewData.kpis.rate + '%' }} /></div>} />
          </div>

          {deptChildren.length > 0 && (
            <section className={panelClass + ' overflow-hidden'} aria-labelledby="department-progress-title">
              <div className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-4">
                <h3 id="department-progress-title" className="text-base font-bold text-slate-900">{isReceipt ? '部门回复情况' : '部门完成情况'}</h3>
                <span className="text-xs text-slate-500">按{isReceipt ? '回复率' : '完成率'}从高到低 · 点击部门查看明细</span>
              </div>
              <div className="max-h-[26rem] overflow-auto">
                <table className="w-full min-w-[560px] table-fixed text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-600">
                    <tr><th className="w-[26%] px-5 py-2.5">部门</th><th className="px-4 py-2.5">{isReceipt ? '回复率' : '完成率'}</th>
                      <th className="w-[19%] px-4 py-2.5 text-right">{isReceipt ? '已回复 / 应回复' : '已提交 / 应提交'}</th>
                      <th className="w-[15%] px-5 py-2.5 text-right">{isReceipt ? '待回复' : '未提交'}</th></tr>
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
        </>
      )}

      {overviewData?.mode === 'adhoc' && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <KpiCard label="当日上报人次" value={overviewData.kpis.day_count} />
            <KpiCard label="当日上报人数" value={overviewData.kpis.day_unique} color="text-blue-700" />
            <KpiCard label="累计上报人次" value={overviewData.kpis.total_count} hint={'累计 ' + overviewData.kpis.total_unique + ' 人'} />
          </div>
          <section className={panelClass}>
            <h3 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">近 7 天上报</h3>
            <div className="flex items-end gap-2 p-3 text-xs sm:gap-4 sm:px-5">
              {overviewData.trend.map((day) => (
                <button type="button" key={day.date} aria-pressed={day.date === periodKey} onClick={() => selectPeriod(day.date)}
                  title={day.date + ' · ' + day.count + ' 人次 · ' + day.unique + ' 人'}
                  className="group flex min-w-0 flex-1 flex-col items-center gap-1.5 rounded-lg py-1 text-slate-600 hover:bg-slate-50">
                  <span className="tabular-nums">{day.count}</span>
                  <span className={'block w-2/3 max-w-16 rounded-t ' + (day.date === periodKey ? 'bg-blue-600' : 'bg-blue-200 group-hover:bg-blue-400')}
                    style={{ height: Math.max(2, 60 * day.count / Math.max(1, ...overviewData.trend.map((item) => item.count))) }} />
                  <span className="tabular-nums">{day.date.slice(5)}</span>
                </button>
              ))}
            </div>
          </section>
        </>
      )}

      <section className={panelClass + ' overflow-hidden'} aria-busy={roster.loading || searching}>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-900">{isReceipt ? '回执明细' : task.is_adhoc ? '上报明细' : '员工明细'}</h3>
            {list && !searching && <span className="text-xs tabular-nums text-slate-500">{list.total} {task.is_adhoc ? '条' : '人'}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input aria-label="搜索姓名或手机号" placeholder="搜索姓名或手机号" value={search} onChange={(e) => setSearch(e.target.value)}
                className={fieldClass + ' w-44 pl-8 pr-8'} />
              {search && <button type="button" aria-label="清除搜索" onClick={() => setSearch('')}
                className="absolute right-2 top-2 rounded p-0.5 text-slate-500 hover:bg-slate-100"><X className="h-3.5 w-3.5" /></button>}
            </div>
            {!task.is_adhoc && !isReceipt && (
              <button type="button" onClick={() => void copyTodoList()} className={secondaryButton} disabled={copying || !overviewData}>
                <Copy className="h-3.5 w-3.5" /> {copying ? '正在复制…' : '一键复制未交名单'}
              </button>
            )}
          </div>
        </div>
        {!task.is_adhoc && (
          <div className="flex flex-wrap gap-1.5 border-b border-slate-100 px-4 py-2.5">
            {(Object.keys(tabs) as (keyof typeof tabs)[]).map((tab) => (
              <button type="button" key={tab} aria-pressed={tab === status} onClick={() => { setStatus(tab); setFilter((current) => ({ ...current, page: 1 })); }}
                className={'rounded-lg px-2.5 py-1.5 text-xs font-medium ' + (tab === status ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50')}>
                {tabs[tab]}
              </button>
            ))}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] table-fixed text-left text-xs">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="w-28 px-4 py-3 font-medium">成员</th>
                <th className="w-[17%] px-3 py-3 font-medium">所属部门</th>
                <th className="w-24 px-3 py-3 font-medium">状态</th>
                <th className="w-36 px-3 py-3 font-medium">{isReceipt ? '回复时间' : '提交时间'}</th>
                <th className="px-3 py-3 font-medium">{isReceipt ? '回复内容 / 方式' : '上报内容'}</th>
                {!isReceipt && <th className="w-28 px-4 py-3 text-right font-medium">操作</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(!list?.items.length || searching) ? (
                <tr><td colSpan={isReceipt ? 5 : 6} className="px-4 py-10 text-center text-slate-500">
                  {searching || roster.loading ? '正在加载明细…' : roster.error ? '明细加载失败' : filter.query ? '没有匹配的记录' : '当前范围暂无记录'}
                </td></tr>
              ) : list.items.map((row) => {
                const instanceId = row.instance_id ?? row.id;
                const meta = isReceipt
                  ? (row.receipt_at ? { label: '已回复', className: 'border-emerald-200 text-emerald-700' }
                    : { label: '待回复', className: 'border-slate-200 text-slate-600' })
                  : getInstanceStatusMeta({
                  status: row.status ?? row.instance_status ?? 'pending',
                  kind: task.kind, need_review: task.need_review, submitted_at: row.submitted_at,
                });
                return (
                  <tr key={row.submission_id ?? instanceId} className="align-top hover:bg-slate-50/70">
                    <td className="px-4 py-3">
                      <div className="break-words font-semibold text-slate-900">{row.name}</div>
                      <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">{row.phone}</div>
                    </td>
                    <td className="px-3 py-3 text-slate-700" title={row.dept_path}>
                      <span className="line-clamp-2 break-words">{row.dept_path.split('/').pop() || '—'}</span>
                    </td>
                    <td className="px-3 py-3"><span className={'inline-flex whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ' + meta.className}>{meta.label}</span></td>
                    <td className="px-3 py-3 tabular-nums text-slate-600">
                      {(isReceipt ? row.receipt_at : row.submitted_at?.slice(5, task.is_adhoc ? 19 : 16)) || '—'}
                      {row.late_submit && <div className="mt-0.5 text-amber-700">迟交</div>}
                    </td>
                    <td className="px-3 py-3">{isReceipt ? <div>
                      <div className="whitespace-pre-wrap break-words">{row.receipt_text || '—'}</div>
                      <div className="mt-1 text-slate-500">{row.receipt_mode === 'link' ? 'H5 链接确认' : '文本回复确认'}</div>
                    </div>
                      : <SubmissionPreview content={row.content} onImageClick={setViewerSource} />}</td>
                    {!isReceipt && <td className="px-4 py-3 text-right">
                      <button type="button" disabled={instanceId == null} onClick={() => instanceId != null && setDetail({ instanceId, submissionId: row.submission_id })}
                        className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg bg-slate-100 px-2.5 py-1.5 font-semibold text-slate-700 hover:bg-blue-50 hover:text-blue-700 disabled:opacity-50">
                        <Eye className="h-3.5 w-3.5" /> 查看详情
                      </button>
                    </td>}
                  </tr>
                );
              })}
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
          <section role="dialog" aria-modal="true" aria-labelledby="stats-dept-title" className={panelClass + ' w-full max-w-lg p-6 shadow-2xl'}>
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 id="stats-dept-title" className="font-bold text-slate-900">覆盖部门</h3>
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
      {detail && <InstanceDetailDialog instanceId={detail.instanceId} submissionId={detail.submissionId}
        onClose={() => setDetail(null)} onChanged={() => setRevision((n) => n + 1)} />}
      <ImageViewer source={viewerSource} onClose={() => setViewerSource(null)} />
    </div>
  );
}
