import React, { useEffect, useState } from 'react';
import {
  ClipboardCheck,
  FileSpreadsheet,
  Settings2,
  Users,
  CalendarCheck2,
  UserCog,
  Shield,
} from 'lucide-react';
import { AppProvider, useAppStore } from './lib/store';
import { AdminMe, AdminUserCard } from './components/system/AdminUserCard';
import { TemplatesView } from './components/imtask/TemplatesView';
import { RolesMembersView } from './components/imtask/RolesMembersView';
import { InstancesView } from './components/imtask/InstancesView';
import { AdminAccountsView } from './components/imtask/AdminAccountsView';
import ChannelSettingsView from './components/imtask/ChannelSettingsView';

type TabKey = 'instances' | 'templates' | 'roles' | 'channels' | 'accounts';

interface MainAppProps {
  me: AdminMe;
  onLoggedOut: () => void;
}

const MainApp: React.FC<MainAppProps> = ({ me, onLoggedOut }) => {
  const { loading, templates, roles, stats, adminAccounts, ownerFilter, setOwnerFilter, refreshAll, refreshAdminAccounts } = useAppStore();
  const [activeTab, setActiveTab] = useState<TabKey>('instances');
  /* 2026-11-04 多管理员分级：超级管理员 = 全局只读 + 账号管理。
     优先用 props.me（登录时已拿到），store 的 isSuperAdmin 作为兜底。 */
  const isSuperAdmin = me?.role_level === 'super_admin' || me?.is_super_admin === true;

  /* 2026-10-30 首次安装引导：全部通道都未配置时，打开后台直接进「通道配置」——
     否则管理员看到的是一个永远发不出消息的空系统，不知道该先干什么。 */
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch('/api/admin/channel-config', { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = (await res.json()) as { any_configured?: boolean };
        if (alive && data?.any_configured === false) setActiveTab('channels');
      } catch {
        /* 读不到就保持默认页，不影响正常使用 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 2026-11-04 多管理员分级：超管进入时拉一次管理员账号列表（供「所属管理员筛选」用）
  useEffect(() => {
    if (!isSuperAdmin) return;
    void refreshAdminAccounts();
  }, [isSuperAdmin, refreshAdminAccounts]);

  const submittedCount = stats?.by_status?.submitted || 0;
  const enabledTemplatesCount = templates.filter((t) => t.enabled).length;

  const navItems: Array<{
    id: TabKey;
    label: string;
    icon: React.FC<{ className?: string }>;
    badge?: string | number;
    badgeTone?: 'blue' | 'slate';
  }> = [
    {
      id: 'instances',
      label: '任务实例与审核',
      icon: ClipboardCheck,
      badge: submittedCount > 0 ? `${submittedCount} 待审` : undefined,
      badgeTone: 'blue',
    },
    {
      id: 'templates',
      label: '任务模板管理',
      icon: FileSpreadsheet,
      badge: `${enabledTemplatesCount}/${templates.length}`,
      badgeTone: 'slate',
    },
    {
      id: 'roles',
      label: '角色与成员名册',
      icon: Users,
      badge: roles.length,
      badgeTone: 'slate',
    },
  ];

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[#f7f8fa] text-slate-900">
      {/* 左侧导航栏（三大核心工作台） */}
      <aside className="w-60 bg-white border-r border-slate-200/90 flex flex-col justify-between shrink-0 select-none z-20">
        <div className="p-4 space-y-5">
          {/* 顶部品牌 */}
          <div className="flex items-center gap-2.5 px-1.5 pt-0.5">
            <div className="w-9 h-9 rounded-xl bg-blue-600 flex items-center justify-center shadow-xs shrink-0 text-white">
              <CalendarCheck2 className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h1 className="font-bold text-sm text-slate-900 tracking-tight leading-tight truncate">
                IMTask 任务调度平台
              </h1>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-[10px] text-slate-400 font-mono">im-task</span>
                <span className="text-[9px] text-blue-600 bg-blue-50 px-1.5 py-0.2 rounded font-medium">
                  管理后台
                </span>
              </div>
            </div>
          </div>

          {/* 导航菜单 */}
          <nav className="space-y-1">
            <div className="px-2.5 pb-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
              核心调度工作台
            </div>
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                    isActive
                      ? 'bg-blue-600 text-white shadow-xs font-semibold'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                    <span>{item.label}</span>
                  </div>
                  {item.badge !== undefined && (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded-md font-semibold ${
                        isActive
                          ? 'bg-white/20 text-white'
                          : item.badgeTone === 'blue'
                            ? 'bg-blue-50 text-blue-600 border border-blue-200/70'
                            : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          {/* 2026-10-30 系统设置：新增分组，⛔ 不动上面既有导航与布局
              2026-11-04 多管理员分级：通道配置与账号管理仅超级管理员可见 */}
          <nav className="space-y-1">
            <div className="px-2.5 pb-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
              系统设置
            </div>
            {isSuperAdmin && (
              <button
                onClick={() => setActiveTab('accounts')}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                  activeTab === 'accounts'
                    ? 'bg-blue-600 text-white shadow-xs font-semibold'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <UserCog className={`w-4 h-4 ${activeTab === 'accounts' ? 'text-white' : 'text-slate-400'}`} />
                  <span>管理员账号管理</span>
                </div>
              </button>
            )}
            {isSuperAdmin && (
              <button
                onClick={() => setActiveTab('channels')}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                  activeTab === 'channels'
                    ? 'bg-blue-600 text-white shadow-xs font-semibold'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Settings2
                    className={`w-4 h-4 ${activeTab === 'channels' ? 'text-white' : 'text-slate-400'}`}
                  />
                  <span>通道配置</span>
                </div>
              </button>
            )}
          </nav>
        </div>

        {/* 底部：管理员卡片（2026-10-28 用户指令：移除获取代码入口） */}
        <div className="p-3 border-t border-slate-100 space-y-2.5">

          <AdminUserCard me={me} onLoggedOut={onLoggedOut} />
        </div>
      </aside>

      {/* 右侧主内容画布 */}
      <main className="flex-1 overflow-y-auto">
        {/* 2026-11-03 用户口径：整体拉长变宽（原 max-w-7xl 在宽屏两侧留白过多） */}
        <div className="max-w-[1760px] mx-auto p-6">
          {/* 2026-11-04 多管理员分级：超管专用的「全局查看条」 */}
          {isSuperAdmin && (
            <div className="mb-4 bg-white rounded-2xl border border-slate-200/90 shadow-2xs px-4 py-3 flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-blue-600" />
                <span className="text-xs font-bold text-slate-700">超级管理员 · 全局查看</span>
              </div>
              <span className="text-[11px] text-slate-500">
                你可以看到全部分级管理员创建的任务、角色与成员；业务数据由分级管理员维护。
              </span>
              <div className="ml-auto flex items-center gap-2">
                <label className="text-[11px] text-slate-500">所属管理员</label>
                <select
                  value={ownerFilter}
                  onChange={(e) => {
                    setOwnerFilter(e.target.value);
                    void refreshAll();
                  }}
                  className="px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                >
                  <option value="">全部管理员</option>
                  {adminAccounts.map((a) => (
                    <option key={a.id} value={String(a.id)}>
                      {a.display_name || a.username}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
          {loading ? (
            <div className="space-y-4">
              <div className="h-20 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
              <div className="h-96 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
            </div>
          ) : (
            <>
              {activeTab === 'instances' && <InstancesView />}
              {activeTab === 'templates' && <TemplatesView />}
              {activeTab === 'roles' && <RolesMembersView />}
              {activeTab === 'channels' && isSuperAdmin && <ChannelSettingsView />}
              {activeTab === 'accounts' && isSuperAdmin && <AdminAccountsView />}
            </>
          )}
        </div>
      </main>

    </div>
  );
};

export default function App({
  me,
  onLoggedOut,
}: {
  me: AdminMe;
  onLoggedOut: () => void;
}) {
  return (
    <AppProvider>
      <MainApp me={me} onLoggedOut={onLoggedOut} />
    </AppProvider>
  );
}
