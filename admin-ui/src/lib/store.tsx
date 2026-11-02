import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api';
import { AdminHealth, InstanceStats, RoleItem, TemplateItem } from './types';

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
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [stats, setStats] = useState<InstanceStats | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [loading, setLoading] = useState(true);
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
    const res = await api.get<{ ok: boolean; items: TemplateItem[] }>(`/api/admin/templates?size=500&platform=${platform}`);
    setTemplates(res.items || []);
  }, [platform]);

  const refreshRoles = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: RoleItem[] }>(`/api/admin/roles?platform=${platform}`);
    setRoles(res.items || []);
  }, [platform]);

  const refreshStats = useCallback(async () => {
    const res = await api.get<InstanceStats>(`/api/admin/instances/stats?platform=${platform}`);
    setStats(res);
  }, [platform]);

  const refreshAll = useCallback(async () => {
    try {
      const [tplRes, roleRes, statRes, healthRes] = await Promise.all([
        api.get<{ ok: boolean; items: TemplateItem[] }>(`/api/admin/templates?size=500&platform=${platform}`),
        api.get<{ ok: boolean; items: RoleItem[] }>(`/api/admin/roles?platform=${platform}`),
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
  }, [platform]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

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
