import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, plainError } from '../../lib/api';

/**
 * 2026-11-13 冲刺模块三：多维自适应统计看板。
 * · 四 KPI：应交总数 / 已交数 / 完成率 / 按时率（+ 平均提交耗时）；
 * · 三视图：报数（数值字段）/ 文本（说明）/ 附件，按提交人聚合；
 * · 督办红榜：过期未交按滞留时长倒序（催办名单）。
 * 权限在后端收敛：超管=全局只读；分级管理员自动 owner 隔离。
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

type ViewTab = 'numbers' | 'texts' | 'files';

function KpiCard({ label, value, sub, tone = 'slate' }: { label: string; value: string; sub?: string; tone?: string }) {
  const toneMap: Record<string, string> = {
    slate: 'bg-slate-800 border-slate-700',
    green: 'bg-emerald-900/40 border-emerald-700/60',
    amber: 'bg-amber-900/40 border-amber-700/60',
    red: 'bg-rose-900/40 border-rose-700/60',
    blue: 'bg-sky-900/40 border-sky-700/60',
  };
  return (
    <div className={`rounded-xl border p-4 ${toneMap[tone] || toneMap.slate}`}>
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
      {sub ? <div className="mt-1 text-xs text-slate-400">{sub}</div> : null}
    </div>
  );
}

export function StatisticsView() {
  const [data, setData] = useState<DashboardResp | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [viewTab, setViewTab] = useState<ViewTab>('numbers');
  const [periodKey, setPeriodKey] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const qs = new URLSearchParams();
      if (periodKey.trim()) qs.set('period_key', periodKey.trim());
      const res = await api.get<DashboardResp>(`/api/admin/stats/dashboard?${qs.toString()}`);
      setData(res);
    } catch (e) {
      setErr(plainError(String(e)));
    } finally {
      setLoading(false);
    }
  }, [periodKey]);

  useEffect(() => { void load(); }, [load]);

  const viewRows: Array<Record<string, unknown>> = useMemo(() => {
    if (!data) return [];
    if (viewTab === 'numbers') return data.views.numbers as unknown as Array<Record<string, unknown>>;
    if (viewTab === 'texts') return data.views.texts as unknown as Array<Record<string, unknown>>;
    return data.views.files as unknown as Array<Record<string, unknown>>;
  }, [data, viewTab]);

  const k = data?.kpis;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-white">统计看板</h2>
        <input
          value={periodKey}
          onChange={(e) => setPeriodKey(e.target.value)}
          placeholder="周期 2026-11-13（留空=全部）"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-500"
        />
        <button
          onClick={() => void load()}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
        {data?.scope_owner_id ? (
          <span className="text-xs text-slate-400">当前范围：管理员 #{data.scope_owner_id}（分级隔离）</span>
        ) : (
          <span className="text-xs text-slate-400">当前范围：全局（超管只读）</span>
        )}
      </div>

      {err ? <div className="rounded-lg border border-rose-700 bg-rose-900/30 p-3 text-sm text-rose-300">{err}</div> : null}

      {k ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <KpiCard label="应交总数" value={String(k.should_submit)} tone="slate" />
          <KpiCard label="已交数" value={String(k.submitted)} sub={`通过 ${k.approved} · 驳回 ${k.rejected}`} tone="blue" />
          <KpiCard label="完成率" value={`${k.completion_rate}%`} sub={k.avg_submit_minutes != null ? `平均耗时 ${k.avg_submit_minutes} 分钟` : undefined} tone="green" />
          <KpiCard label="按时率" value={`${k.on_time_rate}%`} sub={`迟交 ${k.late_submit} · 未交 ${k.pending}`} tone={k.on_time_rate >= 80 ? 'green' : 'amber'} />
        </div>
      ) : null}

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-white">提交内容三视图</h3>
          {([['numbers', '报数'], ['texts', '文本'], ['files', '附件']] as Array<[ViewTab, string]>).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setViewTab(id)}
              className={`rounded-lg px-2.5 py-1 text-xs ${
                viewTab === id ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {label}
              {data ? ` (${data.views[id].length})` : ''}
            </button>
          ))}
        </div>
        <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-slate-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-800/80 text-xs text-slate-400">
              <tr>
                <th className="px-3 py-2 text-left">成员</th>
                <th className="px-3 py-2 text-left">部门</th>
                <th className="px-3 py-2 text-left">{viewTab === 'numbers' ? '字段' : viewTab === 'texts' ? '内容' : '附件数'}</th>
                <th className="px-3 py-2 text-left">{viewTab === 'numbers' ? '数值' : viewTab === 'texts' ? '' : '周期'}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-300">
              {viewRows.length === 0 ? (
                <tr><td colSpan={4} className="px-3 py-6 text-center text-slate-500">暂无数据</td></tr>
              ) : viewRows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-800/50">
                  <td className="px-3 py-2 whitespace-nowrap">{String(r.name)}<span className="ml-2 text-xs text-slate-500">{String(r.phone)}</span></td>
                  <td className="px-3 py-2 whitespace-nowrap">{String(r.dept || '-')}</td>
                  {viewTab === 'numbers' ? (
                    <>
                      <td className="px-3 py-2">{String(r.field)}</td>
                      <td className="px-3 py-2 font-medium text-emerald-300">{String(r.value)}</td>
                    </>
                  ) : viewTab === 'texts' ? (
                    <>
                      <td className="px-3 py-2 max-w-md truncate" title={String(r.text)}>{String(r.text)}</td>
                      <td className="px-3 py-2 text-xs text-slate-500">{String(r.status)}</td>
                    </>
                  ) : (
                    <>
                      <td className="px-3 py-2 font-medium text-sky-300">{String(r.file_count)} 个</td>
                      <td className="px-3 py-2 text-xs text-slate-500">{String(r.period_key)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-xl border border-rose-800/60 bg-rose-950/20 p-4">
        <h3 className="text-sm font-semibold text-rose-200">督办红榜（过期未交 {data?.red_list.length ?? 0}）</h3>
        <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-slate-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-800/80 text-xs text-slate-400">
              <tr>
                <th className="px-3 py-2 text-left">成员</th>
                <th className="px-3 py-2 text-left">部门</th>
                <th className="px-3 py-2 text-left">任务</th>
                <th className="px-3 py-2 text-left">截止时间</th>
                <th className="px-3 py-2 text-left">滞留时长</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-300">
              {(data?.red_list ?? []).length === 0 ? (
                <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-500">无逾期未交成员 🎉</td></tr>
              ) : (data?.red_list ?? []).map((r, i) => (
                <tr key={i} className="hover:bg-rose-900/20">
                  <td className="px-3 py-2 whitespace-nowrap">{r.name}<span className="ml-2 text-xs text-slate-500">{r.phone}</span></td>
                  <td className="px-3 py-2 whitespace-nowrap">{r.dept || '-'}</td>
                  <td className="px-3 py-2">{r.template_name}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-400">{r.deadline_at}</td>
                  <td className="px-3 py-2 font-medium text-rose-300">
                    {r.overdue_hours >= 24 ? `${Math.floor(r.overdue_hours / 24)} 天 ${r.overdue_hours % 24} 小时` : `${r.overdue_hours} 小时`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default StatisticsView;
