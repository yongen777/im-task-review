import React, { useEffect, useState } from 'react';
import {
  UserCog,
  Key,
  LogOut,
  X,
  CheckCircle2,
  Shield,
  ChevronDown,
  AlertCircle,
  Lock,
  ArrowLeftRight,
  Undo2,
} from 'lucide-react';
import { api, DEFAULT_SUB_ADMIN_PASSWORD, plainError, setCsrfToken } from '../../lib/api';
import { useToast } from '../../lib/toast';
import { useAppStore } from '../../lib/store';
import { AdminRoleLevel, OfficePlatform } from '../../lib/types';

/**
 * P2-3: 密码输入框统一使用 type="text" + style="-webkit-text-security: disc"
 * 避免浏览器弹出原生密码管理器或无网络账户登录干扰。
 */
const MASKED_INPUT_STYLE = { WebkitTextSecurity: 'disc' } as unknown as React.CSSProperties;

export interface AdminMe {
  id?: number;
  username: string;
  display_name?: string;
  role_level?: AdminRoleLevel;
  allowed_platforms?: OfficePlatform[];
  must_change_password: boolean;
  /* 2026-11-05：代登录标识 —— 后端 /me 返回（当前会话是超管 assume 出来的） */
  assumed?: boolean;
}

/* ------------------------------------------------------------------ */
/* 修改密码弹窗（支持 forced=true 首次登录强制改密拦截）                   */
/* ------------------------------------------------------------------ */

interface ChangePasswordModalProps {
  isOpen: boolean;
  forced?: boolean;
  onClose: () => void;
  onChanged: () => void;
}

export const ChangePasswordModal: React.FC<ChangePasswordModalProps> = ({
  isOpen,
  forced = false,
  onClose,
  onChanged,
}) => {
  const toast = useToast();
  const [oldPassword, setOldPassword] = useState(forced ? DEFAULT_SUB_ADMIN_PASSWORD : '');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!isOpen) return null;

  const submit = async () => {
    setErrorMsg(null);
    if (!oldPassword) {
      const msg = '请输入当前原密码（分级管理员初始密码为 Hds22999!）';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (newPassword.length < 8 || !/[a-zA-Z]/.test(newPassword) || !/\d/.test(newPassword)) {
      const msg = '新密码长度至少 8 位，且需同时包含字母和数字';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (
      newPassword === oldPassword ||
      newPassword.replace(/！/g, '!') === DEFAULT_SUB_ADMIN_PASSWORD
    ) {
      const msg = '新密码不能与初始密码 Hds22999! 或原密码相同';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (newPassword !== confirmPassword) {
      const msg = '两次输入的新密码不一致，请核对后重试';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    setBusy(true);
    try {
      await api.post('/api/admin/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      });
      const doneText = '密码已修改，请用新密码重新登录';
      setSuccessMsg(doneText);
      toast.success(doneText, '密码修改成功');
      try {
        await api.post('/api/admin/logout');
      } catch {
        // ignore
      }
      window.setTimeout(() => {
        setCsrfToken('');
        onChanged();
        if (window.location.pathname === '/change-password') {
          window.history.replaceState(null, '', '/');
        }
      }, 900);
    } catch (err) {
      const friendly = err instanceof Error ? plainError(err.message) : '密码修改未成功';
      setErrorMsg(friendly);
      toast.error(friendly);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-xs">
              <Key className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">
                {forced ? '首次登录 · 强制重设密码' : '修改管理员密码'}
              </h3>
              <span className="text-[11px] text-slate-400">
                IMTask 办公任务调度平台 · 身份凭据
              </span>
            </div>
          </div>
          {!forced && (
            <button
              onClick={onClose}
              className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {forced && (
          <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-xs flex items-start gap-2 leading-relaxed">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-amber-600" />
            <span>
              当前账号正使用初始密码（<code className="font-mono font-bold">Hds22999!</code>
              ），出于安全要求，首次登录必须重设为您本人的新密码后才能进入工作台。
            </span>
          </div>
        )}

        <div className="space-y-3 text-xs">
          {errorMsg && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}
          {successMsg && (
            <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded-xl text-emerald-800 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-600" />
              <span>{successMsg}</span>
            </div>
          )}

          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              当前原密码 {forced && <span className="text-slate-400 font-normal">（初始密码 Hds22999!）</span>}
            </label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请输入当前登录密码"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              新密码（至少 8 位，含字母与数字，不可为 Hds22999!）
            </label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="至少8位字符，同时包含字母与数字"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div>
            <label className="font-semibold text-slate-700 block mb-1">确认新密码</label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请再次输入新密码"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit();
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div className="pt-2 flex items-center justify-end gap-2">
            {!forced && (
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
            )}
            <button
              onClick={() => void submit()}
              disabled={busy}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
            >
              {busy ? '正在提交…' : forced ? '确认重设密码并重新登录' : '确认修改'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 修改管理员账号名                                                     */
/* ------------------------------------------------------------------ */

interface ChangeUsernameModalProps {
  isOpen: boolean;
  current: string;
  onClose: () => void;
  onChanged: (username: string) => void;
}

export const ChangeUsernameModal: React.FC<ChangeUsernameModalProps> = ({
  isOpen,
  current,
  onClose,
  onChanged,
}) => {
  const toast = useToast();
  const [newUsername, setNewUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setNewUsername('');
    setPassword('');
    setErrorMsg(null);
  }, [isOpen]);

  if (!isOpen) return null;

  const submit = async () => {
    setErrorMsg(null);
    const name = newUsername.trim();
    if (name.length < 2 || name.length > 32) {
      setErrorMsg('账号名长度需在 2~32 位之间');
      return;
    }
    if (/\s/.test(name)) {
      setErrorMsg('账号名不能包含空格');
      return;
    }
    if (name === current) {
      setErrorMsg('新账号名与当前账号名相同');
      return;
    }
    if (!password) {
      setErrorMsg('请输入登录密码以验证身份');
      return;
    }
    setBusy(true);
    try {
      const data = await api.post<{ ok: boolean; username: string }>('/api/admin/username', {
        new_username: name,
        password,
      });
      toast.success(`管理员账号名已修改为「${data.username || name}」`);
      onChanged(data.username || name);
    } catch (err) {
      const msg = err instanceof Error ? plainError(err.message) : '修改未成功';
      setErrorMsg(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-xs">
              <UserCog className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">修改登录账号名</h3>
              <span className="text-[11px] text-slate-400">改完下次登录请使用新账号名</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {errorMsg && (
          <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="space-y-3 text-xs">
          <div className="p-2.5 bg-slate-50 border border-slate-100 rounded-xl flex justify-between">
            <span className="text-slate-400">当前账号名</span>
            <span className="font-semibold text-slate-800 font-mono">{current}</span>
          </div>
          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              新账号名（2~32 位，不含空格）
            </label>
            <input
              type="text"
              placeholder="请输入新的登录账号名"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>
          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              登录密码（用于验证身份）
            </label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请输入当前登录密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit();
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>
          <div className="pt-2 flex items-center justify-end gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
            >
              取消
            </button>
            <button
              onClick={() => void submit()}
              disabled={busy}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
            >
              {busy ? '正在提交…' : '确认修改'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 侧边栏底部管理员卡片                                                 */
/* ------------------------------------------------------------------ */

interface AdminUserCardProps {
  me: AdminMe;
  onLoggedOut: () => void;
}

export const AdminUserCard: React.FC<AdminUserCardProps> = ({ me, onLoggedOut }) => {
  const { platform, setPlatform, allowedPlatforms, isSuperAdmin, adminAccounts } = useAppStore();
  const [open, setOpen] = useState(false);
  const [modal, setModal] = useState<'none' | 'password' | 'username' | 'logout'>('none');
  const [busy, setBusy] = useState(false);
  const [assumeBusyId, setAssumeBusyId] = useState<number | null>(null);
  const [displayName, setDisplayName] = useState(me.username || '');
  useEffect(() => setDisplayName(me.username || ''), [me.username]);
  const toast = useToast();

  const display = displayName || '管理员';
  const initial = display.slice(0, 1).toUpperCase();
  const canUseZdxlz = allowedPlatforms.includes('zdxlz');
  const canUseWecom = allowedPlatforms.includes('wecom');

  /* 2026-11-09 用户口径：「没开放当前办公软件的分级管理员直接隐藏」。
     超管当前选中企业微信时，未授权 wecom 的分级管理员（如仅 zdxlz 的向文晗）不显示，
     避免点进去才发现无权限。allowed_platforms 为空时按 ['zdxlz'] 处理，
     与 store.tsx 里 allowedPlatforms 的兜底口径保持一致。 */
  const switchableAccounts = adminAccounts.filter((a) => {
    if (a.role_level !== 'sub_admin') return false;
    const plats =
      Array.isArray(a.allowed_platforms) && a.allowed_platforms.length > 0
        ? a.allowed_platforms
        : (['zdxlz'] as OfficePlatform[]);
    return plats.includes(platform);
  });

  /* 2026-11-05 切换身份（用户需求：免退出切换到分级管理员账号，代登录后可真实执行其操作）。
     切回走后端 unassume：HttpOnly Cookie 前端拿不到 sid，原超管会话存在服务端（parent_sid）。 */
  const handleAssume = async (accId: number, accName: string) => {
    setAssumeBusyId(accId);
    try {
      // 带上当前办公软件，后端据此校验目标账号是否有该平台授权（前端已隐藏未授权的，这里是兜底）
      await api.post(`/api/admin/accounts/${accId}/assume?platform=${encodeURIComponent(platform)}`);
      toast.success(`已切换到「${accName}」身份，正在刷新界面…`);
      window.setTimeout(() => window.location.reload(), 500);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '切换身份失败');
      setAssumeBusyId(null);
    }
  };

  const handleUnassume = async () => {
    setAssumeBusyId(-1);
    try {
      await api.post('/api/admin/accounts/unassume');
      toast.success('已切回超级管理员，正在刷新界面…');
      window.setTimeout(() => window.location.reload(), 500);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '切回失败');
      setAssumeBusyId(null);
    }
  };

  const doLogout = async () => {
    setBusy(true);
    try {
      await api.post('/api/admin/logout');
    } catch {
      /* ignore */
    } finally {
      setCsrfToken('');
      onLoggedOut();
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between p-2 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200/80 transition-all cursor-pointer text-left"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <div
            className={`w-8 h-8 rounded-full text-white font-bold text-xs flex items-center justify-center shrink-0 shadow-xs ${
              isSuperAdmin ? 'bg-blue-600' : 'bg-indigo-600'
            }`}
          >
            {initial}
          </div>
          <div className="truncate">
            <div className="font-bold text-xs text-slate-800 leading-tight truncate">
              {me.display_name ? `${me.display_name} (${display})` : display}
            </div>
              <div className="text-[10px] text-blue-600 flex items-center gap-1 font-medium mt-0.5">
                <Shield className="w-2.5 h-2.5" />
                <span>{isSuperAdmin ? '超级管理员 (admin)' : me.assumed ? '代管身份（超管代登录）' : '分级管理员'}</span>
              </div>
          </div>
        </div>
        <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
      </button>

      {open && (
        <div className="absolute left-0 bottom-full mb-2 w-full bg-white rounded-2xl shadow-xl border border-slate-200 p-1.5 z-50 text-xs animate-in fade-in zoom-in-95">
          <div className="px-3 py-2 border-b border-slate-100">
            <div className="font-bold text-slate-800">
              {me.display_name ? `${me.display_name} (${display})` : display}
            </div>
                <div className="text-[10px] text-slate-400 font-mono">
                  {isSuperAdmin
                    ? '超级管理员 · 全局视图权限'
                    : me.assumed
                      ? '代管身份 · 超管代登录，可切回'
                      : '分级管理员 · 独立工作台'}
                </div>
            <div className="text-[10px] text-emerald-600 font-semibold mt-0.5">● 会话已鉴权</div>
          </div>

          {/* 2026-11-02 用户指令：在这里选择当前面板展示哪个办公软件的信息（受超管分配的办公软件权限控制） */}
          <div className="px-3 py-2 border-b border-slate-100">
            <div className="text-[10px] text-slate-400 font-semibold mb-1.5 flex items-center justify-between">
              <span>当前办公软件</span>
              {!isSuperAdmin && (
                <span className="text-[10px] text-slate-400 font-normal">
                  已开通 {allowedPlatforms.length} 项
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-1">
              <button
                type="button"
                disabled={!canUseZdxlz}
                onClick={() => {
                  if (!canUseZdxlz) return;
                  setPlatform('zdxlz');
                  setOpen(false);
                }}
                title={canUseZdxlz ? '切换至量子密信' : '未开通量子密信权限，请联系超级管理员'}
                className={`px-2 py-1.5 rounded-lg text-[11px] font-semibold border transition-colors flex items-center justify-center gap-1 ${
                  !canUseZdxlz
                    ? 'bg-slate-50 text-slate-300 border-slate-200/60 cursor-not-allowed'
                    : platform === 'zdxlz'
                      ? 'bg-blue-600 text-white border-blue-600 cursor-pointer'
                      : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50 cursor-pointer'
                }`}
              >
                {!canUseZdxlz && <Lock className="w-2.5 h-2.5" />}
                <span>量子密信</span>
              </button>
              <button
                type="button"
                disabled={!canUseWecom}
                onClick={() => {
                  if (!canUseWecom) return;
                  setPlatform('wecom');
                  setOpen(false);
                }}
                title={canUseWecom ? '切换至企业微信' : '未开通企业微信权限，请联系超级管理员'}
                className={`px-2 py-1.5 rounded-lg text-[11px] font-semibold border transition-colors flex items-center justify-center gap-1 ${
                  !canUseWecom
                    ? 'bg-slate-50 text-slate-300 border-slate-200/60 cursor-not-allowed'
                    : platform === 'wecom'
                      ? 'bg-blue-600 text-white border-blue-600 cursor-pointer'
                      : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50 cursor-pointer'
                }`}
              >
                {!canUseWecom && <Lock className="w-2.5 h-2.5" />}
                <span>企业微信</span>
              </button>
            </div>
          </div>

          {/* 2026-11-05 切换身份（仅超管）：免退出代登录到任意分级管理员，全程审计 */}
          {isSuperAdmin && (
            <div className="px-3 py-2 border-b border-slate-100">
              <div className="text-[10px] text-slate-400 font-semibold mb-1.5 flex items-center gap-1">
                <ArrowLeftRight className="w-3 h-3" />
                <span>切换身份（免退出代登录，全程审计）</span>
              </div>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {switchableAccounts.map((a) => {
                    const switching = assumeBusyId === a.id;
                    return (
                      <button
                        key={a.id}
                        type="button"
                        disabled={switching || assumeBusyId !== null}
                        onClick={() => void handleAssume(a.id, a.display_name || a.username)}
                        className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-slate-50 hover:bg-blue-50 border border-slate-200/70 hover:border-blue-200 transition-colors group disabled:opacity-60 cursor-pointer"
                      >
                        <span className="min-w-0 flex items-center gap-2">
                          <span className="w-5 h-5 rounded-full bg-indigo-600 text-white text-[10px] font-bold flex items-center justify-center shrink-0">
                            {(a.display_name || a.username || '?').slice(0, 1)}
                          </span>
                          <span className="text-[11px] font-semibold text-slate-700 truncate">
                            {a.display_name || a.username}
                            <span className="text-slate-400 font-normal"> (@{a.username})</span>
                          </span>
                        </span>
                        <span className="text-[10px] font-semibold text-blue-600 shrink-0 group-hover:underline">
                          {switching ? '切换中…' : '以此身份进入'}
                        </span>
                      </button>
                    );
                  })}
                {switchableAccounts.length === 0 && (
                  <div className="text-[11px] text-slate-400 px-2.5 py-1">
                    当前办公软件下暂无分级管理员账号
                  </div>
                )}
              </div>
            </div>
          )}

          {/* 代管身份：一键切回原超级管理员（服务端用 parent_sid 恢复原会话） */}
          {me.assumed && (
            <div className="pt-1 border-t border-slate-100">
              <button
                type="button"
                disabled={assumeBusyId === -1}
                onClick={() => void handleUnassume()}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-violet-50 text-violet-700 font-semibold flex items-center gap-2 cursor-pointer transition-colors disabled:opacity-60"
              >
                <Undo2 className="w-3.5 h-3.5" />
                <span>{assumeBusyId === -1 ? '正在切回…' : '切回超级管理员'}</span>
              </button>
            </div>
          )}

          <div className="py-1 space-y-0.5">
            <button
              onClick={() => {
                setOpen(false);
                setModal('password');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-50 text-slate-700 font-medium flex items-center gap-2 cursor-pointer transition-colors"
            >
              <Key className="w-3.5 h-3.5 text-blue-600" />
              <span>修改管理员密码</span>
            </button>

            {!isSuperAdmin && (
              <button
                onClick={() => {
                  setOpen(false);
                  setModal('username');
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-50 text-slate-700 font-medium flex items-center gap-2 cursor-pointer transition-colors"
              >
                <UserCog className="w-3.5 h-3.5 text-slate-500" />
                <span>修改账号名</span>
              </button>
            )}
          </div>

          <div className="pt-1 border-t border-slate-100">
            <button
              onClick={() => {
                setOpen(false);
                setModal('logout');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-rose-50 text-rose-600 font-semibold flex items-center gap-2 cursor-pointer transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>安全退出后台</span>
            </button>
          </div>
        </div>
      )}

      <ChangePasswordModal
        isOpen={modal === 'password'}
        onClose={() => setModal('none')}
        onChanged={() => {
          setModal('none');
          onLoggedOut();
        }}
      />

      <ChangeUsernameModal
        isOpen={modal === 'username'}
        current={display}
        onClose={() => setModal('none')}
        onChanged={(name) => {
          setDisplayName(name);
          setModal('none');
        }}
      />

      {modal === 'logout' && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-xl bg-rose-600 text-white flex items-center justify-center font-bold text-xs">
                  <LogOut className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">退出登录确认</h3>
                  <span className="text-[11px] text-slate-400">
                    IMTask 办公任务调度平台 · 身份凭据
                  </span>
                </div>
              </div>
              <button
                onClick={() => setModal('none')}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              确定要退出当前管理员登录会话吗？退出后将吊销当前会话令牌，下次访问需重新输入账号与密码。
            </p>

            <div className="pt-2 flex items-center justify-end gap-2">
              <button
                onClick={() => setModal('none')}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={doLogout}
                disabled={busy}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold shadow-xs flex items-center gap-1.5 text-xs cursor-pointer disabled:opacity-60"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span>{busy ? '正在退出…' : '确认退出登录'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
