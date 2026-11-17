import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, plainError } from '../../lib/api';
import { copyText } from '../../lib/clipboard';
import { useAppStore } from '../../lib/store';
import {
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Copy,
  Download,
  FileSpreadsheet,
  Filter,
  RefreshCw,
  Search,
  TrendingUp,
  Users,
  X,
} from 'lucide-react';

/**
 * 2026-11-16 · 上传任务数据统计分析面板（最终完整版，用户工程指令）：
 * · 仅分级管理员（App.tsx 对超管隐藏本菜单；后端五端点对超管一律 403）；
 * · 单任务独占（⛔ 严禁「全部任务」），默认选中名下第一个上传任务；
 * · 智能周期：same_day/无截止/不定期=日；weekend=周（ISO 周）；month_end=月；
 * · 任务专属动态部门树：只聚合当前任务+周期实际覆盖成员的 dept_path（非全量组织树）；
 * · 模式A（定期）：四 KPI + 下级部门对比图（末级自动隐藏）+ 员工明细（筛选/搜索/分页/详情/复制未交名单）；
 * · 模式B（不定期）：当天人次/人数/累计 KPI + 近 7 天趋势 + 当天流水明细；
 * · Excel 实时导出（.xlsx，无合并单元格 + 冻结首行 + 全列 AutoFilter，状态统一收敛 4 种）。
 * UI 与平台其它面板保持同一套浅色卡片风格（沿用旧统计看板的 KpiCard/StatusPill 视觉）。
 */

interface PanelTask {
  template_id: string;
  name: string;
  kind: string;
  period: string;
  anchor_weekday?: number | null;
  anchor_dom?: number | null;
  remind_at?: string | null;
  exec_weekdays?: number[] | null;
  exec_dom_days?: number[] | null;
  is_adhoc: boolean;
  has_deadline: boolean;
  deadline_type: 'same_day' | 'weekend' | 'month_end' | null;
  deadline_time: string | null;
  need_review: boolean;
}

function execCycleText(task: PanelTask): string {
  if (task.is_adhoc) return '不定期';
  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  if (task.exec_dom_days?.length) return `每月${task.exec_dom_days.join('、')}号`;
  if (task.exec_weekdays?.length === 7) return '每天';
  if (task.exec_weekdays?.length) return `每周${task.exec_weekdays.map((day) => weekdays[day - 1]).join('、')}`;
  if (task.period === 'week') return `每周${weekdays[(task.anchor_weekday || 1) - 1]}`;
  if (task.period === 'month') return `每月${task.anchor_dom || 1}号`;
  return '每天';
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
  need_review: boolean;
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

interface OverviewResp {
  ok: boolean;
  mode: 'periodic' | 'adhoc';
  template: {
    template_id: string;
    name: string;
    need_review: boolean;
    is_adhoc: boolean;
    deadline_type: string | null;
    modules: string[];
  };
  dept_tree: TreeNode[];
  selected_dept_path: string;
  kpis: PeriodicKpis | AdhocKpis;
  dept_stats?: Record<string, { covered: number; done: number }>;
  trend?: Array<{ date: string; count: number; unique: number }>;
}

interface RowItem {
  id?: number;
  instance_id?: number;
  submission_id?: number;
  phone: string;
  name: string;
  dept_snap: string;
  dept_path: string;
  status: string;
  instance_status?: string;
  submitted_at: string | null;
  preview: string;
  late_submit?: boolean;
}

interface ListResp {
  ok: boolean;
  total: number;
  page: number;
  size: number;
  items: RowItem[];
}

interface InstanceDetailResp {
  ok: boolean;
  instance: Record<string, any>;
  template: Record<string, any> | null;
  submission: {
    fields: Array<{ key: string; label: string; value: string }>;
    note: string;
    attachments: Array<{
      ref: string;
      file_name: string;
      mime: string;
      size: number;
      is_image: boolean;
      url: string;
    }>;
    submitted_at: string | null;
    status: string;
  } | null;
  submissions: Array<Record<string, any>>;
  reminders: Array<Record<string, any>>;
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待提交',
  submitted: '待审核',
  approved: '已通过',
  rejected: '已驳回',
  overdue: '已逾期',
  expired: '已过期',
  cancelled: '已取消',
};

const STATUS_CLS: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-600 border-slate-200',
  submitted: 'bg-blue-50 text-blue-700 border-blue-200',
  approved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  rejected: 'bg-rose-50 text-rose-700 border-rose-200',
  overdue: 'bg-amber-50 text-amber-700 border-amber-300',
  expired: 'bg-slate-100 text-slate-400 border-slate-200',
  cancelled: 'bg-slate-100 text-slate-400 border-slate-200',
};

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full border text-[11px] font-semibold ${STATUS_CLS[status] || STATUS_CLS.pending}`}>
      {STATUS_TEXT[status] || status}
    </span>
  );
}

function KpiCard({ label, value, sub, icon, tone = 'slate' }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; icon?: React.ReactNode; tone?: string;
}) {
  const toneMap: Record<string, string> = {
    slate: 'border-slate-200 bg-white',
    green: 'border-emerald-200 bg-emerald-50/60',
    amber: 'border-amber-200 bg-amber-50/60',
    blue: 'border-blue-200 bg-blue-50/60',
    violet: 'border-violet-200 bg-violet-50/60',
  };
  return (
    <div className={`rounded-2xl border p-4 ${toneMap[tone] || toneMap.slate}`}>
      <div className="flex items-center gap-1.5 text-xs text-slate-500 font-medium">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-1.5 text-2xl font-bold text-slate-900 tabular-nums">{value}</div>
      {sub ? <div className="mt-1 text-[11px] text-slate-400">{sub}</div> : null}
    </div>
  );
}

function fmtClock(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function todayKey(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function currentMonthKey(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}`;
}

function isoWeekOf(d: Date): { year: number; week: number } {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { year: t.getUTCFullYear(), week };
}

function isoWeekKey(d: Date): string {
  const { year, week } = isoWeekOf(d);
  return `${String(year).padStart(4, '0')}-W${String(week).padStart(2, '0')}`;
}

function weekMonday(d: Date): Date {
  const t = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = (t.getDay() + 6) % 7;
  t.setDate(t.getDate() - day);
  return t;
}

function fmtDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}/${p(d.getDate())}`;
}

/** 周选择器的近 8 周（含本周）；label 形如 `2026年第46周 (11/09 - 11/15)`（用户指定格式） */
function weekOptions(): Array<{ key: string; label: string }> {
  const out: Array<{ key: string; label: string }> = [];
  const thisMon = weekMonday(new Date());
  for (let i = 0; i < 8; i++) {
    const mon = new Date(thisMon);
    mon.setDate(mon.getDate() - i * 7);
    const sun = new Date(mon);
    sun.setDate(sun.getDate() + 6);
    const { year, week } = isoWeekOf(mon);
    out.push({
      key: `${String(year).padStart(4, '0')}-W${String(week).padStart(2, '0')}`,
      label: `${year}年第${week}周 (${fmtDate(mon)} - ${fmtDate(sun)})`,
    });
  }
  return out;
}

type PeriodMode = 'day' | 'week' | 'month';
type StatusTab = 'all' | 'done' | 'pending' | 'overdue' | 'rejected';

/** 面板详情弹窗：完整提交表单 + 图片大图 + 审核记录（复用 /instances/{id} 归一化详情） */
function DetailModal({ instanceId, onClose }: { instanceId: number; onClose: () => void }) {
  const [data, setData] = useState<InstanceDetailResp | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    api.get<InstanceDetailResp>(`/api/admin/instances/${instanceId}`)
      .then((res) => { if (alive) setData(res); })
      .catch((e) => { if (alive) setErr(plainError(String(e))); });
    return () => { alive = false; };
  }, [instanceId]);

  const inst = data?.instance || {};
  const sub = data?.submission;
  const imgs = (sub?.attachments || []).filter((a) => a.is_image);
  const files = (sub?.attachments || []).filter((a) => !a.is_image);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 sticky top-0 bg-white z-10">
          <div className="flex items-center gap-2">
            <h3 className="font-bold text-slate-900 text-sm">提交详情</h3>
            {data ? <StatusPill status={String(inst.status || '')} /> : null}
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 cursor-pointer" aria-label="关闭">
            <X className="w-4 h-4 text-slate-500" />
          </button>
        </div>
        {err ? (
          <div className="px-5 py-10 text-center text-xs text-rose-500">{err}</div>
        ) : !data ? (
          <div className="px-5 py-10 text-center text-xs text-slate-400">加载中…</div>
        ) : (
          <div className="px-5 py-4 space-y-4 text-xs">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[10px] font-semibold text-slate-400">成员</div>
                <div className="font-bold text-slate-800">{String(inst.name_snap || '')} <span className="font-mono text-[11px] text-slate-400">{String(inst.phone || '')}</span></div>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-slate-400">任务 / 部门</div>
                <div className="font-bold text-slate-800 truncate" title={String(data.template?.name || '')}>
                  {String(data.template?.name || '')} / {String(inst.dept_snap || '—')}
                </div>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-slate-400">提交时间</div>
                <div className="font-mono text-slate-700 tabular-nums">{String(inst.submitted_at || '—').slice(0, 19)}</div>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-slate-400">截止时间</div>
                <div className="font-mono text-slate-700 tabular-nums">
                  {String(inst.deadline_at || '').startsWith('9999-12-31') ? '不限' : String(inst.deadline_at || '—').slice(0, 16)}
                </div>
              </div>
            </div>

            {(sub?.fields || []).length > 0 && (
              <div className="rounded-xl border border-slate-200 overflow-hidden">
                <div className="px-3 py-2 bg-slate-50 border-b border-slate-100 font-bold text-slate-600 text-[11px]">上报内容</div>
                <table className="w-full text-left border-collapse">
                  <tbody className="divide-y divide-slate-50">
                    {(sub?.fields || []).map((f) => (
                      <tr key={f.key}>
                        <td className="py-2 pl-3 pr-2 w-32 text-slate-500">{f.label}</td>
                        <td className="py-2 px-3 font-semibold text-slate-800 break-all">{f.value || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {sub?.note ? (
              <div className="rounded-xl border border-blue-200 bg-blue-50/50 px-3 py-2">
                <div className="text-[10px] font-semibold text-blue-500 mb-0.5">补充说明</div>
                <div className="text-slate-700 whitespace-pre-wrap break-all">{sub.note}</div>
              </div>
            ) : null}

            {imgs.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-slate-400 mb-1.5">图片（{imgs.length}）</div>
                <div className="grid grid-cols-4 gap-2">
                  {imgs.map((a) => (
                    <a key={a.ref} href={a.url} target="_blank" rel="noreferrer" title={a.file_name}>
                      <img src={a.url} alt={a.file_name} className="w-full h-20 object-cover rounded-lg border border-slate-200 hover:scale-[1.02] transition-transform" />
                    </a>
                  ))}
                </div>
              </div>
            )}

            {files.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-slate-400 mb-1.5">附件（{files.length}）</div>
                <div className="flex flex-wrap gap-2">
                  {files.map((a) => (
                    <a key={a.ref} href={a.url} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium">
                      <FileSpreadsheet className="w-3.5 h-3.5" />
                      <span className="max-w-[200px] truncate">{a.file_name}</span>
                    </a>
                  ))}
                </div>
              </div>
            )}

            <div className="rounded-xl border border-slate-200 overflow-hidden">
              <div className="px-3 py-2 bg-slate-50 border-b border-slate-100 font-bold text-slate-600 text-[11px]">审核记录</div>
              <div className="px-3 py-2 space-y-1 text-slate-600">
                {inst.reviewed_at ? (
                  <div>
                    审核人 <b>{String(inst.reviewer || '—')}</b> · 审核时间 <span className="font-mono tabular-nums">{String(inst.reviewed_at).slice(0, 19)}</span>
                    {String(inst.status) === 'rejected' && inst.reject_reason ? ` · 驳回原因：${inst.reject_reason}` : ''}
                  </div>
                ) : (
                  <div>{data.instance && data.instance.need_review ? '尚未审核' : '该任务无需审核'}</div>
                )}
                <div className="text-slate-400">
                  历史提交 {data.submissions.length} 次
                  {inst.late_submit ? ' · 本期迟交' : ''}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function StatisticsView() {
  const { platform, statsFocus } = useAppStore();
  const [tasks, setTasks] = useState<PanelTask[]>([]);
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const [templateId, setTemplateId] = useState(statsFocus.id || '');
  const [periodMode, setPeriodMode] = useState<PeriodMode>('day');
  const [dayKey, setDayKey] = useState(todayKey());
  const [weekKey, setWeekKey] = useState(isoWeekKey(new Date()));
  const [monthKey, setMonthKey] = useState(currentMonthKey());
  const [deptPath, setDeptPath] = useState('');
  const [statusTab, setStatusTab] = useState<StatusTab>('all');
  const [kw, setKw] = useState('');
  const [kwInput, setKwInput] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 50;

  const [overview, setOverview] = useState<OverviewResp | null>(null);
  const [rows, setRows] = useState<ListResp | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const tickRef = useRef(0);

  const selectedTask = useMemo(
    () => tasks.find((t) => t.template_id === templateId) || null,
    [tasks, templateId],
  );
  const isAdhoc = !!selectedTask?.is_adhoc;
  const periodKey = periodMode === 'day' ? dayKey : periodMode === 'week' ? weekKey : monthKey;

  /* 模板行「统计」按钮 → 联动选中（seq 自增支持重复点击重置筛选） */
  useEffect(() => {
    if (statsFocus.seq > 0 && statsFocus.id) {
      setTemplateId(statsFocus.id);
      setDeptPath('');
      setStatusTab('all');
      setKw(''); setKwInput('');
      setPage(1);
    }
  }, [statsFocus.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 任务清单（随平台切换刷新；⛔ 后端只回上传任务，无「全部任务」概念） */
  useEffect(() => {
    let alive = true;
    api.get<{ ok: boolean; items: PanelTask[] }>(`/api/admin/stats-panel/tasks?platform=${encodeURIComponent(platform)}`)
      .then((res) => {
        if (!alive) return;
        setTasks(res.items || []);
        setTasksLoaded(true);
        setTemplateId((cur) => (cur && (res.items || []).some((t) => t.template_id === cur))
          ? cur
          : (res.items || [])[0]?.template_id || '');
      })
      .catch(() => { if (alive) setTasksLoaded(true); });
    return () => { alive = false; };
  }, [platform]);

  /* 任务切换 → 智能周期控件自动切换（指令口径：same_day/无截止/不定期=日；weekend=周；month_end=月） */
  useEffect(() => {
    if (!selectedTask) return;
    const mode: PeriodMode = selectedTask.is_adhoc || !selectedTask.deadline_type || selectedTask.deadline_type === 'same_day'
      ? 'day'
      : selectedTask.deadline_type === 'weekend' ? 'week' : 'month';
    setPeriodMode(mode);
    setDayKey(todayKey());
    setWeekKey(isoWeekKey(new Date()));
    setMonthKey(currentMonthKey());
    setDeptPath('');
    setStatusTab('all');
    setPage(1);
  }, [templateId]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    if (!templateId) { setOverview(null); setRows(null); return; }
    setLoading(true);
    setErr('');
    const tick = ++tickRef.current;
    try {
      const common = `template_id=${encodeURIComponent(templateId)}&period_key=${encodeURIComponent(periodKey)}&dept_path=${encodeURIComponent(deptPath)}`;
      const ov = await api.get<OverviewResp>(`/api/admin/stats-panel/overview?${common}`);
      if (tick !== tickRef.current) return;
      setOverview(ov);
      let listUrl: string;
      if (ov.mode === 'adhoc') {
        listUrl = `/api/admin/stats-panel/submissions-day?${common}&q=${encodeURIComponent(kw)}&page=${page}&size=${PAGE_SIZE}`;
      } else {
        const group = statusTab === 'all' ? 'all'
          : statusTab === 'done' ? 'done'
            : statusTab === 'pending' ? 'pending'
              : statusTab === 'overdue' ? 'overdue' : 'rejected';
        listUrl = `/api/admin/stats-panel/instances?${common}&status_group=${group}&q=${encodeURIComponent(kw)}&page=${page}&size=${PAGE_SIZE}`;
      }
      const list = await api.get<ListResp>(listUrl);
      if (tick !== tickRef.current) return;
      setRows(list);
      setLastUpdated(new Date());
    } catch (e) {
      if (tick !== tickRef.current) return;
      setErr(plainError(String(e)));
    } finally {
      if (tick === tickRef.current) setLoading(false);
    }
  }, [templateId, periodKey, deptPath, statusTab, kw, page, PAGE_SIZE]);

  useEffect(() => { void load(); }, [load]);

  /* 15 秒自动刷新（沿用平台统计页惯例） */
  useEffect(() => {
    if (!autoRefresh || !templateId) return;
    const t = window.setInterval(() => { void load(); }, 15000);
    return () => window.clearInterval(t);
  }, [autoRefresh, load, templateId]);

  /* 分页/筛选变化重置页码 */
  useEffect(() => { setPage(1); }, [statusTab, kw, deptPath]);

  const statusGroupLabel: Record<StatusTab, string> = {
    all: '全部人员', done: '已完成', pending: '待提交', overdue: '已逾期', rejected: '被驳回',
  };

  /* 下级部门对比（选中节点的直接子级；末级无子级 → 前端直接不渲染该区块） */
  const deptChildren = useMemo(() => {
    if (!overview || overview.mode !== 'periodic' || !overview.dept_stats) return [] as TreeNode[];
    const sel = deptPath;
    const kids = (overview.dept_tree || []).filter((n) => {
      if (!sel) return n.depth === 1;
      return n.path.startsWith(sel + '/') && n.path.split('/').length === sel.split('/').length + 1;
    });
    return kids;
  }, [overview, deptPath]);

  /* 复制未交名单：拉全量 todo 行拼「姓名 手机号 部门」 */
  const copyTodoList = useCallback(async () => {
    if (!templateId) return;
    try {
      const common = `template_id=${encodeURIComponent(templateId)}&period_key=${encodeURIComponent(periodKey)}&dept_path=${encodeURIComponent(deptPath)}`;
      const res = await api.get<ListResp>(`/api/admin/stats-panel/instances?${common}&status_group=todo&size=5000&page=1`);
      const lines = (res.items || []).map((r) => `${r.name}\t${r.phone}\t${r.dept_snap || r.dept_path}`);
      const text = lines.length
        ? `【未交名单】${selectedTask?.name || ''} · ${periodKey}\n${lines.join('\n')}`
        : '当前筛选范围内没有未交人员';
      const ok = await copyText(text);
      if (ok) window.alert(`已复制 ${lines.length} 名未交人员（粘贴到表格即可）`);
      else window.alert('复制失败：请手动选择下方表格复制');
    } catch (e) {
      window.alert(plainError(String(e)));
    }
  }, [templateId, periodKey, deptPath, selectedTask]);

  /* Excel 导出（blob 下载；403/校验错误透传人话） */
  const exportXlsx = useCallback(async () => {
    if (!templateId || exporting) return;
    setExporting(true);
    try {
      const qs = `template_id=${encodeURIComponent(templateId)}&period_key=${encodeURIComponent(periodKey)}&dept_path=${encodeURIComponent(deptPath)}`;
      const resp = await fetch(`/api/admin/stats-panel/export?${qs}`, { credentials: 'same-origin' });
      if (!resp.ok) {
        let msg = `导出失败（HTTP ${resp.status}）`;
        try {
          const j = await resp.json();
          if (j && j.error && j.error.message) msg = j.error.message;
        } catch { /* 非 JSON 报错走默认文案 */ }
        window.alert(msg);
        return;
      }
      const blob = await resp.blob();
      const cd = resp.headers.get('Content-Disposition') || '';
      const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = star ? decodeURIComponent(star[1]) : '统计数据.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      window.alert(plainError(String(e)));
    } finally {
      setExporting(false);
    }
  }, [templateId, periodKey, deptPath, exporting]);

  const totalPages = rows ? Math.max(1, Math.ceil(rows.total / PAGE_SIZE)) : 1;

  const kpis = overview?.kpis as PeriodicKpis | undefined;
  const adhocKpis = overview?.kpis as AdhocKpis | undefined;
  const trendMax = Math.max(1, ...(overview?.trend || []).map((t) => t.count));

  return (
    <div className="space-y-4">
      {/* 顶部：标题 + 刷新 + 导出 */}
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="text-lg font-bold text-slate-900">任务数据统计</h2>
          <p className="text-xs text-slate-400 mt-0.5">
            上传任务 · 单任务进度 / 流水分析 · Excel 实时导出
            {lastUpdated ? ` · 最后更新 ${fmtClock(lastUpdated)}` : ''}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="w-3.5 h-3.5 accent-blue-600 cursor-pointer"
            />
            15 秒自动刷新
          </label>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold cursor-pointer transition-colors disabled:opacity-60"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            立即刷新
          </button>
          <button
            type="button"
            onClick={() => void exportXlsx()}
            disabled={!templateId || exporting}
            title="导出当前任务+周期+部门范围的实时全量数据（.xlsx，支持 Excel 直接筛选）"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold cursor-pointer transition-colors disabled:opacity-50"
          >
            <Download className={`w-3.5 h-3.5 ${exporting ? 'animate-pulse' : ''}`} />
            {exporting ? '导出中…' : '导出统计表格'}
          </button>
        </div>
      </div>

      {/* 顶部控制栏：任务（单选，⛔ 无「全部任务」） + 智能周期 + 任务专属部门树 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4 flex flex-wrap items-end gap-4">
        <div className="min-w-[220px]">
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">任务（单选）</label>
          <select
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            disabled={tasksLoaded && tasks.length === 0}
            className="border border-slate-200 rounded-xl px-3 py-2 text-xs font-semibold text-slate-800 bg-white outline-none focus:border-blue-500 min-w-[220px] cursor-pointer"
          >
            {tasks.map((t) => (
              <option key={t.template_id} value={t.template_id}>{t.name}</option>
            ))}
          </select>
        </div>

        {selectedTask && (
          <dl className="flex gap-4 text-xs text-slate-700">
            <div><dt className="mb-1 text-slate-500">执行时间</dt><dd>{selectedTask.remind_at || '—'}</dd></div>
            <div><dt className="mb-1 text-slate-500">执行周期</dt><dd>{execCycleText(selectedTask)}</dd></div>
          </dl>
        )}
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">
            {periodMode === 'day' ? '日期' : periodMode === 'week' ? '周' : '月份'}
            <span className="ml-1 text-slate-300">（随任务截止规则自动切换）</span>
          </label>
          {periodMode === 'day' && (
            <input
              type="date"
              value={dayKey}
              onChange={(e) => { setDayKey(e.target.value); }}
              className="border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 bg-white outline-none focus:border-blue-500 cursor-pointer"
            />
          )}
          {periodMode === 'week' && (
            <select
              value={weekKey}
              onChange={(e) => setWeekKey(e.target.value)}
              className="border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 bg-white outline-none focus:border-blue-500 cursor-pointer"
            >
              {weekOptions().map((w) => (
                <option key={w.key} value={w.key}>{w.label}</option>
              ))}
            </select>
          )}
          {periodMode === 'month' && (
            <input
              type="month"
              value={monthKey}
              onChange={(e) => setMonthKey(e.target.value)}
              className="border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 bg-white outline-none focus:border-blue-500 cursor-pointer"
            />
          )}
        </div>

        <div className="min-w-[200px]">
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">
            覆盖部门（任务专属 · 按实际参与成员聚合）
          </label>
          <select
            value={deptPath}
            onChange={(e) => setDeptPath(e.target.value)}
            disabled={!(overview?.dept_tree || []).length}
            className="border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 bg-white outline-none focus:border-blue-500 max-w-[260px] cursor-pointer disabled:opacity-60"
          >
            <option value="">全部覆盖部门</option>
            {(overview?.dept_tree || []).map((n) => (
              <option key={n.path} value={n.path}>
                {'　'.repeat(Math.max(0, n.depth - 1))}{n.name}（{n.count}人）
              </option>
            ))}
          </select>
        </div>

        {deptPath && (
          <button
            type="button"
            onClick={() => setDeptPath('')}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 text-[11px] font-semibold cursor-pointer"
            title="回到全部覆盖部门"
          >
            <X className="w-3 h-3" /> 退出部门下钻
          </button>
        )}
      </div>

      {err ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-600 font-medium">{err}</div>
      ) : null}

      {!tasksLoaded ? null : tasks.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white px-6 py-16 text-center">
          <BarChart3 className="w-10 h-10 text-slate-200 mx-auto mb-3" />
          <div className="text-sm font-bold text-slate-500">请先创建上传任务</div>
          <div className="text-xs text-slate-400 mt-1">
            当前办公软件下，您名下还没有上传类任务；到「任务模板管理」创建后即可在此查看统计数据
          </div>
        </div>
      ) : !overview ? null : overview.mode === 'periodic' ? (
        <>
          {/* 模式A：四大 KPI */}
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
            <KpiCard label="应参与人数" value={kpis?.total ?? '—'} icon={<Users className="w-3.5 h-3.5" />} sub={`周期 ${periodKey}`} />
            <KpiCard
              label="已完成人数"
              value={kpis?.done ?? '—'}
              icon={<CheckCircle2 className="w-3.5 h-3.5" />}
              tone="green"
              sub={
                kpis?.need_review
                  ? `（含待审 ${kpis.pending_review} 人 / 已通过 ${kpis.approved} 人）`
                  : undefined
              }
            />
            <KpiCard label="未完成人数" value={kpis?.not_done ?? '—'} icon={<ClipboardList className="w-3.5 h-3.5" />} tone="amber"
              sub={`待提交 ${kpis?.pending ?? 0} · 已逾期 ${kpis?.overdue ?? 0} · 被驳回 ${kpis?.rejected ?? 0}`} />
            <KpiCard label="任务完成率" value={`${kpis?.rate ?? 0}%`} icon={<TrendingUp className="w-3.5 h-3.5" />} tone="blue"
              sub={
                <div className="mt-1.5 h-2 rounded-full bg-slate-200 overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${Math.min(100, kpis?.rate ?? 0)}%` }} />
                </div>
              } />
          </div>

          {/* 下级部门完成率对比（末级部门自动隐藏——无子级即不渲染） */}
          {deptChildren.length > 0 && (
            <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-100">
                <Filter className="w-3.5 h-3.5 text-slate-400" />
                <h4 className="text-sm font-bold text-slate-700">
                  {deptPath ? `「${deptPath.split('/').pop()}」下级部门完成率` : '下级部门完成率对比'}
                </h4>
                <span className="text-[11px] text-slate-400">点击条形下钻选中该部门</span>
              </div>
              <div className="p-4 space-y-2.5">
                {deptChildren.map((n) => {
                  const st = overview.dept_stats?.[n.path] || { covered: n.count, done: 0 };
                  const pct = st.covered ? Math.round((st.done / st.covered) * 100) : 0;
                  return (
                    <button
                      key={n.path}
                      type="button"
                      onClick={() => setDeptPath(n.path)}
                      className="w-full text-left group cursor-pointer"
                      title="点击筛选到该部门"
                    >
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="font-semibold text-slate-700 group-hover:text-blue-600">{n.name}</span>
                        <span className="text-slate-400 tabular-nums">{st.done} / {st.covered} 人 · {pct}%</span>
                      </div>
                      <div className="h-3 rounded-full bg-slate-100 overflow-hidden">
                        <div className={`h-full rounded-full transition-all ${pct >= 100 ? 'bg-emerald-500' : pct >= 50 ? 'bg-blue-500' : 'bg-amber-400'}`}
                          style={{ width: `${Math.max(2, pct)}%` }} />
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* 员工明细 */}
          <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-100">
              <div className="flex items-center gap-1 bg-slate-100 rounded-xl p-1">
                {(['all', 'done', 'pending', 'overdue', 'rejected'] as StatusTab[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setStatusTab(t)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                      statusTab === t ? 'bg-white text-blue-600 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    {statusGroupLabel[t]}
                  </button>
                ))}
              </div>
              <form
                className="relative ml-auto"
                onSubmit={(e) => { e.preventDefault(); setKw(kwInput.trim()); }}
              >
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2" />
                <input
                  value={kwInput}
                  onChange={(e) => setKwInput(e.target.value)}
                  placeholder="搜姓名/手机号后回车"
                  className="bg-white border border-slate-200 rounded-lg pl-7 pr-2 py-1 text-xs outline-none focus:border-blue-500 w-44"
                />
              </form>
              {statusTab !== 'all' && statusTab !== 'done' && (
                <button
                  type="button"
                  onClick={() => void copyTodoList()}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-amber-50 hover:bg-amber-100 text-amber-700 text-[11px] font-semibold cursor-pointer"
                  title="复制当前范围内全部未交人员的「姓名 手机号 部门」"
                >
                  <Copy className="w-3 h-3" /> 一键复制未交名单
                </button>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 text-[11px] font-semibold text-slate-400 bg-slate-50/60">
                    <th className="py-2.5 pl-4 pr-3">成员</th>
                    <th className="py-2.5 px-3">所属部门</th>
                    <th className="py-2.5 px-3">状态</th>
                    <th className="py-2.5 px-3">提交时间</th>
                    <th className="py-2.5 px-3">上报内容预览</th>
                    <th className="py-2.5 px-3 pr-4 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50 text-xs">
                  {(rows?.items || []).length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-10 text-center text-slate-300">当前筛选范围内暂无人员</td>
                    </tr>
                  ) : (
                    (rows?.items || []).map((r) => (
                      <tr key={`${r.id}-${r.phone}`} className="hover:bg-slate-50/70">
                        <td className="py-2.5 pl-4 pr-3">
                          <div className="font-bold text-slate-800">{r.name}</div>
                          <div className="text-[11px] text-slate-400 font-mono">{r.phone}</div>
                        </td>
                        <td className="py-2.5 px-3 text-slate-500 max-w-[160px] truncate" title={r.dept_path}>
                          {r.dept_snap || r.dept_path || '—'}
                        </td>
                        <td className="py-2.5 px-3"><StatusPill status={r.status} /></td>
                        <td className="py-2.5 px-3 font-mono text-slate-600 tabular-nums">
                          {r.submitted_at ? String(r.submitted_at).slice(5, 16) : '—'}
                          {r.late_submit ? <span className="ml-1.5 text-[10px] font-bold text-amber-600">迟交</span> : null}
                        </td>
                        <td className="py-2.5 px-3 text-slate-500 max-w-[220px] truncate" title={r.preview}>{r.preview || '—'}</td>
                        <td className="py-2.5 px-3 pr-4 text-right">
                          <button
                            type="button"
                            onClick={() => setDetailId(Number(r.id))}
                            className="px-2.5 py-1 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 text-[11px] font-semibold cursor-pointer"
                          >
                            查看详情
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 bg-slate-50/60 text-xs text-slate-500">
              <span className="tabular-nums">共 {rows?.total ?? 0} 人 · 第 {page} / {totalPages} 页</span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                  aria-label="上一页"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                  aria-label="下一页"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </>
      ) : (
        <>
          {/* 模式B：不定期任务流水统计 */}
          <div className="grid grid-cols-3 gap-3">
            <KpiCard label="当天上报人次" value={adhocKpis?.day_count ?? '—'} icon={<CalendarDays className="w-3.5 h-3.5" />} sub={`所选日期 ${periodKey}`} />
            <KpiCard label="当天上报人数（去重）" value={adhocKpis?.day_unique ?? '—'} icon={<Users className="w-3.5 h-3.5" />} tone="blue" sub="按手机号去重" />
            <KpiCard label="历史累计总上报" value={adhocKpis?.total_count ?? '—'} icon={<TrendingUp className="w-3.5 h-3.5" />} tone="violet"
              sub={`累计 ${adhocKpis?.total_count ?? 0} 人次 / ${adhocKpis?.total_unique ?? 0} 人`} />
          </div>

          {/* 近 7 天趋势（点击柱子切到那天） */}
          <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-100">
              <TrendingUp className="w-3.5 h-3.5 text-slate-400" />
              <h4 className="text-sm font-bold text-slate-700">近 7 天上报趋势</h4>
              <span className="text-[11px] text-slate-400">点击某天查看当日明细</span>
            </div>
            <div className="p-4 flex items-end gap-2.5 h-44">
              {(overview.trend || []).map((t) => {
                const h = Math.max(4, Math.round((t.count / trendMax) * 120));
                const active = t.date === dayKey;
                return (
                  <button
                    key={t.date}
                    type="button"
                    onClick={() => { setDayKey(t.date); setPage(1); }}
                    className="flex-1 flex flex-col items-center justify-end gap-1.5 group cursor-pointer"
                    title={`${t.date} · ${t.count} 人次 / ${t.unique} 人`}
                  >
                    <span className="text-[11px] font-bold tabular-nums text-slate-600 group-hover:text-blue-600">{t.count}</span>
                    <div className={`w-full rounded-t-lg transition-all ${active ? 'bg-blue-600' : 'bg-blue-200 group-hover:bg-blue-400'}`}
                      style={{ height: h }} />
                    <span className={`text-[10px] tabular-nums ${active ? 'text-blue-600 font-bold' : 'text-slate-400'}`}>
                      {t.date.slice(5)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* 当天流水明细 */}
          <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-100">
              <h4 className="text-sm font-bold text-slate-700">{periodKey} 上报明细</h4>
              <span className="text-[11px] text-slate-400">每条提交一行 · 时间精确到秒</span>
              <form
                className="relative ml-auto"
                onSubmit={(e) => { e.preventDefault(); setKw(kwInput.trim()); }}
              >
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2" />
                <input
                  value={kwInput}
                  onChange={(e) => setKwInput(e.target.value)}
                  placeholder="搜姓名/手机号后回车"
                  className="bg-white border border-slate-200 rounded-lg pl-7 pr-2 py-1 text-xs outline-none focus:border-blue-500 w-44"
                />
              </form>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 text-[11px] font-semibold text-slate-400 bg-slate-50/60">
                    <th className="py-2.5 pl-4 pr-3">上报人</th>
                    <th className="py-2.5 px-3">所属部门</th>
                    <th className="py-2.5 px-3">提交时间</th>
                    <th className="py-2.5 px-3">上报内容预览</th>
                    <th className="py-2.5 px-3 pr-4 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50 text-xs">
                  {(rows?.items || []).length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-10 text-center text-slate-300">当天暂无上报记录</td>
                    </tr>
                  ) : (
                    (rows?.items || []).map((r) => (
                      <tr key={r.submission_id} className="hover:bg-slate-50/70">
                        <td className="py-2.5 pl-4 pr-3">
                          <div className="font-bold text-slate-800">{r.name}</div>
                          <div className="text-[11px] text-slate-400 font-mono">{r.phone}</div>
                        </td>
                        <td className="py-2.5 px-3 text-slate-500 max-w-[160px] truncate" title={r.dept_path}>
                          {r.dept_snap || r.dept_path || '—'}
                        </td>
                        <td className="py-2.5 px-3 font-mono text-slate-600 tabular-nums">
                          {String(r.submitted_at || '').slice(5, 19)}
                        </td>
                        <td className="py-2.5 px-3 text-slate-500 max-w-[220px] truncate" title={r.preview}>{r.preview || '—'}</td>
                        <td className="py-2.5 px-3 pr-4 text-right">
                          <button
                            type="button"
                            onClick={() => setDetailId(Number(r.instance_id))}
                            className="px-2.5 py-1 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 text-[11px] font-semibold cursor-pointer"
                          >
                            查看详情
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 bg-slate-50/60 text-xs text-slate-500">
              <span className="tabular-nums">共 {rows?.total ?? 0} 条 · 第 {page} / {totalPages} 页</span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                  aria-label="上一页"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                  aria-label="下一页"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {detailId !== null && <DetailModal instanceId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
