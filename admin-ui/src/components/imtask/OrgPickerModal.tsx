import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { X, Building2, Users, ChevronRight, Home, RefreshCw, CheckSquare, Square, UserCheck, Loader2 } from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useToast } from '../../lib/toast';

/**
 * 2026-11-11 任务二：组织架构选择器（Org Picker）。
 *
 * 交互形态（镜像客户端）：面包屑 + 文件夹下钻 + 人员勾选。
 * · 面包屑：德阳分公司 > 市公司本部 > 云中台 > 产数支撑中心，点击上级瞬间回退；
 * · 有子部门 → 文件夹卡片（含人数）+「直接选择该部门全员」；
 * · 末级/任意部门 → 成员列表（姓名、职位、手机号脱敏、已在册置灰禁选）；
 * · 顶部「全选当前部门所有人」；底部「已选择 X 人 → 确认加入当前角色」；
 * · 确认后调 POST /api/admin/roles/{id}/import-from-org，入册+激活同一事务完成。
 */

interface OrgDept {
  dept_id: string;
  name: string;
  has_children: boolean | null;
  user_count: number;
}

interface OrgUser {
  name: string;
  title: string;
  phone: string;
  masked_phone: string;
  external_id: string;
  department: string;
  already_in_role: boolean;
}

interface Crumb {
  dept_id: string;
  name: string;
}

interface Props {
  open: boolean;
  roleId: string;
  roleName: string;
  platform: string;
  onClose: () => void;
  onImported: (summary: { added: number; activated: number; skipped: number }) => void;
}

export const OrgPickerModal: React.FC<Props> = ({ open, roleId, roleName, platform, onClose, onImported }) => {
  const toast = useToast();
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const [depts, setDepts] = useState<OrgDept[]>([]);
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [selected, setSelected] = useState<Record<string, OrgUser>>({});
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState('');
  const [configured, setConfigured] = useState(true);

  const currentDeptId = crumbs.length ? crumbs[crumbs.length - 1].dept_id : '';

  const loadDepts = useCallback(
    async (parentId: string) => {
      setLoading(true);
      setNotice('');
      try {
        const res = await api.get<{ ok: boolean; configured?: boolean; items: OrgDept[]; message?: string }>(
          `/api/admin/org/departments?platform=${encodeURIComponent(platform)}${parentId ? `&parent_id=${encodeURIComponent(parentId)}` : ''}`,
        );
        setDepts(res.items || []);
        setConfigured(res.configured !== false);
        if (res.configured === false || (res.message && !(res.items || []).length)) {
          setNotice(res.message || '');
        }
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载部门失败');
      } finally {
        setLoading(false);
      }
    },
    [platform, toast],
  );

  const loadUsers = useCallback(
    async (deptId: string, deptName: string) => {
      if (!deptId) return;
      setLoadingUsers(true);
      try {
        const res = await api.get<{ ok: boolean; items: OrgUser[]; configured?: boolean }>(
          `/api/admin/org/department-users?platform=${encodeURIComponent(platform)}&dept_id=${encodeURIComponent(deptId)}` +
            `&dept_name=${encodeURIComponent(deptName)}&role_id=${encodeURIComponent(roleId)}`,
        );
        setUsers(res.items || []);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载部门成员失败');
      } finally {
        setLoadingUsers(false);
      }
    },
    [platform, roleId, toast],
  );

  // 打开时重置到根
  useEffect(() => {
    if (open) {
      setCrumbs([]);
      setUsers([]);
      setSelected({});
      setNotice('');
      void loadDepts('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, platform]);

  // 切层时：根层不拉成员；进入某层 → 该部门直属成员 + 子部门
  useEffect(() => {
    if (!open) return;
    if (currentDeptId && crumbs.length) {
      const name = crumbs[crumbs.length - 1].name;
      void loadDepts(currentDeptId);
      void loadUsers(currentDeptId, name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDeptId]);

  const enterDept = (d: OrgDept) => {
    setCrumbs((prev) => [...prev, { dept_id: d.dept_id, name: d.name }]);
    setSelected({});
  };

  const jumpTo = (idx: number) => {
    setCrumbs((prev) => prev.slice(0, idx + 1));
    setSelected({});
  };

  const selectable = useMemo(() => users.filter((u) => !u.already_in_role && u.phone), [users]);
  const allSelected = selectable.length > 0 && selectable.every((u) => selected[u.phone]);
  const selectedCount = Object.keys(selected).filter((p) => !users.find((u) => u.phone === p)?.already_in_role).length
    // 跨层已选（面包屑回退后保留的选择）
    + Object.keys(selected).filter((p) => !users.find((u) => u.phone === p)).length;

  const toggleUser = (u: OrgUser) => {
    if (u.already_in_role) return;
    setSelected((prev) => {
      const next = { ...prev };
      if (next[u.phone]) delete next[u.phone];
      else next[u.phone] = u;
      return next;
    });
  };

  const toggleAllCurrent = () => {
    setSelected((prev) => {
      const next = { ...prev };
      if (allSelected) {
        selectable.forEach((u) => delete next[u.phone]);
      } else {
        selectable.forEach((u) => { next[u.phone] = u; });
      }
      return next;
    });
  };

  const selectWholeDept = () => {
    // 「直接选择该部门全员」：勾满当前层可选项（跨层选择保留）
    if (!selectable.length) return;
    setSelected((prev) => {
      const next = { ...prev };
      selectable.forEach((u) => { next[u.phone] = u; });
      return next;
    });
    toast.success(`已勾选「${crumbs[crumbs.length - 1]?.name || ''}」直属成员 ${selectable.length} 人`, '已全选该部门');
  };

  const submit = async () => {
    const usersToImport = Object.values(selected);
    if (!usersToImport.length) {
      toast.warning('请先勾选成员');
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.post<{ ok: boolean; added: number; activated: number; skipped: number; invalid?: { reason: string }[] }>(
        `/api/admin/roles/${encodeURIComponent(roleId)}/import-from-org`,
        { selected_users: usersToImport.map((u) => ({ name: u.name, phone: u.phone, external_id: u.external_id, department: currentDeptId ? crumbs[crumbs.length - 1].name : (u.department || '') })) },
      );
      onImported({ added: res.added, activated: res.activated, skipped: res.skipped });
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '导入未成功');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-2xl w-full shadow-2xl border border-slate-200 flex flex-col max-h-[85vh]">
        {/* 标题 */}
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5 shrink-0">
          <div>
            <h3 className="font-bold text-slate-900 text-base flex items-center gap-2">
              <Building2 className="w-4.5 h-4.5 text-blue-600" />
              从组织架构添加成员至「{roleName}」
            </h3>
            <p className="text-[11px] text-slate-400">
              按部门下钻选人，确认后同步入册并自动激活（{platform === 'wecom' ? '企业微信' : '量子密信'}）
            </p>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 面包屑 */}
        <div className="px-5 py-2.5 bg-slate-50/70 border-b border-slate-100 flex items-center gap-1 text-xs overflow-x-auto shrink-0">
          <button
            onClick={() => { setCrumbs([]); setSelected({}); }}
            className={`flex items-center gap-1 shrink-0 cursor-pointer ${crumbs.length === 0 ? 'text-blue-600 font-bold' : 'text-slate-500 hover:text-blue-600'}`}
          >
            <Home className="w-3.5 h-3.5" />
            <span>顶级</span>
          </button>
          {crumbs.map((c, i) => (
            <React.Fragment key={c.dept_id}>
              <ChevronRight className="w-3.5 h-3.5 text-slate-300 shrink-0" />
              <button
                onClick={() => jumpTo(i)}
                className={`shrink-0 cursor-pointer ${i === crumbs.length - 1 ? 'text-blue-600 font-bold' : 'text-slate-500 hover:text-blue-600'}`}
              >
                {c.name}
              </button>
            </React.Fragment>
          ))}
          {loading && <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin ml-1 shrink-0" />}
        </div>

        {notice && (
          <div className="mx-5 mt-3 rounded-xl border border-amber-200 bg-amber-50/80 px-3.5 py-2.5 text-[11px] text-amber-800 leading-5">
            {notice}
          </div>
        )}

        {/* 列表区 */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-[220px]">
          {/* 子部门文件夹 */}
          {depts.length > 0 && (
            <div className="grid grid-cols-2 gap-2.5">
              {depts.map((d) => (
                <div
                  key={d.dept_id}
                  className="group rounded-xl border border-slate-200 hover:border-blue-300 bg-white px-3.5 py-3 flex items-center justify-between gap-2 transition-colors"
                >
                  <button
                    onClick={() => enterDept(d)}
                    className="flex items-center gap-2.5 min-w-0 flex-1 text-left cursor-pointer"
                    title={d.has_children ? '点击进入下级' : '点击查看部门成员'}
                  >
                    <span className="w-8 h-8 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 group-hover:bg-blue-100">
                      <Building2 className="w-4 h-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold text-slate-800 truncate">{d.name}</span>
                      <span className="block text-[10px] text-slate-400">
                        {d.user_count > 0 ? `${d.user_count} 人` : ''}
                        {d.has_children === false ? ' · 末级部门' : d.has_children ? '' : ''}
                      </span>
                    </span>
                  </button>
                  <button
                    onClick={() => { enterDept(d); setTimeout(selectWholeDept, 350); }}
                    className="shrink-0 text-[10px] font-semibold text-blue-600 hover:text-white bg-blue-50 hover:bg-blue-600 border border-blue-200/70 rounded-lg px-2 py-1 cursor-pointer transition-colors"
                    title="进入该部门并勾选全部直属成员"
                  >
                    选全员
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* 当前层直属成员 */}
          {currentDeptId && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-[11px] font-bold text-slate-500 flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5" />
                  <span>
                    {crumbs[crumbs.length - 1]?.name} 直属成员（{users.length} 人
                    {users.filter((u) => u.already_in_role).length > 0
                      ? `，${users.filter((u) => u.already_in_role).length} 人已在当前角色`
                      : ''}
                    ）
                  </span>
                </div>
                {selectable.length > 0 && (
                  <button
                    onClick={toggleAllCurrent}
                    className="flex items-center gap-1 text-[11px] font-semibold text-blue-600 hover:text-blue-700 cursor-pointer"
                  >
                    {allSelected ? <CheckSquare className="w-3.5 h-3.5" /> : <Square className="w-3.5 h-3.5" />}
                    {allSelected ? '取消全选' : '全选当前部门所有人'}
                  </button>
                )}
              </div>
              {loadingUsers ? (
                <div className="flex items-center justify-center py-8 text-xs text-slate-400 gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> 加载成员中…
                </div>
              ) : users.length === 0 ? (
                <div className="text-center py-8 text-xs text-slate-400">
                  该部门暂无直属成员{depts.length > 0 ? '（可进入上方子部门继续选择）' : ''}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-200 divide-y divide-slate-100 overflow-hidden">
                  {users.map((u) => (
                    <button
                      key={u.phone + u.external_id}
                      onClick={() => toggleUser(u)}
                      disabled={u.already_in_role}
                      className={`w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors ${
                        u.already_in_role
                          ? 'bg-slate-50 cursor-not-allowed opacity-70'
                          : selected[u.phone]
                            ? 'bg-blue-50/80 hover:bg-blue-50 cursor-pointer'
                            : 'bg-white hover:bg-slate-50 cursor-pointer'
                      }`}
                    >
                      {u.already_in_role ? (
                        <UserCheck className="w-4 h-4 text-slate-300 shrink-0" />
                      ) : selected[u.phone] ? (
                        <CheckSquare className="w-4 h-4 text-blue-600 shrink-0" />
                      ) : (
                        <Square className="w-4 h-4 text-slate-300 shrink-0" />
                      )}
                      <span className="min-w-0 flex-1 flex items-baseline gap-2">
                        <span className="text-xs font-semibold text-slate-800 truncate">{u.name}</span>
                        {u.title ? <span className="text-[10px] text-slate-400 truncate shrink-0">{u.title}</span> : null}
                      </span>
                      {u.already_in_role ? (
                        <span className="text-[10px] text-slate-400 bg-slate-100 rounded-md px-1.5 py-0.5 shrink-0">已在册</span>
                      ) : (
                        <span className="text-[10px] text-slate-400 font-mono shrink-0">{u.masked_phone || u.phone}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {!loading && !currentDeptId && depts.length === 0 && !notice && (
            <div className="text-center py-10 text-xs text-slate-400">暂无部门数据</div>
          )}
        </div>

        {/* 底部确认 */}
        <div className="border-t border-slate-100 px-5 py-3.5 flex items-center justify-between shrink-0 bg-white rounded-b-2xl">
          <div className="text-xs text-slate-500">
            已选择 <span className="font-bold text-blue-600 text-sm">{selectedCount}</span> 人
            <span className="text-[10px] text-slate-400">（跨层勾选会保留，回退不丢失）</span>
          </div>
          <div className="flex items-center gap-2.5">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold text-xs cursor-pointer transition-colors"
            >
              取消
            </button>
            <button
              onClick={submit}
              disabled={submitting || selectedCount === 0}
              className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors"
            >
              {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserCheck className="w-3.5 h-3.5" />}
              确认加入当前角色
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OrgPickerModal;
