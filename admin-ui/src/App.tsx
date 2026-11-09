import React, { useState } from 'react';
import {
  ClipboardCheck,
  FileSpreadsheet,
  Users,
  CalendarCheck2,
  ShieldCheck,
  Radio,
} from 'lucide-react';
import { AppProvider, useAppStore } from './lib/store';
import { AdminMe, AdminUserCard } from './components/system/AdminUserCard';
import { TemplatesView } from './components/imtask/TemplatesView';
import { RolesMembersView } from './components/imtask/RolesMembersView';
import { InstancesView } from './components/imtask/InstancesView';
import { AdminAccountsView } from './components/imtask/AdminAccountsView';
import ChannelSettingsView from './components/imtask/ChannelSettingsView';

type TabKey = 'instances' | 'templates' | 'roles' | 'accounts' | 'channels';

interface MainAppProps {
  me: AdminMe;
  onLoggedOut: () => void;
}

const MainApp: React.FC<MainAppProps> = ({ me, onLoggedOut }) => {
  const { loading, templates, roles, stats, platform } = useAppStore();
  const [activeTab, setActiveTab] = useState<TabKey>('instances');

  const isSuperAdmin = me.role_level !== 'sub_admin';
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
      {/* 左侧导航栏 */}
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
                {/* 2026-11-09 用户要求：移除左上角「管理后台」标签（只留平台标签，那是有业务含义的当前办公软件指示） */}
                {/* 2026-11-06 用户要求：当前所选办公软件实时渲染在左上角（超管/分级同口径，醒目展示） */}
                <span
                  className={`text-[9px] font-bold px-1.5 py-0.5 rounded text-white inline-flex items-center gap-0.5 shrink-0 ${
                    platform === 'wecom' ? 'bg-blue-600' : 'bg-emerald-600'
                  }`}
                  title={`当前展示与操作的是「${platform === 'wecom' ? '企业微信' : '量子密信'}」的数据；可在左下角账户卡片中切换`}
                >
                  <Radio className="w-2.5 h-2.5" />
                  {platform === 'wecom' ? '企业微信' : '量子密信'}
                </span>
              </div>
            </div>
          </div>

          {/* 核心调度工作台 */}
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

          {/* 系统设置（仅超级管理员可见） */}
          {isSuperAdmin && (
            <nav className="space-y-1">
              <div className="px-2.5 pb-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                系统设置
              </div>
              <button
                onClick={() => setActiveTab('accounts')}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                  activeTab === 'accounts'
                    ? 'bg-blue-600 text-white shadow-xs font-semibold'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <ShieldCheck
                    className={`w-4 h-4 ${activeTab === 'accounts' ? 'text-white' : 'text-slate-400'}`}
                  />
                  <span>管理员账号管理</span>
                </div>
              </button>

              <button
                onClick={() => setActiveTab('channels')}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                  activeTab === 'channels'
                    ? 'bg-blue-600 text-white shadow-xs font-semibold'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Radio
                    className={`w-4 h-4 ${activeTab === 'channels' ? 'text-white' : 'text-slate-400'}`}
                  />
                  <span>通道配置</span>
                </div>
              </button>
            </nav>
          )}
        </div>

        {/* 底部：管理员卡片（TODO 2026-11-05：「获取最新代码与 Agent 指令」按钮后端 /api/agent-bundle 未实现，待补齐后恢复） */}
        <div className="p-3 border-t border-slate-100 space-y-2">
          <AdminUserCard me={me} onLoggedOut={onLoggedOut} />
        </div>
      </aside>

      {/* 右侧主内容画布 */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-[1760px] mx-auto p-6">
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
              {activeTab === 'accounts' && isSuperAdmin && <AdminAccountsView />}
              {activeTab === 'channels' && isSuperAdmin && <ChannelSettingsView />}
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
    <AppProvider me={me}>
      <MainApp me={me} onLoggedOut={onLoggedOut} />
    </AppProvider>
  );
}
