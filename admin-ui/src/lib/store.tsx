import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from './api';
import {
  AdminAccountItem,
  AdminHealth,
  AdminRoleLevel,
  InstanceStats,
  OfficePlatform,
  RoleItem,
  TemplateItem,
} from './types';

export type Platform = OfficePlatform;
const PLATFORM_KEY = 'imtask.platform';

export interface CurrentAdminInfo {
  id?: number;
  username: string;
  display_name?: string;
  role_level?: AdminRoleLevel;
  allowed_platforms?: OfficePlatform[];
  must_change_password: boolean;
}

interface AppContextType {
  me: CurrentAdminInfo;
  isSuperAdmin: boolean;
  allowedPlatforms: Platform[];
  ownerFilter: string;
  setOwnerFilter: (ownerId: string) => void;
  adminAccounts: AdminAccountItem[];
  templates: TemplateItem[];
  roles: RoleItem[];
  stats: InstanceStats | null;
  health: AdminHealth | null;
  loading: boolean;
  platform: Platform;
  setPlatform: (p: Platform) => void;
  memberDistinctTotal: number;
  refreshAll: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
  refreshRoles: () => Promise<void>;
  refreshStats: () => Promise<void>;
  refreshAdminAccounts: () => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ me: CurrentAdminInfo; children: React.ReactNode }> = ({
  me,
  children,
}) => {
  const isSuperAdmin = me.role_level === 'super_admin' || me.username === 'admin';
  const allowedPlatforms: Platform[] =
    isSuperAdmin
      ? ['zdxlz', 'wecom']
      : Array.isArray(me.allowed_platforms) && me.allowed_platforms.length > 0
        ? me.allowed_platforms
        : ['zdxlz'];

  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [memberDistinctTotal, setMemberDistinctTotal] = useState(0);
  const [stats, setStats] = useState<InstanceStats | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [adminAccounts, setAdminAccounts] = useState<AdminAccountItem[]>([]);
  const [loading, setLoading] = useState(true);
  // 超管默认看全部管理员 ('all')，也可切换看某个具体管理员 ('1', '2', ...)
  const [ownerFilter, setOwnerFilterState] = useState<string>('all');
  // 平台/管理员切换会立刻触发新一轮 refreshAll，上一轮的响应可能更慢才回来；
  // 用自增序号只认最后一轮，避免旧平台的数据覆盖当前平台的列表。
  const refreshSeq = useRef(0);

  const [platform, setPlatformState] = useState<Platform>(() => {
    const saved = (localStorage.getItem(PLATFORM_KEY) || '').trim() as Platform;
    const candidate: Platform = saved === 'zdxlz' || saved === 'wecom' ? saved : 'zdxlz';
    return allowedPlatforms.includes(candidate) ? candidate : allowedPlatforms[0] || 'zdxlz';
  });

  // 当切换登录账号或权限变更时，若当前 platform 不在 allowedPlatforms 内，自动切到首个允许的平台
  useEffect(() => {
    if (!allowedPlatforms.includes(platform)) {
      const fallback = allowedPlatforms[0] || 'zdxlz';
      setPlatformState(fallback);
      localStorage.setItem(PLATFORM_KEY, fallback);
    }
  }, [allowedPlatforms, platform]);

  const setPlatform = useCallback(
    (p: Platform) => {
      if (!allowedPlatforms.includes(p)) return;
      setPlatformState(p);
      localStorage.setItem(PLATFORM_KEY, p);
    },
    [allowedPlatforms],
  );

  const setOwnerFilter = useCallback((ownerId: string) => {
    setOwnerFilterState(ownerId || 'all');
  }, []);

  // 首次进入（没有本地记忆）时，跟随后端「当前启用通道」（受 allowedPlatforms 约束）。
  useEffect(() => {
    if (localStorage.getItem(PLATFORM_KEY)) return;
    void (async () => {
      try {
        const res = await api.get<{ active_channel?: string }>('/api/admin/channel-config');
        if (!localStorage.getItem(PLATFORM_KEY)) {
          const preferred: Platform =
            res?.active_channel === 'wecom' || res?.active_channel === 'wecom_aibot'
              ? 'wecom'
              : 'zdxlz';
          if (allowedPlatforms.includes(preferred)) {
            setPlatformState(preferred);
          }
        }
      } catch {
        /* 读不到就保持默认 */
      }
    })();
  }, [allowedPlatforms]);

  const ownerQuery = isSuperAdmin && ownerFilter ? `&owner_id=${encodeURIComponent(ownerFilter)}` : '';

  const refreshTemplates = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: TemplateItem[] }>(
      `/api/admin/templates?size=500&platform=${platform}${ownerQuery}`,
    );
    setTemplates(res.items || []);
  }, [platform, ownerQuery]);

  const refreshRoles = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: RoleItem[]; member_distinct_total?: number }>(
      `/api/admin/roles?platform=${platform}${ownerQuery}`,
    );
    setRoles(res.items || []);
    /* 2026-11-05：在册人数改用后端按人去重口径（一人多角色只算一次） */
    setMemberDistinctTotal(Number(res.member_distinct_total || 0));
  }, [platform, ownerQuery]);

  const refreshStats = useCallback(async () => {
    const res = await api.get<InstanceStats>(
      `/api/admin/instances/stats?platform=${platform}${ownerQuery}`,
    );
    setStats(res);
  }, [platform, ownerQuery]);

  const refreshAdminAccounts = useCallback(async () => {
    if (!isSuperAdmin) return;
    try {
      /* 2026-11-06 平台口径：账号卡片统计（角色/成员/模板/今日实例）按当前办公软件过滤 */
      const res = await api.get<{ ok: boolean; items: AdminAccountItem[] }>(
        `/api/admin/accounts?platform=${platform}`,
      );
      setAdminAccounts(res.items || []);
    } catch (e) {
      console.warn('刷新管理员账号列表失败', e);
    }
  }, [isSuperAdmin, platform]);

  const refreshAll = useCallback(async () => {
    const seq = ++refreshSeq.current;
    try {
      const [tplRes, roleRes, statRes, healthRes] = await Promise.all([
        api.get<{ ok: boolean; items: TemplateItem[] }>(
          `/api/admin/templates?size=500&platform=${platform}${ownerQuery}`,
        ),
        api.get<{ ok: boolean; items: RoleItem[]; member_distinct_total?: number }>(
          `/api/admin/roles?platform=${platform}${ownerQuery}`,
        ),
        api.get<InstanceStats>(`/api/admin/instances/stats?platform=${platform}${ownerQuery}`),
        api.get<AdminHealth>('/api/admin/health'),
      ]);
      if (seq !== refreshSeq.current) return;
      setTemplates(tplRes.items || []);
      setRoles(roleRes.items || []);
      setMemberDistinctTotal(Number(roleRes.member_distinct_total || 0));
      setStats(statRes);
      setHealth(healthRes);
      if (isSuperAdmin) {
        await refreshAdminAccounts();
      }
    } catch (e) {
      console.warn('刷新后台数据失败', e);
    } finally {
      if (seq === refreshSeq.current) setLoading(false);
    }
  }, [platform, ownerQuery, isSuperAdmin, refreshAdminAccounts]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  return (
    <AppContext.Provider
      value={{
        me,
        isSuperAdmin,
        allowedPlatforms,
        ownerFilter,
        setOwnerFilter,
        adminAccounts,
        templates,
        roles,
        stats,
        health,
        loading,
        platform,
        setPlatform,
        memberDistinctTotal,
        refreshAll,
        refreshTemplates,
        refreshRoles,
        refreshStats,
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
