import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api';
import { AdminHealth, InstanceStats, RoleItem, TemplateItem } from './types';

interface AppContextType {
  templates: TemplateItem[];
  roles: RoleItem[];
  stats: InstanceStats | null;
  health: AdminHealth | null;
  loading: boolean;
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

  const refreshTemplates = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: TemplateItem[] }>('/api/admin/templates');
    setTemplates(res.items || []);
  }, []);

  const refreshRoles = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: RoleItem[] }>('/api/admin/roles');
    setRoles(res.items || []);
  }, []);

  const refreshStats = useCallback(async () => {
    const res = await api.get<InstanceStats>('/api/admin/instances/stats');
    setStats(res);
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [tplRes, roleRes, statRes, healthRes] = await Promise.all([
        api.get<{ ok: boolean; items: TemplateItem[] }>('/api/admin/templates'),
        api.get<{ ok: boolean; items: RoleItem[] }>('/api/admin/roles'),
        api.get<InstanceStats>('/api/admin/instances/stats'),
        api.get<AdminHealth>('/api/admin/health'),
      ]);
      setTemplates(tplRes.items || []);
      setRoles(roleRes.items || []);
      setStats(statRes);
      setHealth(healthRes);
    } finally {
      setLoading(false);
    }
  }, []);

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
