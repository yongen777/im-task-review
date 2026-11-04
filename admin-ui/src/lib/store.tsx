import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api';
import { AdminAccountItem, AdminHealth, AdminMe, InstanceStats, RoleItem, TemplateItem } from './types';

export type Platform = 'zdxlz' | 'wecom';
const PLATFORM_KEY = 'imtask.platform';

interface AppContextType {
  templates: TemplateItem[];
  roles: RoleItem[];
  stats: InstanceStats | null;
  health: AdminHealth | null;
  loading: boolean;
  platform: Platform;
  setPlatform: (p: Platform) => void;
  refreshAll: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
  refreshRoles: () => Promise<void>;
  refreshStats: () => Promise<void>;
  /** 2026-11-04 多管理员分级：当前登录管理员档案 */
  me: AdminMe | null;
  /** 超级管理员（全局只读 + 账号管理） */
  isSuperAdmin: boolean;
  /** 本账号被授权的办公软件平台 */
  allowedPlatforms: Platform[];
  /** 2026-11-04：管理员账号列表（仅超管能拉到） */
  adminAccounts: AdminAccountItem[];
  /** 2026-11-04：超管的「所属管理员筛选」（'' = 全部） */
  ownerFilter: string;
  setOwnerFilter: (v: string) => void;
  refreshAdminAccounts: () => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [stats, setStats] = useState<InstanceStats | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [loading, setLoading] = useState(true);
  // 2026-11-04 多管理员分级：当前账号档案（角色级别 / 授权平台）
  const [me, setMe] = useState<AdminMe | null>(null);
  const [allowedPlatforms, setAllowedPlatforms] = useState<Platform[]>(['zdxlz', 'wecom']);
  // 2026-11-04：超管的「所属管理员筛选」（'' = 全部）；分级管理员固定看自己（后端强制）
  const [ownerFilter, setOwnerFilter] = useState<string>('');
  const [adminAccounts, setAdminAccounts] = useState<AdminAccountItem[]>([]);
  const isSuperAdmin = me?.role_level === 'super_admin';
  const [platform, setPlatformState] = useState<Platform>(() => {
    const saved = (localStorage.getItem(PLATFORM_KEY) || '').trim() as Platform;
    return saved === 'zdxlz' || saved === 'wecom' ? saved : 'zdxlz';
  });

  const setPlatform = useCallback((p: Platform) => {
    setPlatformState(p);
    localStorage.setItem(PLATFORM_KEY, p);
  }, []);

  // 首次进入（没有本地记忆）时，跟随后端「当前启用通道」。
  useEffect(() => {
    if (localStorage.getItem(PLATFORM_KEY)) return;
    void (async () => {
      try {
        const res = await fetch('/api/admin/channel-config', { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = (await res.json()) as { active_channel?: string };
        if (!localStorage.getItem(PLATFORM_KEY)) {
          setPlatformState(data.active_channel === 'wecom' || data.active_channel === 'wecom_aibot' ? 'wecom' : 'zdxlz');
        }
      } catch {
        /* 读不到就保持默认 zdxlz */
      }
    })();
  }, []);

  const refreshTemplates = useCallback(async () => {
    const ownerQ = ownerFilter ? `&owner_id=${encodeURIComponent(ownerFilter)}` : '';
    const res = await api.get<{ ok: boolean; items: TemplateItem[] }>(`/api/admin/templates?size=500&platform=${platform}${ownerQ}`);
    setTemplates(res.items || []);
  }, [platform, ownerFilter]);

  const refreshRoles = useCallback(async () => {
    const ownerQ = ownerFilter ? `&owner_id=${encodeURIComponent(ownerFilter)}` : '';
    const res = await api.get<{ ok: boolean; items: RoleItem[] }>(`/api/admin/roles?platform=${platform}${ownerQ}`);
    setRoles(res.items || []);
  }, [platform, ownerFilter]);

  const refreshStats = useCallback(async () => {
    const res = await api.get<InstanceStats>(`/api/admin/instances/stats?platform=${platform}`);
    setStats(res);
  }, [platform]);

  const refreshAll = useCallback(async () => {
    try {
      const ownerQ = ownerFilter ? `&owner_id=${encodeURIComponent(ownerFilter)}` : '';
      const [tplRes, roleRes, statRes, healthRes] = await Promise.all([
        api.get<{ ok: boolean; items: TemplateItem[] }>(`/api/admin/templates?size=500&platform=${platform}${ownerQ}`),
        api.get<{ ok: boolean; items: RoleItem[] }>(`/api/admin/roles?platform=${platform}${ownerQ}`),
        api.get<InstanceStats>(`/api/admin/instances/stats?platform=${platform}`),
        api.get<AdminHealth>('/api/admin/health'),
      ]);
      setTemplates(tplRes.items || []);
      setRoles(roleRes.items || []);
      setStats(statRes);
      setHealth(healthRes);
    } finally {
      setLoading(false);
    }
  }, [platform, ownerFilter]);

  /** 2026-11-04：拉管理员账号列表（仅超管有权限，拉不到就静默为空） */
  const refreshAdminAccounts = useCallback(async () => {
    try {
      const res = await api.get<{ ok: boolean; items: AdminAccountItem[] }>('/api/admin/accounts');
      setAdminAccounts(res.items || []);
    } catch {
      setAdminAccounts([]);
    }
  }, []);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // 2026-11-04：读取当前账号档案；授权平台变化时把当前平台夹回授权范围内
  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<AdminMe>('/api/admin/me');
        setMe(res);
        const allowed = (res.allowed_platforms || []).filter((p): p is Platform => p === 'zdxlz' || p === 'wecom');
        if (allowed.length) {
          setAllowedPlatforms(allowed);
          setPlatformState((prev) => (allowed.includes(prev) ? prev : allowed[0]));
        }
      } catch {
        /* 读不到就按默认双平台处理 */
      }
    })();
  }, []);

  return (
    <AppContext.Provider
      value={{
        templates,
        roles,
        stats,
        health,
        loading,
        platform,
        setPlatform,
        refreshAll,
        refreshTemplates,
        refreshRoles,
        refreshStats,
        me,
        isSuperAdmin,
        allowedPlatforms,
        adminAccounts,
        ownerFilter,
        setOwnerFilter,
        refreshAdminAccounts,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useAppStore = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppStore must be used within AppProvider');
  return ctx;
};
