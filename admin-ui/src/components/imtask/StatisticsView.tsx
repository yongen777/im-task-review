import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, plainError } from '../../lib/api';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  RefreshCw,
  Search,
  Users,
} from 'lucide-react';

/**
 * 2026-11-13 统计看板重设计（用户拍板「UI 太乱，重做；要能看三点任务这些人的实时上传数据」）：
 * · 主视角 = **单任务当期实时上传名单**：选任务 + 周期 → 应交/已交/未交 KPI +
 *   已交名单（提交时间/迟交标记）与未交名单（状态/截止），15 秒自动刷新，一眼掌握进度；
 * · 保留多维台账（报数/文本/附件三视图）与督办红榜，收纳为折叠区（不再抢主视线）；
 * · 权限在后端收敛：超管=全局只读；分级管理员自动 owner 隔离（/stats/dashboard 与
 *   /stats/roster 同一口径）。
 * 旧版（2026-11-13 冲刺模块三）为深色调试风格，本次整体重写为与全站一致的浅色卡片风格。
 */

interface DashboardKpis {
  should_submit: number;
  submitted: number;
  approved: number;
  rejected: number;
  pending: number;
  overdue: number;
  late_submit: number;
  completion_rate: number;
  on_time_rate: number;
  avg_submit_minutes: number | null;
}

interface ViewRow {
  phone: string;
  name: string;
  dept: string;
  template_id: string;
  period_key: string;
  status: string;
}

interface NumberRow extends ViewRow { field: string; value: number | string }
interface TextRow extends ViewRow { text: string }
interface FileRow extends ViewRow { file_count: number; refs: string[] }
interface RedRow {
  phone: string; name: string; dept: string; template_name: string;
  deadline_at: string; overdue_hours: number;
}

interface DashboardResp {
  ok: boolean;
  scope_owner_id: number | null;
  kpis: DashboardKpis;
  views: { numbers: NumberRow[]; texts: TextRow[]; files: FileRow[] };
  red_list: RedRow[];
}

interface RosterItem {
  phone: string;
  name: string;
  dept: string;
  status: string;
  submitted_at: string | null;
  late_submit: boolean;
  deadline_at: string | null;
  period_key: string;
}

interface RosterResp {
  ok: boolean;
  total: number;
  items: RosterItem[];
}

interface TemplateItem {
  template_id: string;
  name: string;
  kind: string;
  period: string;
  enabled: number | boolean;
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待提交',
  submitted: '待审核',
  approved: '已通过',
  rejected: '已驳回',
  overdue: '已逾期',
  expired: '已过期',
  sent: '已通知',
  cancelled: '已取消',
};

const DONE_STATUSES = new Set(['submitted', 'approved']);
const NOT_DONE_STATUSES = new Set(['pending', 'overdue', 'rejected']);

function fmtDeadline(raw: string | null): string {
  if (!raw) return '—';
  if (raw.startsWith('9999-12-31')) return '不限';
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return raw;
  return `${Number(m[2])}月${Number(m[3])}日 ${m[4]}:${m[5]}`;
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

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    pending: 'bg-slate-100 text-slate-600 border-slate-200',
    submitted: 'bg-blue-50 text-blue-700 border-blue-200',
    approved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    rejected: 'bg-rose-50 text-rose-700 border-rose-200',
    overdue: 'bg-amber-50 text-amber-700 border-amber-300',
    expired: 'bg-slate-100 text-slate-400 border-slate-200',
    sent: 'bg-sky-50 text-sky-700 border-sky-200',
    cancelled: 'bg-slate-100 text-slate-400 border-slate-200',
  };
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full border text-[11px] font-semibold ${map[status] || map.pending}`}>
      {STATUS_TEXT[status] || status}
    </span>
  );
}

function KpiCard({ label, value, sub, icon, tone = 'slate' }: {
  label: string; value: string; sub?: string; icon?: React.ReactNode; tone?: string;
}) {
  const toneMap: Record<string, string> = {
    slate: 'border-slate-200 bg-white',
    green: 'border-emerald-200 bg-emerald-50/60',
    amber: 'border-amber-200 bg-amber-50/60',
    blue: 'border-blue-200 bg-blue-50/60',
    rose: 'border-rose-200 bg-rose-50/60',
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

/** 名单面板：搜索 + 内存分页（50/页），2600 人规模不卡顿 */
function RosterPanel({ title, tone, rows, emptyText, mode }: {
  title: string;
  tone: 'green' | 'amber';
  rows: RosterItem[];
  emptyText: string;
  mode: 'done' | 'todo';
}) {
  const [kw, setKw] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 50;

  const filtered = useMemo(() => {
    if (!kw.trim()) return rows;
    const k = kw.trim().toLowerCase();
    return rows.filter((r) => r.phone.includes(k) || r.name.toLowerCase().includes(k));
  }, [rows, kw]);

  useEffect(() => { setPage(1); }, [kw, rows.length]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const headCls = tone === 'green'
    ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
    : 'bg-amber-50 text-amber-800 border-amber-200';

  return (
    <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <div className={`flex flex-wrap items-center gap-2 px-4 py-2.5 border-b ${headCls}`}>
        <h4 className="text-sm font-bold">{title}</h4>
        <span className="text-xs font-bold tabular-nums opacity-80">{filtered.length} 人</span>
        <div className="ml-auto relative">
          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2" />
          <input
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="搜姓名/手机号"
            className="bg-white border border-slate-200 rounded-lg pl-7 pr-2 py-1 text-xs outline-none focus:border-blue-500 w-36 sm:w-44"
          />
        </div>
      </div>
      <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-slate-100 text-[11px] font-semibold text-slate-400">
              <th className="py-2 pl-4 pr-3">成员</th>
              <th className="py-2 px-3">部门</th>
              {mode === 'done' ? (
                <>
                  <th className="py-2 px-3">提交时间</th>
                  <th className="py-2 px-3 pr-4">状态</th>
                </>
              ) : (
                <>
                  <th className="py-2 px-3">截止</th>
                  <th className="py-2 px-3 pr-4">状态</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 text-xs">
            {pageRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-10 text-center text-slate-300">{emptyText}</td>
              </tr>
            ) : (
              pageRows.map((r) => (
                <tr key={`${r.phone}-${r.period_key}`} className="hover:bg-slate-50/70">
                  <td className="py-2 pl-4 pr-3">
                    <div className="font-bold text-slate-800">{r.name}</div>
                    <div className="text-[11px] text-slate-400 font-mono">{r.phone}</div>
                  </td>
                  <td className="py-2 px-3 text-slate-500 max-w-[140px] truncate" title={r.dept}>
                    {r.dept || '—'}
                  </td>
                  {mode === 'done' ? (
                    <>
                      <td className="py-2 px-3 font-mono text-slate-600 tabular-nums">
                        {r.submitted_at ? String(r.submitted_at).slice(5, 16) : '—'}
                        {r.late_submit ? (
                          <span className="ml-1.5 text-[10px] font-bold text-amber-600">迟交</span>
                        ) : null}
                      </td>
                      <td className="py-2 px-3 pr-4"><StatusPill status={r.status} /></td>
                    </>
                  ) : (
                    <>
                      <td className="py-2 px-3 font-mono text-slate-600 tabular-nums">
                        {fmtDeadline(r.deadline_at)}
                      </td>
                      <td className="py-2 px-3 pr-4"><StatusPill status={r.status} /></td>
                    </>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 bg-slate-50/60 text-xs text-slate-500">
          <span className="tabular-nums">第 {safePage} / {totalPages} 页</span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
              aria-label="上一页"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="p-1 rounded-md border border-slate-200 bg-white disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
              aria-label="下一页"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

type ViewTab = 'numbers' | 'texts' | 'files';

export function StatisticsView() {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [data, setData] = useState<DashboardResp | null>(null);
  const [roster, setRoster] = useState<RosterResp | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [viewTab, setViewTab] = useState<ViewTab>('numbers');
  const [templateId, setTemplateId] = useState('');
  const [periodKey, setPeriodKey] = useState(todayKey());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const tickRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    const tick = ++tickRef.current;
    try {
      const qs = new URLSearchParams();
      if (templateId.trim()) qs.set('template_id', templateId.trim());
      if (periodKey.trim()) qs.set('period_key', periodKey.trim());
      const dash = await api.get<DashboardResp>(`/api/admin/stats/dashboard?${qs.toString()}`);
      let rosterResp: RosterResp | null = null;
      if (templateId.trim()) {
        rosterResp = await api.get<RosterResp>(`/api/admin/stats/roster?${qs.toString()}`);
      }
      if (tick !== tickRef.current) return; // 过期响应丢弃（快速切任务时防串数据）
      setData(dash);
      setRoster(rosterResp);
      setLastUpdated(new Date());
    } catch (e) {
      if (tick !== tickRef.current) return;
      setErr(plainError(String(e)));
    } finally {
      if (tick === tickRef.current) setLoading(false);
    }
  }, [templateId, periodKey]);

  useEffect(() => { void load(); }, [load]);

  /* 2026-11-13 用户需求核心：实时 —— 15 秒自动刷新（可关闭） */
  useEffect(() => {
    if (!autoRefresh) return;
    const t = window.setInterval(() => { void load(); }, 15000);
    return () => window.clearInterval(t);
  }, [autoRefresh, load]);

  useEffect(() => {
    api.get<{ ok: boolean; items: TemplateItem[] }>('/api/admin/templates?size=500')
      .then((res) => setTemplates((res.items || []).filter((t) => t.kind !== 'notice')))
      .catch(() => { /* 模板下拉加载失败不阻塞看板 */ });
  }, []);

  const k = data?.kpis;
  const doneRows = useMemo(
    () => (roster?.items || []).filter((r) => DONE_STATUSES.has(r.status)),
    [roster],
  );
  const todoRows = useMemo(
    () => (roster?.items || []).filter((r) => NOT_DONE_STATUSES.has(r.status)),
    [roster],
  );
  const otherCount = useMemo(() => {
    const items = roster?.items || [];
    return items.filter((r) => !DONE_STATUSES.has(r.status) && !NOT_DONE_STATUSES.has(r.status)).length;
  }, [roster]);

  const viewRows: Array<Record<string, unknown>> = useMemo(() => {
    if (!data) return [];
    if (viewTab === 'numbers') return data.views.numbers as unknown as Array<Record<string, unknown>>;
    if (viewTab === 'texts') return data.views.texts as unknown as Array<Record<string, unknown>>;
    return data.views.files as unknown as Array<Record<string, unknown>>;
  }, [data, viewTab]);

  const viewHeaders: Record<ViewTab, string[]> = {
    numbers: ['成员', '部门', '字段', '数值'],
    texts: ['成员', '部门', '说明'],
    files: ['成员', '部门', '附件数'],
  };

  return (
    <div className="space-y-4">
      {/* 顶部：标题 + 刷新控制 */}
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="text-lg font-bold text-slate-900">统计看板</h2>
          <p className="text-xs text-slate-400 mt-0.5">
            任务实时上传进度 · 已交/未交名单 · 每 15 秒自动刷新
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
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold cursor-pointer transition-colors disabled:opacity-60"
            disabled={loading}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            立即刷新
          </button>
        </div>
      </div>

      {/* 筛选：任务 + 周期 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">任务</label>
          <select
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-56 sm:w-72"
          >
            <option value="">全部任务（跨任务汇总）</option>
            {templates.map((t) => (
              <option key={t.template_id} value={t.template_id}>
                {t.name}{Number(t.enabled) ? '' : '（已停用）'}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">
            周期键 <span className="font-normal text-slate-400">（日任务=2026-11-13，月任务=2026-11，留空=全部）</span>
          </label>
          <input
            value={periodKey}
            onChange={(e) => setPeriodKey(e.target.value)}
            placeholder="2026-11-13"
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44 font-mono"
          />
        </div>
        {templateId ? (
          <button
            type="button"
            onClick={() => setPeriodKey(todayKey())}
            className="px-3 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-slate-600 hover:border-blue-400 hover:text-blue-600 cursor-pointer transition-colors"
          >
            回到今天
          </button>
        ) : null}
      </div>

      {err ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-700">{err}</div>
      ) : null}

      {/* KPI */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiCard label="应交" value={String(k?.should_submit ?? 0)} icon={<Users className="w-3.5 h-3.5" />} />
        <KpiCard label="已交" value={String(k?.submitted ?? 0)} tone="green" icon={<CheckCircle2 className="w-3.5 h-3.5" />}
          sub={k ? `通过 ${k.approved} · 待审 ${k.submitted - k.approved >= 0 ? k.submitted - k.approved : 0}` : undefined} />
        <KpiCard label="未交" value={String((k?.pending ?? 0) + (k?.overdue ?? 0) + (k?.rejected ?? 0))} tone="amber"
          icon={<AlertTriangle className="w-3.5 h-3.5" />}
          sub={k ? `待提交 ${k.pending} · 逾期 ${k.overdue} · 驳回 ${k.rejected}` : undefined} />
        <KpiCard label="完成率" value={`${k?.completion_rate ?? 0}%`} tone="blue" icon={<Activity className="w-3.5 h-3.5" />} />
        <KpiCard label="按时率" value={`${k?.on_time_rate ?? 0}%`} sub={k ? `迟交 ${k.late_submit} 条` : undefined} />
        <KpiCard label="平均耗时" value={k?.avg_submit_minutes != null ? `${k.avg_submit_minutes} 分` : '—'}
          icon={<Clock className="w-3.5 h-3.5" />} sub="派单 → 提交" />
      </div>

      {/* 实时名单（选中任务时） */}
      {templateId ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <RosterPanel
            title="✅ 已交名单"
            tone="green"
            rows={doneRows}
            mode="done"
            emptyText="还没有人提交，等一等或点「立即刷新」"
          />
          <RosterPanel
            title="⏳ 未交名单"
            tone="amber"
            rows={todoRows}
            mode="todo"
            emptyText="全部交齐了，零催办 🎉"
          />
          {otherCount > 0 ? (
            <div className="xl:col-span-2 text-[11px] text-slate-400">
              另有 {otherCount} 条已过期/已取消/通知类实例未计入上述名单。
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-10 text-center text-xs text-slate-400">
          在上方选择一个任务（如「三点任务」），即可查看该任务当期每个人的实时上传情况。
        </div>
      )}

      {/* 台账三视图 + 督办红榜（折叠收纳，不抢主视线） */}
      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
        <button
          type="button"
          onClick={() => setLedgerOpen((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 text-sm font-bold text-slate-700 hover:bg-slate-50 cursor-pointer"
        >
          <span>台账明细（报数 / 文本 / 附件）与督办红榜</span>
          <ChevronRight className={`w-4 h-4 text-slate-400 transition-transform ${ledgerOpen ? 'rotate-90' : ''}`} />
        </button>
        {ledgerOpen ? (
          <div className="px-4 pb-4 space-y-4">
            <div className="rounded-xl border border-slate-200 overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100">
                {(['numbers', 'texts', 'files'] as ViewTab[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setViewTab(t)}
                    className={`px-3 py-1 rounded-full text-xs font-semibold cursor-pointer transition-colors ${
                      viewTab === t
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {t === 'numbers' ? '报数' : t === 'texts' ? '文本' : '附件'}
                  </button>
                ))}
                <span className="ml-auto text-[11px] text-slate-400 tabular-nums">{viewRows.length} 条</span>
              </div>
              <div className="overflow-x-auto max-h-[320px] overflow-y-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-100 text-[11px] font-semibold text-slate-400">
                      {viewHeaders[viewTab].map((h) => (
                        <th key={h} className="py-2 px-3">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50 text-xs">
                    {viewRows.length === 0 ? (
                      <tr><td colSpan={4} className="py-8 text-center text-slate-300">暂无提交明细</td></tr>
                    ) : (
                      viewRows.map((r, i) => (
                        <tr key={i} className="hover:bg-slate-50/70">
                          <td className="py-2 px-3">
                            <span className="font-semibold text-slate-700">{String(r.name)}</span>
                            <span className="ml-1.5 text-[11px] text-slate-400 font-mono">{String(r.phone)}</span>
                          </td>
                          <td className="py-2 px-3 text-slate-500">{String(r.dept || '—')}</td>
                          {viewTab === 'numbers' ? (
                            <>
                              <td className="py-2 px-3 text-slate-500">{String((r as NumberRow).field)}</td>
                              <td className="py-2 px-3 font-bold text-slate-800 tabular-nums">{String((r as NumberRow).value)}</td>
                            </>
                          ) : viewTab === 'texts' ? (
                            <td className="py-2 px-3 text-slate-600 max-w-[420px]">{String((r as TextRow).text)}</td>
                          ) : (
                            <td className="py-2 px-3 text-slate-600">{String((r as FileRow).file_count)} 个附件</td>
                          )}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="rounded-xl border border-rose-200 overflow-hidden">
              <div className="px-3 py-2 bg-rose-50 border-b border-rose-100 text-sm font-bold text-rose-800">
                督办红榜（过期未交 {data?.red_list.length ?? 0} 人）
              </div>
              <div className="overflow-x-auto max-h-[280px] overflow-y-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-100 text-[11px] font-semibold text-slate-400">
                      <th className="py-2 px-3">成员</th>
                      <th className="py-2 px-3">部门</th>
                      <th className="py-2 px-3">任务</th>
                      <th className="py-2 px-3">截止</th>
                      <th className="py-2 px-3">滞留</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50 text-xs">
                    {(data?.red_list ?? []).length === 0 ? (
                      <tr><td colSpan={5} className="py-8 text-center text-slate-300">无逾期未交成员 🎉</td></tr>
                    ) : (
                      (data?.red_list ?? []).map((r, i) => (
                        <tr key={i} className="hover:bg-rose-50/40">
                          <td className="py-2 px-3 font-semibold text-slate-700">
                            {r.name}<span className="ml-1.5 text-[11px] text-slate-400 font-mono">{r.phone}</span>
                          </td>
                          <td className="py-2 px-3 text-slate-500">{r.dept || '—'}</td>
                          <td className="py-2 px-3 text-slate-600">{r.template_name}</td>
                          <td className="py-2 px-3 text-slate-500 font-mono">{fmtDeadline(r.deadline_at)}</td>
                          <td className="py-2 px-3 font-bold text-rose-600">
                            {r.overdue_hours >= 24
                              ? `${Math.floor(r.overdue_hours / 24)} 天 ${r.overdue_hours % 24} 小时`
                              : `${r.overdue_hours} 小时`}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default StatisticsView;
