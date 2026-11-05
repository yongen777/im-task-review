import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { useAppStore } from '../../lib/store';

interface AdminScopeHeaderProps {
  title: string;
  badge?: string;
  description: React.ReactNode;
  actions?: React.ReactNode;
}

export const AdminScopeHeader: React.FC<AdminScopeHeaderProps> = ({
  title,
  badge,
  description,
  actions,
}) => {
  const { isSuperAdmin, ownerFilter, setOwnerFilter, adminAccounts } = useAppStore();

  // 仅列出分级管理员账号（排除超管 admin，因为超管视角本身就是首选）
  const subAdmins = adminAccounts.filter(
    (a) => a.role_level === 'sub_admin' || a.username !== 'admin',
  );

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-5 rounded-2xl border border-slate-200/90 shadow-2xs">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-bold text-slate-900 tracking-tight">{title}</h2>
          {badge && (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/60">
              {badge}
            </span>
          )}
        </div>
        <div className="text-xs text-slate-500 mt-1">{description}</div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5 shrink-0">
        {/* 仅超级管理员可见的视角切换器（严格遵守用户要求：绝对没有「全部管理员」，选项仅有「超级管理员」与各「分级管理员」） */}
        {isSuperAdmin && (
          <div className="flex items-center gap-2 bg-slate-50 border border-slate-200/90 rounded-xl px-3 py-1.5 text-xs shadow-2xs">
            <ShieldCheck className="w-4 h-4 text-blue-600 shrink-0" />
            <span className="text-slate-600 font-semibold shrink-0">管理员视角：</span>
            <select
              aria-label="管理员视角切换"
              value={ownerFilter}
              onChange={(e) => setOwnerFilter(e.target.value)}
              className="bg-transparent font-bold text-slate-900 outline-none cursor-pointer text-xs"
            >
              <option value="all">超级管理员</option>
              {subAdmins.map((acc) => (
                <option key={acc.id} value={String(acc.id)}>
                  {acc.display_name || acc.username} (@{acc.username})
                </option>
              ))}
            </select>
          </div>
        )}

        {actions}
      </div>
    </div>
  );
};
