import React, { useCallback, useEffect, useState } from 'react';
import { Key, Plus, Shield, Trash2, UserCog, Users, X, Pencil } from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useToast } from '../../lib/toast';
import { AdminAccountItem } from '../../lib/types';

const PLATFORMS: Array<{ key: 'zdxlz' | 'wecom'; label: string }> = [
  { key: 'zdxlz', label: '量子密信' },
  { key: 'wecom', label: '企业微信' },
];

const DEFAULT_PASSWORD = 'Hds22999!';

/** 2026-11-04 多管理员分级：超级管理员专属的「管理员账号管理」页。 */
export const AdminAccountsView: React.FC = () => {
  const toast = useToast();
  const [items, setItems] = useState<AdminAccountItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ username: '', display_name: '', platforms: ['zdxlz', 'wecom'] as Array<'zdxlz' | 'wecom'> });
  const [editing, setEditing] = useState<AdminAccountItem | null>(null);
  const [editName, setEditName] = useState('');
  const [editPlatforms, setEditPlatforms] = useState<Array<'zdxlz' | 'wecom'>>(['zdxlz', 'wecom']);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<{ ok: boolean; items: AdminAccountItem[] }>('/api/admin/accounts');
      setItems(res.items || []);
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '账号列表读取失败'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const togglePlatform = (list: Array<'zdxlz' | 'wecom'>, key: 'zdxlz' | 'wecom') =>
    list.includes(key) ? list.filter((x) => x !== key) : [...list, key];

  const submitCreate = async () => {
    if (!form.username.trim()) {
      toast.warning('请填写用户名');
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ ok: boolean; account: AdminAccountItem }>('/api/admin/accounts', {
        username: form.username.trim(),
        display_name: form.display_name.trim(),
        allowed_platforms: form.platforms,
      });
      toast.success(`已创建「${res.account.display_name || res.account.username}」，初始密码 ${DEFAULT_PASSWORD}，首次登录须改密`);
      setCreating(false);
      setForm({ username: '', display_name: '', platforms: ['zdxlz', 'wecom'] });
      await load();
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '创建失败'));
    } finally {
      setBusy(false);
    }
  };

  const submitEdit = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await api.put(`/api/admin/accounts/${editing.id}`, {
        display_name: editName.trim(),
        allowed_platforms: editPlatforms,
      });
      toast.success('已保存');
      setEditing(null);
      await load();
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async (acc: AdminAccountItem) => {
    setBusy(true);
    try {
      await api.post(`/api/admin/accounts/${acc.id}/reset-password`);
      toast.success(`已重置为初始密码 ${DEFAULT_PASSWORD}，该账号首次登录须改密`);
      await load();
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '重置失败'));
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (acc: AdminAccountItem) => {
    setBusy(true);
    try {
      await api.put(`/api/admin/accounts/${acc.id}`, { enabled: !acc.enabled });
      toast.success(acc.enabled ? '已停用该账号' : '已启用该账号');
      await load();
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '操作失败'));
    } finally {
      setBusy(false);
    }
  };

  const removeAccount = async (acc: AdminAccountItem) => {
    if (!window.confirm(`确认删除「${acc.display_name || acc.username}」？名下有角色/成员/模板时不可删除。`)) return;
    setBusy(true);
    try {
      await api.delete(`/api/admin/accounts/${acc.id}`);
      toast.success('已删除');
      await load();
    } catch (err) {
      toast.error(plainError(err instanceof Error ? err.message : '删除失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-5">
        <div className="flex items-center justify-between gap-3 pb-4 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center shrink-0">
              <UserCog className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-slate-900 tracking-tight leading-tight">管理员账号管理</h2>
              <p className="text-xs text-slate-500 mt-0.5">
                超级管理员负责开通分级管理员并分配办公软件权限；业务数据（任务/角色/成员）由各分级管理员自己维护
              </p>
            </div>
          </div>
          <button
            onClick={() => setCreating((v) => !v)}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer shrink-0"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>新建分级管理员</span>
          </button>
        </div>

        {creating && (
          <div className="mt-4 p-4 bg-slate-50 rounded-xl border border-slate-200 space-y-3">
            <div className="grid sm:grid-cols-2 gap-3">
              <label className="text-xs font-semibold text-slate-700 space-y-1 block">
                <span>用户名（登录用，建议拼音/工号）</span>
                <input
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                  placeholder="如 zhangsan"
                  className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 outline-none focus:border-blue-500"
                />
              </label>
              <label className="text-xs font-semibold text-slate-700 space-y-1 block">
                <span>显示名称（中文名/职责）</span>
                <input
                  value={form.display_name}
                  onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                  placeholder="如 张三 · 城东支局"
                  className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 outline-none focus:border-blue-500"
                />
              </label>
            </div>
            <div className="text-xs font-semibold text-slate-700">
              <span className="block mb-1.5">授权办公软件平台（可单选或全选）</span>
              <div className="flex gap-2 flex-wrap">
                {PLATFORMS.map((p) => {
                  const on = form.platforms.includes(p.key);
                  return (
                    <button
                      key={p.key}
                      type="button"
                      onClick={() => setForm({ ...form, platforms: togglePlatform(form.platforms, p.key) })}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold border cursor-pointer ${
                        on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200'
                      }`}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11px] text-slate-500">
                初始密码固定为 <code className="font-mono text-slate-700">{DEFAULT_PASSWORD}</code>，首次登录强制修改
              </span>
              <div className="flex gap-2">
                <button onClick={() => setCreating(false)} className="px-3 py-1.5 rounded-lg text-xs text-slate-600 hover:bg-slate-100 cursor-pointer">
                  取消
                </button>
                <button
                  onClick={submitCreate}
                  disabled={busy}
                  className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg cursor-pointer disabled:opacity-60"
                >
                  {busy ? '创建中…' : '创建账号'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="w-4 h-4 text-slate-400" />
            <span className="font-bold text-sm text-slate-900">账号列表</span>
            <span className="text-xs text-slate-400">{items.length} 个</span>
          </div>
          <button onClick={() => void load()} className="text-xs text-slate-500 hover:text-blue-600 cursor-pointer">
            刷新
          </button>
        </div>
        {loading ? (
          <div className="p-10 text-center text-xs text-slate-400">读取中…</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {items.map((acc) => (
              <div key={acc.id} className="px-5 py-3 flex items-center gap-3 flex-wrap">
                <div className="w-8 h-8 rounded-lg bg-slate-100 text-slate-600 font-bold text-xs flex items-center justify-center shrink-0">
                  {(acc.display_name || acc.username).slice(0, 1)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-bold text-xs text-slate-800 truncate">{acc.display_name || acc.username}</span>
                    <span className="text-[10px] font-mono text-slate-400">{acc.username}</span>
                    {acc.role_level === 'super_admin' ? (
                      <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 border border-indigo-200/70 font-semibold">
                        <Shield className="w-2.5 h-2.5" /> 超级管理员
                      </span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200 font-semibold">
                        分级管理员
                      </span>
                    )}
                    {!acc.enabled && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-600 border border-rose-200 font-semibold">已停用</span>
                    )}
                    {!!acc.must_change && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 border border-amber-200 font-semibold">待改密</span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-2 flex-wrap">
                    <span>平台：{(acc.allowed_platforms || []).map((p) => (p === 'zdxlz' ? '量子密信' : '企业微信')).join('、') || '（无）'}</span>
                    <span>· 角色 {acc.role_count ?? 0}</span>
                    <span>· 成员 {acc.member_count ?? 0}</span>
                    <span>· 模板 {acc.template_count ?? 0}</span>
                    <span>· 实例 {acc.instance_count ?? 0}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => {
                      setEditing(acc);
                      setEditName(acc.display_name || '');
                      setEditPlatforms((acc.allowed_platforms || []) as Array<'zdxlz' | 'wecom'>);
                    }}
                    className="px-2 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-600 hover:text-blue-700 font-semibold text-[11px] cursor-pointer inline-flex items-center gap-1"
                  >
                    <Pencil className="w-3 h-3" /> 编辑
                  </button>
                  <button
                    onClick={() => void resetPassword(acc)}
                    disabled={busy || acc.role_level === 'super_admin'}
                    className="px-2 py-1 rounded-lg bg-slate-100 hover:bg-amber-50 text-slate-600 hover:text-amber-700 font-semibold text-[11px] cursor-pointer inline-flex items-center gap-1 disabled:opacity-40"
                  >
                    <Key className="w-3 h-3" /> 重置密码
                  </button>
                  {acc.role_level !== 'super_admin' && (
                    <>
                      <button
                        onClick={() => void toggleEnabled(acc)}
                        disabled={busy}
                        className="px-2 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold text-[11px] cursor-pointer disabled:opacity-40"
                      >
                        {acc.enabled ? '停用' : '启用'}
                      </button>
                      <button
                        onClick={() => void removeAccount(acc)}
                        disabled={busy}
                        className="px-2 py-1 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 font-semibold text-[11px] cursor-pointer inline-flex items-center gap-1 disabled:opacity-40"
                      >
                        <Trash2 className="w-3 h-3" /> 删除
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base flex items-center gap-1.5">
                <UserCog className="w-4 h-4 text-blue-600" /> 编辑分级管理员
              </h3>
              <button onClick={() => setEditing(null)} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer">
                <X className="w-5 h-5" />
              </button>
            </div>
            <label className="block text-xs font-semibold text-slate-700 space-y-1">
              <span>显示名称</span>
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs outline-none focus:border-blue-500"
              />
            </label>
            <div className="text-xs font-semibold text-slate-700">
              <span className="block mb-1.5">授权办公软件平台</span>
              <div className="flex gap-2 flex-wrap">
                {PLATFORMS.map((p) => {
                  const on = editPlatforms.includes(p.key);
                  return (
                    <button
                      key={p.key}
                      type="button"
                      onClick={() => setEditPlatforms(togglePlatform(editPlatforms, p.key))}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold border cursor-pointer ${
                        on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200'
                      }`}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setEditing(null)} className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 cursor-pointer">
                取消
              </button>
              <button
                onClick={submitEdit}
                disabled={busy}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminAccountsView;
