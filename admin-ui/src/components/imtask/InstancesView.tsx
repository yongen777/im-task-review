import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Eye,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
  X,
  Bell,
  Smartphone,
  Users,
  Calendar,
  Search,
  ListFilter,
} from 'lucide-react';
import { getInstanceStatusMeta, InstanceDetailDialog, InstancePlanTime, useInstanceRoleLabel } from './InstanceDetailDialog';
import PendingReviewPanel from './PendingReviewPanel';
import { AdminScopeHeader } from './AdminScopeHeader';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import {
  InstanceStats,
  TaskInstanceItem,
} from '../../lib/types';

const PAGE_SIZE = 20;

function getTodayDateString(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const InstancesView: React.FC = () => {
  const { roles, refreshStats, platform, isSuperAdmin, ownerFilter, setOwnerFilter, adminAccounts } = useAppStore();
  const toast = useToast();

  // 筛选条件：按日期（默认今天）、按角色、按任务类型、按执行状态、按用户名/手机号
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const combinedStatusLabel = statusFilter === 'approved,sent'
    ? '已提醒 / 已完成'
    : statusFilter === 'rejected,overdue' ? '已驳回 / 已逾期' : '';
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
  const [detailId, setDetailId] = useState<number | null>(null);
  const roleLabel = useInstanceRoleLabel();

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
        // 2026-11-12 任务E3：'resubmit_pending' 虚拟筛选项已随「申请补交」下线，仅按 status 过滤
        const applyStatus = (target: URLSearchParams) => {
          if (!st) return;
          target.set('status', st);
        };
        applyStatus(qs);
        if (kd) qs.set('kind', kd);
        if (rl) qs.set('role_id', rl);
        if (ph) qs.set('q', ph);
        if (pk) qs.set('period_key', pk);
        if (tid) qs.set('template_id', tid);
        if (ownerFilter) qs.set('owner_id', ownerFilter);
        qs.set('platform', platform);

        const statsQs = new URLSearchParams();
        applyStatus(statsQs);
        if (kd) statsQs.set('kind', kd);
        if (rl) statsQs.set('role_id', rl);
        if (ph) statsQs.set('q', ph);
        if (pk) statsQs.set('period_key', pk);
        if (tid) statsQs.set('template_id', tid);
        if (ownerFilter) statsQs.set('owner_id', ownerFilter);
        statsQs.set('platform', platform);

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
    [page, statusFilter, kindFilter, roleFilter, phoneFilter, periodKeyFilter, templateFilter, ownerFilter, platform],
  );

  useEffect(() => {
    loadData(1);
  }, [statusFilter, kindFilter, roleFilter, periodKeyFilter, templateFilter, ownerFilter, platform]);

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

  const [pendingRefresh, setPendingRefresh] = useState(0);
  const handlePendingChanged = useCallback(() => {
    setPendingRefresh((n) => n + 1);
    void Promise.all([loadData(page), refreshStats()]);
  }, [loadData, page, refreshStats]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* 顶部标题与管理员视角（统一标准组件） */}
      <AdminScopeHeader
        title="任务实例与审核"
        badge="执行跟踪与上传审核"
        description="支持按任务类型（通知提醒 / 上传任务）筛选查看执行记录，对需审核的上传任务进行通过或驳回。"
      />

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
            <div className="text-xs text-blue-700 font-semibold">已提交（待审核）</div>
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
        platform={platform}
        onOpenDetail={setDetailId}
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
            aria-label="执行状态"
            value={combinedStatusLabel ? '' : statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-700 font-medium outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
          >
            <option value="">全部执行状态</option>
            <option value="pending">待提醒 / 待上传</option>
            <option value="sent">已提醒（通知类已送达）</option>
            <option value="submitted">已提交（待审核）</option>
            <option value="approved">已完成（审核通过）</option>
            <option value="rejected">已驳回待重交</option>
            <option value="overdue">已逾期未交</option>
            <option value="cancelled">已取消</option>
          </select>
          {combinedStatusLabel && (
            <button type="button" onClick={() => setStatusFilter('')} aria-label={`清除筛选：${combinedStatusLabel}`}
              className="inline-flex items-center gap-1 rounded-lg bg-blue-50 px-2 py-1 font-medium text-blue-700 hover:bg-blue-100">
              {combinedStatusLabel}<X className="h-3.5 w-3.5" />
            </button>
          )}

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
                <th className="py-3.5 px-3 whitespace-nowrap">执行 / 完成时间</th>
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
                        <div>
                          <span className="font-bold text-slate-900 text-xs">{inst.template_name}</span>
                          {isSuperAdmin && (inst.owner_display_name || inst.owner_username) && (
                            <div className="mt-1">
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-violet-50 text-violet-700 border border-violet-200/60 text-[10px] font-medium">
                                创建人：{inst.owner_display_name || inst.owner_username}
                              </span>
                            </div>
                          )}
                        </div>
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
                          {/* 2026-11-20：管理员代交是「提交来源」而不是「状态」，所以不塞进状态下拉框，
                              这里用一个轻量徽标标出来，列表里一眼能认。 */}
                          {inst.latest_submission?.source === 'admin' && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">
                              管理员代交
                            </span>
                          )}
                        </div>
                      </td>

                      {/* 7. 操作列：2026-10-28 按业务方要求只留「详情」，
                             待提醒/通知已送达/通过/驳回等统统挪进详情弹窗里操作 */}
                      <td className="py-4 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-2">
                          <button
                            onClick={() => setDetailId(inst.id)}
                            className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                          >
                            <Eye className="w-3.5 h-3.5 shrink-0" />
                            <span>详情</span>
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

      {detailId !== null && (
        <InstanceDetailDialog key={detailId} instanceId={detailId}
          onClose={() => setDetailId(null)} onChanged={handlePendingChanged} />
      )}
    </div>
  );
};
