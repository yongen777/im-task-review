import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Eye,
  Download,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
  Paperclip,
  Send,
  X,
  Check,
  Bell,
  Smartphone,
  Users,
  Calendar,
  Search,
  ListFilter,
} from 'lucide-react';
import PendingReviewPanel from './PendingReviewPanel';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { humanizeReminderLogError, useToast } from '../../lib/toast';
import {
  InstanceStats,
  TaskInstanceDetail,
  TaskInstanceItem,
} from '../../lib/types';

const PAGE_SIZE = 20;

function getTodayDateString(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function getInstanceStatusMeta(inst: TaskInstanceItem): {
  label: string;
  className: string;
  icon: React.ReactNode;
} {
  // 纯通知提醒任务：只区分「待提醒」与「已提醒」（2026-10-28 方案A：notice 也有实例了）
  if (inst.kind === 'notice') {
    if (inst.status === 'sent') {
      return {
        label: '已提醒',
        className: 'bg-blue-50 text-blue-700 border-blue-200',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-blue-600 shrink-0" />,
      };
    }
    if (inst.status === 'approved' || inst.submitted_at) {
      return {
        label: '已提醒',
        className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
      };
    }
    if (inst.status === 'expired') {
      return {
        label: '未送达',
        className: 'bg-slate-100 text-slate-500 border-slate-200',
        icon: <AlertTriangle className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
      };
    }
    return {
      label: '待提醒',
      className: 'bg-slate-100 text-slate-600 border-slate-200',
      icon: <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
    };
  }

  // 2026-10-29 逾期补交审核制：补交审核状态优先展示（叠加在「已逾期」之上）
  if (inst.resubmit_status === 'pending') {
    return {
      label: '补交审核中',
      className: 'bg-violet-50 text-violet-700 border-violet-200',
      icon: <Clock className="w-3.5 h-3.5 text-violet-600 shrink-0" />,
    };
  }
  if (inst.resubmit_status === 'approved' && (inst.status === 'overdue' || inst.status === 'expired')) {
    return {
      label: '补交已通过',
      className: 'bg-teal-50 text-teal-700 border-teal-200',
      icon: <CheckCircle2 className="w-3.5 h-3.5 text-teal-600 shrink-0" />,
    };
  }
  if (inst.resubmit_status === 'rejected' && inst.status === 'overdue') {
    return {
      label: '补交已拒绝',
      className: 'bg-rose-50 text-rose-700 border-rose-200',
      icon: <XCircle className="w-3.5 h-3.5 text-rose-600 shrink-0" />,
    };
  }

  // 上传任务（upload / form / image / file）
  switch (inst.status) {
    case 'submitted':
      return {
        label: '待审核',
        className: 'bg-blue-50 text-blue-700 border-blue-200',
        icon: <Clock className="w-3.5 h-3.5 text-blue-600 shrink-0" />,
      };
    case 'approved':
      return {
        /* 2026-10-30 用户口径：不需要审核的任务，提交成功即「已完成」；
           需要审核的，审核通过后才是「审核通过」。
           注意判据是 need_review（后端下发），原来判的 require_review 前端从未被赋值，
           导致所有免审任务都错误显示成「审核通过」。 */
        label: inst.need_review ? '审核通过' : '已完成',
        className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
      };
    case 'rejected':
      return {
        label: '已驳回',
        className: 'bg-rose-50 text-rose-700 border-rose-200',
        icon: <XCircle className="w-3.5 h-3.5 text-rose-600 shrink-0" />,
      };
    case 'overdue':
      return {
        label: '已逾期',
        className: 'bg-amber-50 text-amber-800 border-amber-200',
        icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />,
      };
    default:
      return {
        label: '待上传',
        className: 'bg-slate-100 text-slate-600 border-slate-200',
        icon: <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
      };
  }
}

/* 2026-10-29：执行时间统一在这里算——列表和详情共用一份，避免两处再走偏。
   规则：取模板计划时刻（通知类可能是多个）；已到达的加粗，未到达的置灰。 */
function parseNoticeTimes(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String).filter(Boolean);
  if (typeof v === 'string' && v.trim()) {
    try {
      const arr = JSON.parse(v);
      return Array.isArray(arr) ? arr.map(String).filter(Boolean) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function instancePlanSlots(inst: TaskInstanceItem): string[] {
  if (inst.kind === 'notice') {
    const list = parseNoticeTimes(inst.template_notice_times);
    if (list.length > 0) return list;
  }
  return inst.template_remind_at ? [inst.template_remind_at] : [];
}

function slotReached(inst: TaskInstanceItem, hhmm: string): boolean {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(inst.period_key || '');
  const t = /^(\d{1,2}):(\d{2})$/.exec(hhmm || '');
  if (!d || !t) return true; // 解析不出来就按「已到达」显示，不要误导
  const at = new Date(+d[1], +d[2] - 1, +d[3], +t[1], +t[2], 0, 0).getTime();
  return at <= Date.now();
}

const InstancePlanTime: React.FC<{ inst: TaskInstanceItem; className?: string }> = ({ inst, className = '' }) => {
  const slots = instancePlanSlots(inst);
  if (slots.length === 0) return <span className="text-slate-400">—</span>;
  const pk = inst.period_key || '';
  const datePart = /^\d{4}-\d{2}-\d{2}$/.test(pk) ? pk.slice(5) : pk;
  return (
    <span className={`inline-flex flex-wrap items-baseline gap-x-1.5 ${className}`}>
      {slots.map((s, i) => {
        const reached = slotReached(inst, s);
        return (
          <span
            key={`${s}-${i}`}
            className={reached ? 'font-bold text-slate-900' : 'font-normal text-slate-400'}
            title={reached ? '已到达执行时刻' : '尚未到达执行时刻'}
          >
            {datePart} {s}
            {i < slots.length - 1 ? ' /' : ''}
          </span>
        );
      })}
    </span>
  );
};

export const InstancesView: React.FC = () => {  const { roles, refreshStats } = useAppStore();
  const toast = useToast();

  // 筛选条件：按日期（默认今天）、按角色、按任务类型、按执行状态、按用户名/手机号
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [phoneFilter, setPhoneFilter] = useState<string>('');
  const [periodKeyFilter, setPeriodKeyFilter] = useState<string>(() => getTodayDateString());

  /* 2026-10-28 新增：任务名称筛选（下拉 + 内置搜索）
     —— 模板多了以后纯下拉翻不动，所以做成「可搜索的 combobox」，选中即筛选 template_id。 */
  const [templateFilter, setTemplateFilter] = useState<string>('');
  const [templateLabel, setTemplateLabel] = useState<string>('');
  const [tplPickerOpen, setTplPickerOpen] = useState<boolean>(false);
  const [tplKeyword, setTplKeyword] = useState<string>('');
  const [templateOptions, setTemplateOptions] = useState<Array<{ id: string; name: string; kind: string }>>([]);

  // 分页状态 (P1-4: page & size=20)
  const [page, setPage] = useState<number>(1);
  const [total, setTotal] = useState<number>(0);
  const [items, setItems] = useState<TaskInstanceItem[]>([]);
  const [localStats, setLocalStats] = useState<InstanceStats | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  // P1-1: 驳回原因弹窗状态
  const [rejectTarget, setRejectTarget] = useState<TaskInstanceItem | null>(null);
  const [rejectReason, setRejectReason] = useState<string>('');
  const [submittingReject, setSubmittingReject] = useState<boolean>(false);

  // P2-2: 实例详情弹窗状态
  const [detailData, setDetailData] = useState<TaskInstanceDetail | null>(null);
  const [loadingDetailId, setLoadingDetailId] = useState<number | null>(null);

  const roleMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of roles) {
      map[r.role_id] = r.role_name;
    }
    return map;
  }, [roles]);

  /* 2026-10-30 用户反馈：详情里角色一直显示成固定的「业务角色」。
     根因是详情接口没带 role_id（已补），同时这里的兜底也不该编一个假名字 ——
     多角色逐个查名，查不到就显示「—」，让人一眼看出是角色被删了而不是"叫业务角色"。 */
  const roleLabel = useCallback(
    (inst: { role_id?: string; role_ids?: string[] }) => {
      const ids = (
        Array.isArray(inst.role_ids) && inst.role_ids.length
          ? inst.role_ids
          : [inst.role_id || '']
      ).filter(Boolean) as string[];
      const names = ids.map((id) => roleMap[id]).filter(Boolean);
      return names.length ? names.join('、') : '—';
    },
    [roleMap],
  );

  const loadData = useCallback(
    async (
      targetPage = page,
      overrides?: {
        status?: string;
        kind?: string;
        role?: string;
        phone?: string;
        periodKey?: string;
        templateId?: string;
      },
    ) => {
      setLoading(true);
      try {
        const st = overrides?.status !== undefined ? overrides.status : statusFilter;
        const kd = overrides?.kind !== undefined ? overrides.kind : kindFilter;
        const rl = overrides?.role !== undefined ? overrides.role : roleFilter;
        const ph = (overrides?.phone !== undefined ? overrides.phone : phoneFilter).trim();
        const pk = (overrides?.periodKey !== undefined ? overrides.periodKey : periodKeyFilter).trim();
        const tid = overrides?.templateId !== undefined ? overrides.templateId : templateFilter;

        const qs = new URLSearchParams();
        qs.set('page', String(targetPage));
        qs.set('size', String(PAGE_SIZE));
        // 2026-10-29：'resubmit_pending' 是前端虚拟筛选项，映射成 resubmit_status=pending
        const applyStatus = (target: URLSearchParams) => {
          if (!st) return;
          if (st === 'resubmit_pending') target.set('resubmit_status', 'pending');
          else target.set('status', st);
        };
        applyStatus(qs);
        if (kd) qs.set('kind', kd);
        if (rl) qs.set('role_id', rl);
        if (ph) qs.set('q', ph);
        if (pk) qs.set('period_key', pk);
        if (tid) qs.set('template_id', tid);

        const statsQs = new URLSearchParams();
        applyStatus(statsQs);
        if (kd) statsQs.set('kind', kd);
        if (rl) statsQs.set('role_id', rl);
        if (ph) statsQs.set('q', ph);
        if (pk) statsQs.set('period_key', pk);
        if (tid) statsQs.set('template_id', tid);

        const [listRes, statRes] = await Promise.all([
          api.get<{ ok: boolean; total: number; page: number; size: number; items: TaskInstanceItem[] }>(
            `/api/admin/instances?${qs.toString()}`,
          ),
          api.get<InstanceStats>(`/api/admin/instances/stats?${statsQs.toString()}`),
        ]);
        setItems(listRes.items || []);
        setTotal(listRes.total || 0);
        setPage(listRes.page || targetPage);
        setLocalStats(statRes);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载任务实例列表失败');
      } finally {
        setLoading(false);
      }
    },
    [page, statusFilter, kindFilter, roleFilter, phoneFilter, periodKeyFilter, templateFilter],
  );

  useEffect(() => {
    loadData(1);
  }, [statusFilter, kindFilter, roleFilter, periodKeyFilter, templateFilter]);

  /* 2026-10-28：关键字搜索改为实时（防抖 400ms），取消「查询」按钮 */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      loadData(1);
    }, 400);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phoneFilter]);

  /* 任务名称候选：进页面拉一次模板列表（含已停用的历史模板，才能查到旧实例） */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await api.get<{ ok: boolean; items: Array<{ id: number | string; name: string; kind: string }> }>(
          '/api/admin/templates?size=200',
        );
        if (!alive) return;
        const list = (res.items || []).map((t) => ({
          id: String(t.id ?? ''),
          name: String(t.name || ''),
          kind: String(t.kind || ''),
        }));
        list.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'));
        setTemplateOptions(list);
      } catch {
        /* 候选拉取失败不阻断主列表 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  /* 名称下拉的内部搜索：按关键字即时过滤 */
  const filteredTemplateOptions = useMemo(() => {
    const kw = tplKeyword.trim().toLowerCase();
    if (!kw) return templateOptions;
    return templateOptions.filter((o) => o.name.toLowerCase().includes(kw));
  }, [templateOptions, tplKeyword]);

  const handlePickTemplate = (opt: { id: string; name: string } | null) => {
    const id = opt ? opt.id : '';
    setTemplateFilter(id);
    setTemplateLabel(opt ? opt.name : '');
    setTplPickerOpen(false);
    setTplKeyword('');
    setPage(1);
    loadData(1, { templateId: id });
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    loadData(1);
  };

  const handleResetFilters = () => {
    const todayStr = getTodayDateString();
    setStatusFilter('');
    setKindFilter('');
    setRoleFilter('');
    setPhoneFilter('');
    setPeriodKeyFilter(todayStr);
    setTemplateFilter('');
    setTemplateLabel('');
    setTplKeyword('');
    setTplPickerOpen(false);
    setPage(1);
    loadData(1, {
      status: '',
      kind: '',
      role: '',
      phone: '',
      periodKey: todayStr,
      templateId: '',
    });
    toast.info('已重置所有筛选条件（日期已恢复为今天）。', '筛选已重置');
  };

  // 审核通过（仅针对需要审核的上传类任务）
  const handleApprove = async (inst: TaskInstanceItem) => {
    setBusyId(inst.id);
    try {
      await api.post(`/api/admin/instances/${inst.id}/review`, {
        action: 'approve',
      });
      await Promise.all([loadData(page), refreshStats()]);
      if (detailData && detailData.instance.id === inst.id) {
        const refreshed = await api.get<TaskInstanceDetail>(`/api/admin/instances/${inst.id}`);
        setDetailData(refreshed);
      }
      toast.success(
        `已审核通过「${inst.member_name_snap}」提交的《${inst.template_name}》。`,
        '审核已通过',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '审核通过操作失败');
    } finally {
      setBusyId(null);
    }
  };

  /* 2026-10-29 逾期补交审核：通过 → 后端签发 30 分钟补交链接并推送；拒绝 → 推送拒绝文案 */
  const reloadDetailIfOpen = async (id: number) => {
    if (detailData && detailData.instance.id === id) {
      const refreshed = await api.get<TaskInstanceDetail>(`/api/admin/instances/${id}`);
      setDetailData(refreshed);
    }
  };

  const handleResubmitApprove = async (inst: TaskInstanceItem) => {
    setBusyId(inst.id);
    try {
      const res = await api.post<{ ok: boolean; sent: boolean; expires_at?: string }>(
        `/api/admin/instances/${inst.id}/resubmit/approve`,
        {},
      );
      await Promise.all([loadData(page), refreshStats()]);
      await reloadDetailIfOpen(inst.id);
      const until = String(res.expires_at || '').slice(11, 16);
      if (res.sent) {
        toast.success(`已通过，补交链接已发送（${until} 前有效，30 分钟）`, '补交申请已通过');
      } else {
        toast.warning(
          '已通过，但链接没能推送出去（该成员可能未完成绑定），请人工通知。',
          '已通过，推送失败',
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '补交审核操作失败');
    } finally {
      setBusyId(null);
    }
  };

  const handleResubmitReject = async (inst: TaskInstanceItem) => {
    if (
      !window.confirm(
        `确定拒绝「${inst.member_name_snap}」的补交申请？\n拒绝后该实例不能再申请补交。`,
      )
    ) {
      return;
    }
    setBusyId(inst.id);
    try {
      const res = await api.post<{ ok: boolean; sent: boolean }>(
        `/api/admin/instances/${inst.id}/resubmit/reject`,
        {},
      );
      await Promise.all([loadData(page), refreshStats()]);
      await reloadDetailIfOpen(inst.id);
      if (res.sent) {
        toast.success('已拒绝该补交申请，并已通知成员。', '补交申请已拒绝');
      } else {
        toast.warning('已拒绝，但通知没能发出去（该成员可能未完成绑定）。', '已拒绝，推送失败');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '补交审核操作失败');
    } finally {
      setBusyId(null);
    }
  };

  // P1-1: 打开驳回原因弹窗
  const openRejectModal = (inst: TaskInstanceItem) => {
    setRejectTarget(inst);
    setRejectReason('');
  };

  // P1-1: 提交驳回（必填原因）
  const handleConfirmReject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rejectTarget) return;
    const trimmed = rejectReason.trim();
    if (!trimmed) {
      toast.warning('请填写具体的驳回原因，原因不能为空。', '需要填写驳回原因');
      return;
    }
    if (trimmed.length < 2) {
      toast.warning('驳回原因至少需要填写 2 个字，请补充说明需要修改的地方。', '原因字数不足');
      return;
    }

    setSubmittingReject(true);
    try {
      await api.post(`/api/admin/instances/${rejectTarget.id}/review`, {
        action: 'reject',
        reason: trimmed,
      });
      const targetSnap = rejectTarget;
      setRejectTarget(null);
      setRejectReason('');
      await Promise.all([loadData(page), refreshStats()]);
      if (detailData && detailData.instance.id === targetSnap.id) {
        const refreshed = await api.get<TaskInstanceDetail>(`/api/admin/instances/${targetSnap.id}`);
        setDetailData(refreshed);
      }
      toast.success(
        `已驳回「${targetSnap.member_name_snap}」的提交，系统将通知员工按原因修改后重交。`,
        '任务已驳回',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '驳回操作未成功');
    } finally {
      setSubmittingReject(false);
    }
  };

  // P2-2: 打开实例详情弹窗
  const handleOpenDetail = async (instId: number) => {
    setLoadingDetailId(instId);
    try {
      const res = await api.get<TaskInstanceDetail>(`/api/admin/instances/${instId}`);
      setDetailData(res);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '读取实例详情失败');
    } finally {
      setLoadingDetailId(null);
    }
  };

  /* 2026-10-30 审核待办：动作完成后同时刷新待办卡片、主列表与统计卡片 */
  const [pendingRefresh, setPendingRefresh] = useState(0);
  const handlePendingChanged = useCallback(() => {
    setPendingRefresh((n) => n + 1);
    void Promise.all([loadData(page), refreshStats()]);
  }, [loadData, page, refreshStats]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* 顶部标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-5 rounded-2xl border border-slate-200/90 shadow-2xs">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">任务实例与审核</h2>
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/60">
              执行跟踪与上传审核
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            支持按任务类型（通知提醒 / 上传任务）筛选查看执行记录，对需审核的上传任务进行通过或驳回。
          </p>
        </div>
      </div>

      {/* 统计指标卡片 */}
      {localStats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <div
            onClick={() => setStatusFilter('')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === ''
                ? 'bg-blue-50/60 border-blue-300 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-slate-300'
            }`}
          >
            <div className="text-xs text-slate-500 font-medium">全部任务实例</div>
            <div className="text-xl font-bold text-slate-900 font-mono mt-1">{localStats.total}</div>
          </div>

          <div
            onClick={() => setStatusFilter('approved,sent')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'approved,sent'
                ? 'bg-emerald-50/70 border-emerald-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-emerald-200'
            }`}
          >
            <div className="text-xs text-emerald-700 font-semibold">已提醒 / 已完成</div>
            <div className="text-xl font-bold text-emerald-600 font-mono mt-1">
              {(localStats.by_status.approved || 0) + (localStats.by_status.sent || 0)}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('pending')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'pending'
                ? 'bg-slate-100 border-slate-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-slate-300'
            }`}
          >
            <div className="text-xs text-slate-600 font-medium">待提醒 / 待上传</div>
            <div className="text-xl font-bold text-slate-700 font-mono mt-1">
              {localStats.by_status.pending || 0}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('submitted')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'submitted'
                ? 'bg-blue-50/80 border-blue-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-blue-200'
            }`}
          >
            <div className="text-xs text-blue-700 font-semibold">待人工审核</div>
            <div className="text-xl font-bold text-blue-600 font-mono mt-1">
              {localStats.by_status.submitted || 0}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('rejected,overdue')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'rejected,overdue'
                ? 'bg-rose-50/70 border-rose-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-rose-200'
            }`}
          >
            <div className="text-xs text-rose-700 font-medium">已驳回 / 已逾期</div>
            <div className="text-xl font-bold text-rose-600 font-mono mt-1">
              {(localStats.by_status.rejected || 0) + (localStats.by_status.overdue || 0)}
            </div>
          </div>
        </div>
      )}

      {/* 2026-10-30 审核待办：跨日期聚合「用户提交待审 + 补交申请待审」，无待办时整卡不渲染 */}
      <PendingReviewPanel
        refreshToken={pendingRefresh}
        onOpenDetail={handleOpenDetail}
        onChanged={handlePendingChanged}
      />

      {/* 筛选工具栏 + 表格 + 分页 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
        <form
          onSubmit={handleSearch}
          className="p-4 border-b border-slate-100 flex flex-wrap items-center gap-2.5 text-xs"
        >
          {/* 0. 按日期选择（默认今天） */}
          <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
            <Calendar className="w-3.5 h-3.5 text-blue-600 shrink-0" />
            <span className="text-slate-500 font-medium shrink-0">日期</span>
            <input
              type="date"
              aria-label="选择任务日期"
              value={periodKeyFilter}
              onChange={(e) => setPeriodKeyFilter(e.target.value)}
              className="bg-transparent font-mono text-slate-800 font-semibold outline-none cursor-pointer"
            />
          </div>

          {/* 0.5 按任务名称查询（下拉 + 内置搜索，2026-10-28 新增） */}
          <div className="relative">
            <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
              <ListFilter className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              <input
                aria-label="按任务名称筛选"
                value={tplPickerOpen ? tplKeyword : templateLabel}
                placeholder="全部任务名称"
                onChange={(e) => {
                  setTplKeyword(e.target.value);
                  if (!tplPickerOpen) setTplPickerOpen(true);
                }}
                onFocus={() => {
                  setTplPickerOpen(true);
                  setTplKeyword('');
                }}
                onBlur={() => window.setTimeout(() => setTplPickerOpen(false), 150)}
                className="bg-transparent text-slate-700 font-medium outline-none w-36 placeholder:text-slate-400"
              />
              {templateFilter && (
                <button
                  type="button"
                  aria-label="清除任务名称筛选"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => handlePickTemplate(null)}
                  className="text-slate-400 hover:text-rose-500 cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {tplPickerOpen && (
              <div className="absolute left-0 top-full z-30 mt-1 w-64 max-h-64 overflow-auto bg-white border border-slate-200 rounded-xl shadow-lg py-1">
                <div className="px-3 py-1.5 text-[10px] text-slate-400 font-semibold flex items-center gap-1">
                  <Search className="w-3 h-3" /> 输入关键字即可搜索任务名称
                </div>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => handlePickTemplate(null)}
                  className="w-full text-left px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 cursor-pointer"
                >
                  全部任务名称
                </button>
                {filteredTemplateOptions.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => handlePickTemplate(o)}
                    className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 cursor-pointer flex items-center justify-between gap-2 ${
                      templateFilter === o.id ? 'text-blue-700 font-semibold' : 'text-slate-700'
                    }`}
                  >
                    <span className="truncate">{o.name}</span>
                    <span className="shrink-0 text-[10px] text-slate-400">
                      {o.kind === 'notice' ? '通知' : '上传'}
                    </span>
                  </button>
                ))}
                {filteredTemplateOptions.length === 0 && (
                  <div className="px-3 py-2 text-xs text-slate-400">没有匹配的任务名称</div>
                )}
              </div>
            )}
          </div>

          {/* 1. 按角色查询 */}
          <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
            <Users className="w-3.5 h-3.5 text-slate-400" />
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
              className="bg-transparent text-slate-700 font-medium outline-none cursor-pointer"
            >
              <option value="">全部业务角色</option>
              {roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>
                  {r.role_name}
                </option>
              ))}
            </select>
          </div>

          {/* 2. 按任务类型查询（替换原按任务模板查询） */}
          <select
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-700 font-medium outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
          >
            <option value="">全部任务类型</option>
            <option value="notice">通知提醒</option>
            <option value="upload">上传任务（H5链接反馈）</option>
          </select>

          {/* 3. 按状态查询（已移除已过期 expired） */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-700 font-medium outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
          >
            <option value="">全部执行状态</option>
            <option value="pending">待提醒 / 待上传</option>
            <option value="sent">已提醒（通知类已送达）</option>
            <option value="approved">已完成（审核通过）</option>
            <option value="submitted">待人工审核</option>
            <option value="rejected">已驳回待重交</option>
            <option value="overdue">已逾期未交</option>
            <option value="approved,sent">已提醒 / 已完成</option>
            <option value="rejected,overdue">已驳回 / 已逾期</option>
            <option value="resubmit_pending">补交待审核</option>
          </select>

          <input
            type="text"
            value={phoneFilter}
            onChange={(e) => setPhoneFilter(e.target.value)}
            placeholder="搜索用户名或手机号…"
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44"
          />

          <button
            type="button"
            onClick={handleResetFilters}
            className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium rounded-xl inline-flex items-center gap-1 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>重置</span>
          </button>
        </form>

        {/* 实例列表表格 */}
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50/80 border-b border-slate-200/80 text-[11px] font-bold text-slate-500">
                <th className="py-3.5 px-4 whitespace-nowrap">任务名称</th>
                <th className="py-3.5 px-3 whitespace-nowrap">任务类型</th>
                <th className="py-3.5 px-3 whitespace-nowrap">所属角色</th>
                <th className="py-3.5 px-3 whitespace-nowrap">执行人</th>
                <th className="py-3.5 px-3 whitespace-nowrap">执行时间</th>
                <th className="py-3.5 px-3 whitespace-nowrap">当前状态</th>
                <th className="py-3.5 px-4 text-right whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-xs">
              {loading ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-400">
                    正在加载任务实例数据…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-400">
                    {kindFilter === 'notice' ? (
                      <div className="space-y-1">
                        <div className="font-semibold text-slate-500">该日期暂无通知提醒实例</div>
                        <div className="text-[11px]">
                          通知提醒实例会在当天首次调度时按人生成（状态「待提醒」），到点发送后变「已提醒」；
                          若模板当天被停用或成员为空则不会生成。
                        </div>
                      </div>
                    ) : (
                      '当前筛选条件下暂无任务实例。'
                    )}
                  </td>
                </tr>
              ) : (
                items.map((inst) => {
                  const statusMeta = getInstanceStatusMeta(inst);
                  const roleName = roleLabel(inst);
                  const isNotice = inst.kind === 'notice';

                  return (
                    <tr key={inst.id} className="hover:bg-slate-50/60 transition-colors">
                      {/* 1. 任务名称（2026-10-28 去掉前面的实例序号徽标，业务方要求不要） */}
                      <td className="py-4 px-4 whitespace-nowrap">
                        <span className="font-bold text-slate-900 text-xs">{inst.template_name}</span>
                      </td>

                      {/* 2. 任务类型 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        {isNotice ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-amber-50 text-amber-700 border-amber-200 text-xs font-semibold">
                            <Bell className="w-3.5 h-3.5 shrink-0" />
                            <span>通知提醒</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-blue-50 text-blue-700 border-blue-200 text-xs font-semibold">
                            <Smartphone className="w-3.5 h-3.5 shrink-0" />
                            <span>上传任务</span>
                          </span>
                        )}
                      </td>

                      {/* 3. 所属角色 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-700 font-semibold text-xs">
                          <Users className="w-3.5 h-3.5 text-blue-600 shrink-0" />
                          <span>{roleName}</span>
                        </span>
                      </td>

                      {/* 4. 执行人（用户名 + 手机号单行展示） */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-slate-900">{inst.member_name_snap}</span>
                          <span className="font-mono text-slate-500">{inst.phone}</span>
                        </div>
                      </td>

                      {/* 5. 执行时间：模板计划时刻，已到达加粗、未到达置灰 */}
                      <td className="py-4 px-3 font-mono text-xs whitespace-nowrap">
                        <InstancePlanTime inst={inst} />
                      </td>

                      {/* 6. 当前状态 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-semibold ${statusMeta.className}`}
                          >
                            {statusMeta.icon}
                            <span>{statusMeta.label}</span>
                          </span>
                          {inst.reject_reason && (
                            <span className="text-[11px] text-rose-600 font-medium truncate max-w-[160px]">
                              原因：{inst.reject_reason}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* 7. 操作列：2026-10-28 按业务方要求只留「详情」，
                             待提醒/通知已送达/通过/驳回等统统挪进详情弹窗里操作 */}
                      <td className="py-4 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-2">
                          <button
                            onClick={() => handleOpenDetail(inst.id)}
                            disabled={loadingDetailId === inst.id}
                            className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                          >
                            <Eye className="w-3.5 h-3.5 shrink-0" />
                            <span>{loadingDetailId === inst.id ? '加载中…' : '详情'}</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* P1-4: 分页控件（上一页 / 下一页 + 当前页/总页，size=20） */}
        <div className="px-4 py-3 bg-slate-50/70 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="text-slate-500">
            共 <strong className="text-slate-800">{total}</strong> 条任务实例 · 每页{' '}
            <strong className="text-slate-800">{PAGE_SIZE}</strong> 条
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                if (page > 1) loadData(page - 1);
              }}
              disabled={page <= 1 || loading}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              <span>上一页</span>
            </button>
            <span className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 font-mono text-slate-700">
              第 <strong>{page}</strong> / <strong>{totalPages}</strong> 页
            </span>
            <button
              onClick={() => {
                if (page < totalPages) loadData(page + 1);
              }}
              disabled={page >= totalPages || loading}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <span>下一页</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* P1-1: 驳回原因弹窗 */}
      {rejectTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">驳回上传任务</h3>
                <p className="text-[11px] text-slate-400">
                  实例 #{rejectTarget.id} · {rejectTarget.member_name_snap} ({rejectTarget.phone})
                </p>
              </div>
              <button
                onClick={() => setRejectTarget(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleConfirmReject} className="space-y-3.5 text-xs">
              <div className="p-3 rounded-xl bg-rose-50/70 border border-rose-200/80 text-rose-800 leading-relaxed">
                驳回后，该任务状态将变更为「已驳回」，员工打开原 H5 链接可看到您填写的驳回原因并重新上传提交。
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1.5">
                  驳回原因说明 <span className="text-rose-500">*（必填，至少 2 个字）</span>
                </label>
                <textarea
                  rows={4}
                  autoFocus
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="请具体说明不通过的原因及重交要求…"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-slate-800 outline-none focus:bg-white focus:border-rose-500 leading-relaxed"
                />
              </div>

              <div className="pt-1 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRejectTarget(null)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={submittingReject || !rejectReason.trim()}
                  className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-50"
                >
                  {submittingReject ? '正在提交…' : '确认驳回'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* P2-2: 实例详情弹窗 */}
      {detailData && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-slate-200 my-8 space-y-5 max-h-[90vh] overflow-y-auto text-xs">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-slate-400">#{detailData.instance.id}</span>
                  <h3 className="font-bold text-slate-900 text-base">
                    {detailData.instance.template_name}
                  </h3>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${
                      getInstanceStatusMeta(detailData.instance).className
                    }`}
                  >
                    {getInstanceStatusMeta(detailData.instance).label}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  执行日期：{detailData.instance.period_key} · 接收人：
                  {detailData.instance.member_name_snap} ({detailData.instance.phone}) ·{' '}
                  {roleLabel(detailData.instance)}
                </p>
              </div>
              <button
                onClick={() => setDetailData(null)}
                className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 1. 基础信息卡片（接收人带上角色） */}
            <div
              className={`grid grid-cols-2 ${
                detailData.instance.kind === 'notice' ? 'sm:grid-cols-3' : 'sm:grid-cols-4'
              } gap-3 bg-slate-50 p-3.5 rounded-xl border border-slate-200/70`}
            >
              <div>
                <div className="text-[11px] text-slate-400">接收人（含角色）</div>
                <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                  <span className="font-bold text-slate-800">
                    {detailData.instance.member_name_snap}
                  </span>
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/70 text-[11px] font-semibold">
                    <Users className="w-3 h-3 shrink-0" />
                    <span>{roleLabel(detailData.instance)}</span>
                  </span>
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-400">手机号码</div>
                <div className="font-mono font-semibold text-slate-800 mt-0.5">
                  {detailData.instance.phone}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-400">执行时间</div>
                <div className="font-mono text-slate-700 mt-0.5">
                  <InstancePlanTime inst={detailData.instance} />
                </div>
              </div>
              {detailData.instance.submitted_at && (
                <div>
                  <div className="text-[11px] text-slate-400">提交时间</div>
                  <div className="font-mono text-slate-700 mt-0.5">
                    {detailData.instance.submitted_at}
                  </div>
                </div>
              )}
              {detailData.instance.kind !== 'notice' && (
                <div>
                  <div className="text-[11px] text-slate-400">截止时间</div>
                  <div className="font-mono text-slate-700 mt-0.5">
                    {detailData.instance.deadline_at || '—'}
                  </div>
                </div>
              )}
            </div>

            {detailData.instance.reject_reason && (
              <div className="p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-800">
                <div className="font-bold mb-1">最近一次驳回原因：</div>
                <div>{detailData.instance.reject_reason}</div>
              </div>
            )}

            {/* 2026-10-29 逾期补交申请记录（只读展示 + 底部审核按钮） */}
            {detailData.instance.resubmit_status && (
              <div
                className={`p-3.5 rounded-xl border text-xs ${
                  detailData.instance.resubmit_status === 'pending'
                    ? 'bg-violet-50 border-violet-200 text-violet-800'
                    : detailData.instance.resubmit_status === 'approved'
                      ? 'bg-teal-50 border-teal-200 text-teal-800'
                      : 'bg-rose-50 border-rose-200 text-rose-800'
                }`}
              >
                <div className="font-bold mb-1">
                  补交申请：
                  {detailData.instance.resubmit_status === 'pending'
                    ? '待审核'
                    : detailData.instance.resubmit_status === 'approved'
                      ? '已通过'
                      : '已拒绝'}
                </div>
                <div className="space-y-0.5 text-[11px]">
                  <div>申请时间：{detailData.instance.resubmit_req_at || '—'}</div>
                  {detailData.instance.resubmit_decided_at && (
                    <div>
                      审核时间：{detailData.instance.resubmit_decided_at}
                      {detailData.instance.resubmit_decided_by
                        ? ` · 审核人：${detailData.instance.resubmit_decided_by}`
                        : ''}
                    </div>
                  )}
                  {detailData.instance.resubmit_status === 'pending' && (
                    <div>通过后系统自动发送 30 分钟有效的补交链接；拒绝后该实例不能再申请。</div>
                  )}
                  {detailData.instance.resubmit_status === 'approved' && (
                    <div>补交链接有效期 30 分钟；过期未提交则本次补交机会已使用，不能再申请。</div>
                  )}
                </div>
              </div>
            )}

            {/* 2. 提交内容与附件下载 (/api/admin/uploads/{ref}) */}
            <div className="space-y-2.5">
              <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                <Paperclip className="w-3.5 h-3.5 text-blue-600" />
                <span>
                  {detailData.instance.kind === 'notice'
                    ? '提醒任务下发内容与附件'
                    : '员工上传内容与附件凭证'}
                </span>
              </h4>

              {!detailData.submission ? (
                <div className="p-6 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                  该任务实例尚未产生提交或下发记录。
                </div>
              ) : (
                <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3">
                  {(() => {
                    const sub = detailData.submission!;
                    const fieldRows =
                      sub.fields && sub.fields.length > 0
                        ? sub.fields
                        : Object.entries(sub.form_values || {}).map(([k, v]) => ({
                            key: k,
                            label: k,
                            value: String(v ?? ''),
                          }));
                    const attachments = sub.attachments || [];
                    /* 2026-10-30：只渲染模板**实际启用**的模块 —— 管理员只勾了字段+图片，
                       就不该出现「文件（0） 本次未上传文件」这种空区块。
                       例外：真有数据就照常显示（历史模板/脏数据不隐藏证据）。 */
                    const mods: string[] = Array.isArray(detailData.instance.modules)
                      ? detailData.instance.modules
                      : [];
                    const hasModule = (m: string, hasData = false) =>
                      hasData || mods.length === 0 || mods.indexOf(m) >= 0;
                    return (
                      <>
                        {fieldRows.length > 0 && hasModule('fields', true) && (
                          <div className="space-y-1.5">
                            <div className="text-[11px] font-semibold text-slate-500">报数字段</div>
                            <div className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
                              {fieldRows.map((f) => (
                                <div
                                  key={f.key}
                                  className="px-3.5 py-2.5 bg-slate-50/50 flex items-center justify-between gap-4"
                                >
                                  <span className="text-slate-500">{f.label}</span>
                                  <span className="font-semibold text-slate-900 text-right font-mono">
                                    {f.value}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {sub.note && (
                          <div>
                            <div className="text-[11px] font-semibold text-slate-500 mb-1">文字情况说明</div>
                            <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-800 leading-relaxed whitespace-pre-line">
                              {sub.note}
                            </div>
                          </div>
                        )}

                        {/* 2026-10-28：图片与文件分两行单独展示；图片可点开在线查看，文件只给「下载」 */}
                        {(() => {
                          const imgs = attachments.filter((a) => a.is_image);
                          const files = attachments.filter((a) => !a.is_image);
                          return (
                            <>
                              {hasModule('image', imgs.length > 0) && (
                              <div>
                                <div className="text-[11px] font-semibold text-slate-500 mb-1">
                                  图片（{imgs.length}）
                                </div>
                                {imgs.length === 0 ? (
                                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-400 text-center">
                                    本次未上传图片。
                                  </div>
                                ) : (
                                  <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                                    {imgs.map((a) => (
                                      <a
                                        key={a.ref}
                                        href={a.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="group rounded-xl border border-slate-200 overflow-hidden hover:border-blue-400 transition-colors bg-white"
                                        title="点击在线查看大图"
                                      >
                                        <img
                                          src={a.url}
                                          alt={a.file_name}
                                          className="w-full h-24 object-cover group-hover:opacity-90"
                                        />
                                        <div className="px-2 py-1.5 border-t border-slate-100">
                                          <div className="text-[10px] text-slate-500 truncate">{a.file_name}</div>
                                          <div className="text-[10px] text-blue-600 flex items-center gap-1 mt-0.5">
                                            <Eye className="w-3 h-3" />
                                            <span>在线查看</span>
                                          </div>
                                        </div>
                                      </a>
                                    ))}
                                  </div>
                                )}
                              </div>
                              )}

                              {hasModule('file', files.length > 0) && (
                              <div>
                                <div className="text-[11px] font-semibold text-slate-500 mb-1">
                                  文件（{files.length}）
                                </div>
                                {files.length === 0 ? (
                                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-400 text-center">
                                    本次未上传文件。
                                  </div>
                                ) : (
                                  <div className="space-y-2">
                                    {files.map((a) => (
                                      <div
                                        key={a.ref}
                                        className="p-2.5 rounded-xl border border-slate-200 bg-white flex items-center justify-between gap-3"
                                      >
                                        <div className="min-w-0 flex items-center gap-2">
                                          <Paperclip className="w-4 h-4 text-slate-400 shrink-0" />
                                          <div className="min-w-0">
                                            <div className="text-[12px] font-semibold text-slate-800 truncate">
                                              {a.file_name}
                                            </div>
                                            <div className="text-[10px] text-slate-400">
                                              {a.size > 0 ? `${(a.size / 1024).toFixed(0)} KB` : ''}
                                            </div>
                                          </div>
                                        </div>
                                        <div className="flex items-center gap-1.5 shrink-0">
                                          {/* 2026-10-28：docx/xlsx 等浏览器无法内联渲染，点「在线查看」也只会变下载，
                                              按用户口径统一去掉该按钮，只留下载。图片仍可点缩略图在线查看。 */}
                                          <a
                                            href={`${a.url}?dl=1`}
                                            download={a.file_name}
                                            className="px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-semibold hover:bg-blue-100 inline-flex items-center gap-1"
                                          >
                                            <Download className="w-3 h-3" />
                                            <span>下载</span>
                                          </a>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                              )}
                            </>
                          );
                        })()}

                        {sub.submitted_at && (
                          <div className="text-[11px] text-slate-400">提交时间：{sub.submitted_at}</div>
                        )}
                      </>
                    );
                  })()}
                </div>
              )}
            </div>

            {/* 3. 提醒发送记录 */}
            <div className="space-y-2.5">
              <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                <Send className="w-3.5 h-3.5 text-blue-600" />
                <span>量子密信提醒发送记录 ({detailData.reminders.length})</span>
              </h4>

              {detailData.reminders.length === 0 ? (
                <div className="p-4 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                  暂无提醒发送记录。
                </div>
              ) : (
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold">
                        <th className="py-2 px-3">时间点 / 重试</th>
                        <th className="py-2 px-3">计划时刻 / 实际时刻</th>
                        <th className="py-2 px-3">发送结果</th>
                        <th className="py-2 px-3">状态说明</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {detailData.reminders.map((r) => (
                        <tr key={r.id}>
                          <td className="py-2.5 px-3 font-mono">
                            <span className="font-semibold text-slate-800">{r.slot}</span>
                            {r.retry_no > 0 && (
                              <span className="ml-1.5 px-1.5 py-0.2 rounded bg-amber-50 text-amber-700 border border-amber-200">
                                重试 #{r.retry_no}
                              </span>
                            )}
                          </td>
                          <td className="py-2.5 px-3 font-mono text-slate-600">
                            <div>计划: {r.planned_at}</div>
                            <div className="text-slate-400">
                              实际: {r.attempted_at || r.sent_at || '—'}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded font-semibold ${
                                r.result === 'sent'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : r.result === 'need_activate' || r.result === 'no_target'
                                    ? 'bg-amber-50 text-amber-700'
                                    : 'bg-rose-50 text-rose-700'
                              }`}
                            >
                              {r.result === 'sent' ? '已送达' : r.result}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-slate-600">
                            {humanizeReminderLogError(r.result, r.error)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* 弹窗底部：仅需要人工审核的上传任务在待审核时才展示通过/驳回 */}
            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {detailData.instance.kind !== 'notice' &&
                  detailData.instance.need_review === true &&
                  detailData.instance.status === 'submitted' && (
                    <>
                      <button
                        onClick={() => handleApprove(detailData.instance)}
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                      >
                        <Check className="w-3.5 h-3.5" />
                        <span>审核通过</span>
                      </button>
                      <button
                        onClick={() => {
                          const target = detailData.instance;
                          setDetailData(null);
                          openRejectModal(target);
                        }}
                        className="px-4 py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                      >
                        <X className="w-3.5 h-3.5" />
                        <span>驳回重交</span>
                      </button>
                    </>
                  )}
                {detailData.instance.resubmit_status === 'pending' && (
                  <>
                    <button
                      onClick={() => handleResubmitApprove(detailData.instance)}
                      disabled={busyId === detailData.instance.id}
                      className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-60"
                    >
                      <Check className="w-3.5 h-3.5" />
                      <span>{busyId === detailData.instance.id ? '处理中…' : '通过补交'}</span>
                    </button>
                    <button
                      onClick={() => handleResubmitReject(detailData.instance)}
                      disabled={busyId === detailData.instance.id}
                      className="px-4 py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-60"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>拒绝补交</span>
                    </button>
                  </>
                )}
              </div>
              <button
                onClick={() => setDetailData(null)}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl cursor-pointer transition-colors"
              >
                关闭详情
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
