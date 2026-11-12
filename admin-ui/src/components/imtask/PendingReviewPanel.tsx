import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Eye,
  Inbox,
  RefreshCw,
  X,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { PendingReviewItem } from '../../lib/types';

/* 2026-10-30 审核待办卡片
 *
 * 为什么单独做：实例列表的 period_key 是单日精确匹配，往日的待审实例在默认
 * 「今天」筛选下查不到，这个组件按「提交时刻」跨自然日聚合，不改动原有筛选器与列表。
 * 2026-11-12 任务E3：「申请补交」链路整体下线，原有「补交申请待审」一类行随之移除，
 * 只保留「用户提交待审」。
 */

interface PendingReviewsResponse {
  ok: boolean;
  days: number;
  since: string;
  limit: number;
  truncated: { submitted: boolean };
  submitted: PendingReviewItem[];
  total: number;
}

interface Props {
  /** 父组件刷新后递增，触发这里重新拉取 */
  refreshToken?: number;
  /** 当前办公软件平台（zdxlz / wecom） */
  platform?: string;
  /** 打开现有详情弹窗（完整看提交内容与附件） */
  onOpenDetail: (id: number) => void;
  /** 审核动作完成后通知父组件刷新主列表与统计卡片 */
  onChanged: () => void;
}

function fmtMD(ts?: string | null): string {
  const s = String(ts || '');
  // 'YYYY-MM-DD HH:MM:SS' → 'MM-DD HH:MM'
  return s.length >= 16 ? `${s.slice(5, 10)} ${s.slice(11, 16)}` : s || '—';
}

export default function PendingReviewPanel({ refreshToken = 0, platform = '', onOpenDetail, onChanged }: Props) {
  const toast = useToast();
  const { isSuperAdmin, ownerFilter } = useAppStore();
  const [data, setData] = useState<PendingReviewsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  /** 行内展开的驳回原因输入 */
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const ownerParam =
        isSuperAdmin && ownerFilter && ownerFilter !== 'all'
          ? `&owner_id=${encodeURIComponent(ownerFilter)}`
          : '';
      const res = await api.get<PendingReviewsResponse>(
        `/api/admin/instances/pending-reviews?days=7${platform ? `&platform=${encodeURIComponent(platform)}` : ''}${ownerParam}`,
      );
      setData(res);
    } catch (err) {
      // 待办卡片是辅助信息，拉取失败不打断主流程
      console.warn('待办拉取失败', err);
    } finally {
      setLoading(false);
    }
  }, [platform, isSuperAdmin, ownerFilter]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const removeRow = (id: number) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            submitted: prev.submitted.filter((x) => x.id !== id),
            total: Math.max(0, prev.total - 1),
          }
        : prev,
    );
  };

  const runAction = async (item: PendingReviewItem, fn: () => Promise<unknown>, okText: string) => {
    setBusyId(item.id);
    try {
      await fn();
      removeRow(item.id);
      setRejectingId(null);
      setRejectReason('');
      toast.success(okText, '已处理');
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '操作未成功');
    } finally {
      setBusyId(null);
    }
  };

  const approve = (item: PendingReviewItem) =>
    runAction(
      item,
      () => api.post(`/api/admin/instances/${item.id}/review`, { action: 'approve' }),
      `已通过「${item.member_name_snap}」提交的《${item.template_name}》。`,
    );

  const confirmReject = (item: PendingReviewItem) => {
    const reason = rejectReason.trim();
    if (reason.length < 2) {
      toast.warning('驳回原因至少 2 个字，请补充说明需要修改的地方。', '原因太短');
      return;
    }
    void runAction(
      item,
      () => api.post(`/api/admin/instances/${item.id}/review`, { action: 'reject', reason }),
      `已驳回「${item.member_name_snap}」的提交，系统会通知他修改后重交。`,
    );
  };

  const total = data?.total ?? 0;
  // 没有待办时整卡隐藏，不占空间
  if (!loading && total === 0) return null;
  if (!data && loading) return null;

  const renderRow = (item: PendingReviewItem) => {
    const busy = busyId === item.id;
    const rejecting = rejectingId === item.id;
    return (
      <div key={`submitted-${item.id}`} className="border-b border-slate-100 last:border-b-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-xs hover:bg-slate-50/70">
          <span className="font-bold text-slate-800 shrink-0">{item.member_name_snap}</span>
          <span className="font-mono text-slate-500 tabular-nums shrink-0">{item.phone}</span>
          <span className="text-slate-300">·</span>
          <span className="text-slate-700 font-medium truncate max-w-[200px]" title={item.template_name}>
            {item.template_name}
          </span>
          {isSuperAdmin && (item.owner_display_name || item.owner_username) && (
            <>
              <span className="text-slate-300">·</span>
              <span className="text-slate-500">
                创建人：{item.owner_display_name || item.owner_username}
              </span>
            </>
          )}
          <span className="text-slate-300">·</span>
          <span className="text-slate-500 tabular-nums">提交于 {fmtMD(item.submitted_at)}</span>
          <span className="text-slate-300">·</span>
          <span className="text-slate-500 tabular-nums">截止 {fmtMD(item.deadline_at)}</span>

          <span className="ml-auto flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => onOpenDetail(item.id)}
              className="px-2 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer"
            >
              <Eye className="w-3 h-3" />
              <span>查看</span>
            </button>
            {!isSuperAdmin && (
              <>
                <button
                  onClick={() => void approve(item)}
                  disabled={busy}
                  className="px-2 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-60"
                >
                  <Check className="w-3 h-3" />
                  <span>通过</span>
                </button>
                <button
                  onClick={() => {
                    setRejectingId(rejecting ? null : item.id);
                    setRejectReason('');
                  }}
                  disabled={busy}
                  className="px-2 py-1 rounded-lg bg-white hover:bg-rose-50 text-rose-600 border border-rose-200 font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-60"
                >
                  <X className="w-3 h-3" />
                  <span>驳回</span>
                </button>
              </>
            )}
          </span>
        </div>

        {/* 行内驳回：展开原因输入，不弹大窗 */}
        {rejecting && (
          <div className="flex flex-wrap items-center gap-2 px-3 pb-2.5 pt-0.5 bg-rose-50/40">
            <input
              autoFocus
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="请填写驳回原因（至少 2 个字），会随通知发给本人"
              className="flex-1 min-w-[220px] bg-white border border-rose-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-800 outline-none focus:border-rose-400"
            />
            <button
              onClick={() => confirmReject(item)}
              disabled={busy}
              className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold cursor-pointer disabled:opacity-60"
            >
              确认驳回
            </button>
            <button
              onClick={() => {
                setRejectingId(null);
                setRejectReason('');
              }}
              className="px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 text-xs font-semibold cursor-pointer"
            >
              取消
            </button>
          </div>
        )}
      </div>
    );
  };

  const subCount = data?.submitted.length ?? 0;

  return (
    <div className="bg-white rounded-2xl border border-amber-200/80 shadow-2xs overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-amber-50/60 border-b border-amber-100">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
          <span className="text-xs font-bold text-amber-900">审核待办（近 {data?.days ?? 7} 天）</span>
          <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-white text-amber-700 border border-amber-200 tabular-nums">
            共 {total} 条
          </span>
          <span className="text-[11px] text-slate-500">用户提交 {subCount}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => void load()}
            disabled={loading}
            className="px-2 py-1 rounded-lg text-[11px] font-semibold text-slate-600 bg-white border border-slate-200 hover:border-amber-300 inline-flex items-center gap-1 cursor-pointer disabled:opacity-60"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            <span>刷新</span>
          </button>
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="px-2 py-1 rounded-lg text-[11px] font-semibold text-slate-600 bg-white border border-slate-200 hover:border-amber-300 inline-flex items-center gap-1 cursor-pointer"
          >
            {collapsed ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
            <span>{collapsed ? '展开' : '收起'}</span>
          </button>
        </div>
      </div>

      {!collapsed && (
        <div>
          {subCount > 0 && (
            <>
              <div className="px-3 pt-2 pb-1 text-[11px] font-bold text-slate-500 flex items-center gap-1.5">
                <Clock className="w-3 h-3 text-blue-500" />
                <span>用户提交待审</span>
                {data?.truncated.submitted && (
                  <span className="text-amber-600 font-semibold">（仅显示最近 {data.limit} 条）</span>
                )}
              </div>
              {data?.submitted.map((it) => renderRow(it))}
            </>
          )}

          {total === 0 && (
            <div className="px-3 py-6 text-center text-slate-400 text-xs flex items-center justify-center gap-2">
              <Inbox className="w-4 h-4" />
              <span>近 {data?.days ?? 7} 天没有待审核的事项</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
