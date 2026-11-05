import React, { useCallback, useEffect, useState } from 'react';
import {
  Shield,
  UserPlus,
  Key,
  Ban,
  CheckCircle2,
  Trash2,
  X,
  Lock,
  Settings2,
  Users,
  FileSpreadsheet,
  ClipboardCheck,
  Check,
} from 'lucide-react';
import { api, DEFAULT_SUB_ADMIN_PASSWORD, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { AdminAccountItem, OfficePlatform } from '../../lib/types';

const PLATFORM_LABELS: Record<OfficePlatform, string> = {
  zdxlz: '量子密信',
  wecom: '企业微信',
};

export const AdminAccountsView: React.FC = () => {
  const toast = useToast();
  const { refreshAdminAccounts } = useAppStore();
  const [accounts, setAccounts] = useState<AdminAccountItem[]>([]);
  const [loading, setLoading] = useState(true);

  // 开设分级管理员弹窗
  const [createOpen, setCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newPlatforms, setNewPlatforms] = useState<OfficePlatform[]>(['zdxlz']);
  const [creating, setCreating] = useState(false);

  // 编辑办公软件权限与名称弹窗
  const [editTarget, setEditTarget] = useState<AdminAccountItem | null>(null);
  const [editDisplayName, setEditDisplayName] = useState('');
  const [editPlatforms, setEditPlatforms] = useState<OfficePlatform[]>(['zdxlz']);
  const [savingEdit, setSavingEdit] = useState(false);

  // 重置初始密码确认弹窗
  const [resetTarget, setResetTarget] = useState<AdminAccountItem | null>(null);
  const [resetting, setResetting] = useState(false);

  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<AdminAccountItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<{ ok: boolean; items: AdminAccountItem[] }>('/api/admin/accounts');
      setAccounts(res.items || []);
      await refreshAdminAccounts();
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '读取管理员列表失败');
    } finally {
      setLoading(false);
    }
  }, [toast, refreshAdminAccounts]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const toggleNewPlatform = (p: OfficePlatform) => {
    setNewPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  };

  const toggleEditPlatform = (p: OfficePlatform) => {
    setEditPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  };

  const handleCreateSubAdmin = async (e: React.FormEvent) => {
    e.preventDefault();
    const u = newUsername.trim();
    const d = newDisplayName.trim() || u;
    if (!u || u.length < 2) {
      toast.warning('请输入至少 2 位字符的分级管理员账号名');
      return;
    }
    if (newPlatforms.length === 0) {
      toast.warning('请至少勾选 1 个允许开通的办公软件权限');
      return;
    }
    setCreating(true);
    try {
      await api.post('/api/admin/accounts', {
        username: u,
        display_name: d,
        password: DEFAULT_SUB_ADMIN_PASSWORD,
        allowed_platforms: newPlatforms,
      });
      setCreateOpen(false);
      setNewUsername('');
      setNewDisplayName('');
      setNewPlatforms(['zdxlz']);
      await loadAccounts();
      toast.success(
        `已成功开设分级管理员「${d}」(${u})，初始密码为 ${DEFAULT_SUB_ADMIN_PASSWORD}（首次登录将强制要求重设密码）。`,
        '分级管理员已创建',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '开设分级管理员失败');
    } finally {
      setCreating(false);
    }
  };

  const openEditModal = (acc: AdminAccountItem) => {
    setEditTarget(acc);
    setEditDisplayName(acc.display_name || acc.username);
    setEditPlatforms(
      Array.isArray(acc.allowed_platforms) && acc.allowed_platforms.length > 0
        ? [...acc.allowed_platforms]
        : ['zdxlz'],
    );
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editTarget) return;
    if (editPlatforms.length === 0) {
      toast.warning('请至少保留 1 个办公软件权限');
      return;
    }
    setSavingEdit(true);
    try {
      await api.put(`/api/admin/accounts/${editTarget.id}`, {
        display_name: editDisplayName.trim() || editTarget.username,
        allowed_platforms: editPlatforms,
      });
      const targetName = editDisplayName.trim() || editTarget.username;
      setEditTarget(null);
      await loadAccounts();
      toast.success(
        `已更新「${targetName}」的办公软件权限（${editPlatforms.map((p) => PLATFORM_LABELS[p]).join(' + ')}）。`,
        '权限已保存',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '保存失败');
    } finally {
      setSavingEdit(false);
    }
  };

  const handleToggleEnabled = async (acc: AdminAccountItem) => {
    try {
      const next = !acc.enabled;
      await api.put(`/api/admin/accounts/${acc.id}`, { enabled: next });
      await loadAccounts();
      toast.success(
        next
          ? `已启用分级管理员「${acc.display_name}」(${acc.username})`
          : `已停用分级管理员「${acc.display_name}」(${acc.username})`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '状态切换失败');
    }
  };

  const handleConfirmResetPassword = async () => {
    if (!resetTarget) return;
    setResetting(true);
    try {
      await api.post(`/api/admin/accounts/${resetTarget.id}/reset-password`, {
        new_password: DEFAULT_SUB_ADMIN_PASSWORD,
      });
      const target = resetTarget;
      setResetTarget(null);
      await loadAccounts();
      toast.success(
        `已将「${target.display_name}」(${target.username}) 的密码重置为初始密码 ${DEFAULT_SUB_ADMIN_PASSWORD}，该账号下次登录将强制要求修改密码。`,
        '已重置为初始密码',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '重置密码失败');
    } finally {
      setResetting(false);
    }
  };

  const handleDeleteSubAdmin = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/api/admin/accounts/${deleteTarget.id}`);
      const removed = deleteTarget;
      setDeleteTarget(null);
      await loadAccounts();
      toast.success(`已删除分级管理员「${removed.display_name}」(${removed.username})`);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* 顶部标题与操作栏 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">分级管理员账号与权限</h2>
            <span className="text-xs text-slate-400 tabular-nums">
              · 共 {accounts.length} 个管理员账号
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            超级管理员 <code className="font-mono font-semibold text-slate-700">admin</code>{' '}
            可查看全平台全部数据；新建分级管理员拥有独立干净的工作台，默认初始密码为{' '}
            <code className="font-mono font-semibold text-blue-700">{DEFAULT_SUB_ADMIN_PASSWORD}</code>{' '}
            （首次登录强制重设密码），导入或搜索已激活成员时自动跨管理员复用激活状态。
          </p>
        </div>

        <button
          type="button"
          onClick={() => {
            setNewUsername('');
            setNewDisplayName('');
            setNewPlatforms(['zdxlz']);
            setCreateOpen(true);
          }}
          className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer shrink-0 transition-colors"
        >
          <UserPlus className="w-4 h-4" />
          <span>开设分级管理员</span>
        </button>
      </div>

      {/* 管理员列表 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {loading ? (
          <div className="col-span-2 bg-white rounded-2xl border border-slate-200/90 p-12 text-center text-xs text-slate-400">
            正在加载管理员账号列表…
          </div>
        ) : (
          accounts.map((acc) => {
            const isSuper = acc.role_level === 'super_admin';
            const platforms =
              Array.isArray(acc.allowed_platforms) && acc.allowed_platforms.length > 0
                ? acc.allowed_platforms
                : (['zdxlz'] as OfficePlatform[]);

            return (
              <div
                key={acc.id}
                className={`bg-white rounded-2xl border p-5 flex flex-col justify-between gap-4 transition-all ${
                  isSuper ? 'border-blue-200/90' : 'border-slate-200/90'
                }`}
              >
                <div className="space-y-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div
                        className={`w-10 h-10 rounded-xl font-bold text-sm flex items-center justify-center shrink-0 text-white ${
                          isSuper ? 'bg-blue-600' : acc.enabled ? 'bg-indigo-600' : 'bg-slate-400'
                        }`}
                      >
                        {(acc.display_name || acc.username).slice(0, 1).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold text-slate-900 text-sm truncate">
                            {acc.display_name}
                          </span>
                          <span className="font-mono text-xs text-slate-500">
                            @{acc.username}
                          </span>
                        </div>
                        <div className="text-[11px] text-slate-500 flex items-center gap-1.5 mt-0.5 flex-wrap">
                          <span
                            className={
                              isSuper
                                ? 'text-blue-600 font-semibold'
                                : 'text-indigo-600 font-medium'
                            }
                          >
                            {isSuper ? '超级管理员（全局可见）' : '分级管理员（独立工作空间）'}
                          </span>
                          <span aria-hidden="true">·</span>
                          <span>
                            {acc.must_change_password
                              ? `待首次登录修改初始密码 (${DEFAULT_SUB_ADMIN_PASSWORD})`
                              : '已设置正式密码'}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="text-xs font-medium shrink-0">
                      {acc.enabled ? (
                        <span className="text-emerald-700 inline-flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>正常启用</span>
                        </span>
                      ) : (
                        <span className="text-slate-400 inline-flex items-center gap-1">
                          <Ban className="w-3.5 h-3.5" />
                          <span>已停用</span>
                        </span>
                      )}
                    </div>
                  </div>

                  {/* 开通的办公软件权限展示行 */}
                  <div className="px-3.5 py-2.5 rounded-xl bg-slate-50/90 border border-slate-100 flex items-center justify-between gap-2 text-xs">
                    <span className="text-slate-500 font-medium">已开通办公软件权限：</span>
                    <div className="flex items-center gap-2 flex-wrap">
                      {(['zdxlz', 'wecom'] as OfficePlatform[]).map((p) => {
                        const allowed = isSuper || platforms.includes(p);
                        return (
                          <span
                            key={p}
                            className={`inline-flex items-center gap-1 font-semibold ${
                              allowed ? 'text-blue-700' : 'text-slate-300 line-through'
                            }`}
                          >
                            {allowed ? (
                              <Check className="w-3.5 h-3.5 text-blue-600" />
                            ) : (
                              <Lock className="w-3 h-3 text-slate-300" />
                            )}
                            <span>{PLATFORM_LABELS[p]}</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>

                  {/* 独立工作空间统计 */}
                  <div className="grid grid-cols-4 gap-2 pt-0.5">
                    <div className="bg-slate-50/80 border border-slate-100 rounded-xl p-2.5">
                      <div className="text-[11px] text-slate-400 flex items-center gap-1">
                        <Users className="w-3 h-3" />
                        <span>角色分组</span>
                      </div>
                      <div className="text-sm font-bold text-slate-800 font-mono tabular-nums mt-0.5">
                        {acc.role_count}
                      </div>
                    </div>
                    <div className="bg-slate-50/80 border border-slate-100 rounded-xl p-2.5">
                      <div className="text-[11px] text-slate-400 flex items-center gap-1">
                        <Shield className="w-3 h-3" />
                        <span>在册成员</span>
                      </div>
                      <div className="text-sm font-bold text-slate-800 font-mono tabular-nums mt-0.5">
                        {acc.member_count}
                      </div>
                    </div>
                    <div className="bg-slate-50/80 border border-slate-100 rounded-xl p-2.5">
                      <div className="text-[11px] text-slate-400 flex items-center gap-1">
                        <FileSpreadsheet className="w-3 h-3" />
                        <span>任务模板</span>
                      </div>
                      <div className="text-sm font-bold text-slate-800 font-mono tabular-nums mt-0.5">
                        {acc.template_count}
                      </div>
                    </div>
                    <div className="bg-slate-50/80 border border-slate-100 rounded-xl p-2.5">
                      <div className="text-[11px] text-slate-400 flex items-center gap-1">
                        <ClipboardCheck className="w-3 h-3" />
                        <span>任务实例</span>
                      </div>
                      <div className="text-sm font-bold text-slate-800 font-mono tabular-nums mt-0.5">
                        {acc.instance_count}
                      </div>
                    </div>
                  </div>
                </div>

                {/* 底部操作区 */}
                <div className="pt-3 border-t border-slate-100 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="text-[11px] text-slate-400 font-mono tabular-nums">
                    创建于 {acc.created_at.slice(0, 16)}
                  </span>

                  {isSuper ? (
                    <span className="text-[11px] text-slate-400 inline-flex items-center gap-1">
                      <Lock className="w-3 h-3" />
                      <span>超管主账号拥有全部办公软件与全局查看权限</span>
                    </span>
                  ) : (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => openEditModal(acc)}
                        className="px-2.5 py-1.5 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 font-semibold inline-flex items-center gap-1 cursor-pointer transition-colors"
                      >
                        <Settings2 className="w-3.5 h-3.5" />
                        <span>分配办公软件权限</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => setResetTarget(acc)}
                        className="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer transition-colors"
                        title={`重置为默认初始密码 ${DEFAULT_SUB_ADMIN_PASSWORD} 并要求下次登录重设`}
                      >
                        <Key className="w-3.5 h-3.5 text-slate-500" />
                        <span>重置初始密码</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => void handleToggleEnabled(acc)}
                        className="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer transition-colors"
                      >
                        {acc.enabled ? (
                          <>
                            <Ban className="w-3.5 h-3.5 text-amber-600" />
                            <span>停用</span>
                          </>
                        ) : (
                          <>
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                            <span>启用</span>
                          </>
                        )}
                      </button>

                      {(() => {
                        const hasBusinessData =
                          (acc.role_count || 0) > 0 || (acc.template_count || 0) > 0;
                        return (
                          <button
                            type="button"
                            disabled={hasBusinessData}
                            title={
                              hasBusinessData
                                ? '名下已有业务数据，请改用停用'
                                : '删除该分级管理员账号'
                            }
                            onClick={() => {
                              if (!hasBusinessData) setDeleteTarget(acc);
                            }}
                            className="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-rose-50 text-slate-600 hover:text-rose-600 font-semibold inline-flex items-center gap-1 cursor-pointer transition-colors disabled:opacity-45 disabled:cursor-not-allowed disabled:hover:bg-slate-100 disabled:hover:text-slate-600"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            <span>删除</span>
                          </button>
                        );
                      })()}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 开设分级管理员弹窗 */}
      {createOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">开设分级管理员账号</h3>
                <p className="text-[11px] text-slate-400">
                  新账号拥有独立干净的工作空间，可按需开通单个或多个办公软件权限
                </p>
              </div>
              <button
                onClick={() => setCreateOpen(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateSubAdmin} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  分级管理员登录账号名 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  autoFocus
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="以用户名称命名，例如：zhangsan 或 chengxi_admin"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 font-mono"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  管理员显示名称 / 所属部门备注
                </label>
                <input
                  type="text"
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                  placeholder="例如：张三（城西支局管理员）"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1.5">
                  分配开通办公软件权限（可单选或多选） <span className="text-rose-500">*</span>
                </label>
                <div className="grid grid-cols-2 gap-2.5">
                  {(['zdxlz', 'wecom'] as OfficePlatform[]).map((p) => {
                    const checked = newPlatforms.includes(p);
                    return (
                      <label
                        key={p}
                        className={`flex items-center gap-2.5 p-3 rounded-xl border cursor-pointer transition-all ${
                          checked
                            ? 'bg-blue-50/70 border-blue-500 text-blue-900 font-semibold'
                            : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-white'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleNewPlatform(p)}
                          className="w-3.5 h-3.5 accent-blue-600"
                        />
                        <span>{PLATFORM_LABELS[p]}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="p-3 rounded-xl bg-amber-50/80 border border-amber-200/80 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-amber-900">默认初始登录密码</span>
                  <code className="font-mono font-bold text-amber-900 bg-white px-2 py-0.5 rounded border border-amber-200">
                    {DEFAULT_SUB_ADMIN_PASSWORD}
                  </code>
                </div>
                <p className="text-[11px] text-amber-800 leading-relaxed">
                  系统已自动将该分级管理员的初始密码设为{' '}
                  <strong className="font-mono">{DEFAULT_SUB_ADMIN_PASSWORD}</strong>
                  ，首次登录时将强制弹出重设密码窗口，完成改密后方可进入后台。
                </p>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setCreateOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {creating ? '正在创建…' : '确认开设账号'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 分配办公软件权限与显示名称弹窗 */}
      {editTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  分配办公软件权限 · @{editTarget.username}
                </h3>
                <p className="text-[11px] text-slate-400">
                  勾选该分级管理员可使用的办公软件（单个或多个）
                </p>
              </div>
              <button
                onClick={() => setEditTarget(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveEdit} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 block mb-1">显示名称 / 备注</label>
                <input
                  type="text"
                  value={editDisplayName}
                  onChange={(e) => setEditDisplayName(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1.5">
                  开通办公软件权限（可单选或多选） <span className="text-rose-500">*</span>
                </label>
                <div className="grid grid-cols-2 gap-2.5">
                  {(['zdxlz', 'wecom'] as OfficePlatform[]).map((p) => {
                    const checked = editPlatforms.includes(p);
                    return (
                      <label
                        key={p}
                        className={`flex items-center gap-2.5 p-3 rounded-xl border cursor-pointer transition-all ${
                          checked
                            ? 'bg-blue-50/70 border-blue-500 text-blue-900 font-semibold'
                            : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-white'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleEditPlatform(p)}
                          className="w-3.5 h-3.5 accent-blue-600"
                        />
                        <span>{PLATFORM_LABELS[p]}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditTarget(null)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingEdit}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {savingEdit ? '正在保存…' : '保存权限配置'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 重置为初始密码 Hds22999! 确认弹窗 */}
      {resetTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">确认重置为默认初始密码？</h3>
                <p className="text-[11px] text-slate-400">
                  目标账号：{resetTarget.display_name} (@{resetTarget.username})
                </p>
              </div>
              <button
                onClick={() => setResetTarget(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="text-xs text-slate-600 space-y-2 leading-relaxed">
              <p>
                重置后，该分级管理员的登录密码将恢复为默认初始密码{' '}
                <code className="font-mono font-bold text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded">
                  {DEFAULT_SUB_ADMIN_PASSWORD}
                </code>
                ，且下次登录时将<strong>强制弹出重设密码窗口</strong>。
              </p>
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setResetTarget(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmResetPassword()}
                disabled={resetting}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
              >
                {resetting ? '正在重置…' : `确认重置为 ${DEFAULT_SUB_ADMIN_PASSWORD}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除分级管理员弹窗 */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">确认删除该分级管理员？</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed">
              即将删除分级管理员{' '}
              <strong className="text-slate-900">
                {deleteTarget.display_name} (@{deleteTarget.username})
              </strong>{' '}
              及其名下创建的任务模板与角色分组（底层全局已激活员工的机器人会话绑定不会受影响）。
            </p>
            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteSubAdmin()}
                disabled={deleting}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
              >
                {deleting ? '正在删除…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
