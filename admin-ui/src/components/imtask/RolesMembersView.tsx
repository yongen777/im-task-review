import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Users,
  UserPlus,
  Upload,
  Edit3,
  Trash2,
  CheckCircle2,
  Clock,
  Ban,
  Plus,
  X,
  Lock,
  Phone,
  User,
  ShieldCheck,
  ArrowRightCircle,
  UserCheck,
  Search,
  MessageSquare,
  Link2Off,
  AlertTriangle,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { RoleItem, RoleMemberItem, UnactivatedMemberItem } from '../../lib/types';

export const RolesMembersView: React.FC = () => {
  const { roles, refreshRoles } = useAppStore();
  const toast = useToast();

  const [selectedRoleId, setSelectedRoleId] = useState<string>('');
  const [members, setMembers] = useState<RoleMemberItem[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [memberKeyword, setMemberKeyword] = useState('');

  // 已激活待归纳角色人员列表、搜索查询与一键移入角色分组状态
  const [unactivatedList, setUnactivatedList] = useState<UnactivatedMemberItem[]>([]);
  const [loadingUnactivated, setLoadingUnactivated] = useState(false);
  const [unactivatedKeyword, setUnactivatedKeyword] = useState('');
  const [rowTargetRoleMap, setRowTargetRoleMap] = useState<Record<string, string>>({});
  const [batchTargetRoleId, setBatchTargetRoleId] = useState<string>('');
  const [movingPhone, setMovingPhone] = useState<string | null>(null);
  const [batchMoving, setBatchMoving] = useState(false);

  // 两个移除动作的交互确认弹窗状态
  // 1) 从当前角色移除成员 -> 移入右侧「已激活待归纳角色人员」列表（保留会话绑定）
  const [confirmRemoveRoleMember, setConfirmRemoveRoleMember] = useState<RoleMemberItem | null>(null);
  const [removingRoleMember, setRemovingRoleMember] = useState(false);
  // 2) 从「已激活待归纳角色人员」移除 -> 解除量子密信会话绑定，需用户重新对话输入激活
  const [confirmUnbindPerson, setConfirmUnbindPerson] = useState<UnactivatedMemberItem | null>(null);
  const [unbindingPerson, setUnbindingPerson] = useState(false);

  // 单条添加成员表单（仅需用户名与手机号）
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  // 新建/编辑角色弹窗（无需角色编码）
  const [roleModalMode, setRoleModalMode] = useState<'none' | 'create' | 'edit'>('none');
  const [editingRoleId, setEditingRoleId] = useState('');
  const [roleNameInput, setRoleNameInput] = useState('');
  const [savingRole, setSavingRole] = useState(false);

  // P2-1: 成员「改名/停用」弹窗（手机号只读不可改，移除所属部门）
  const [editingMember, setEditingMember] = useState<RoleMemberItem | null>(null);
  const [editMemberName, setEditMemberName] = useState('');
  const [editMemberStatus, setEditMemberStatus] = useState<'active' | 'disabled'>('active');
  const [savingMember, setSavingMember] = useState(false);

  // CSV 批量导入弹窗（仅手机号与用户名）
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [csvText, setCsvText] = useState('13900000000,张三');
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    if (!selectedRoleId && roles.length > 0) {
      setSelectedRoleId(roles[0].role_id);
    } else if (selectedRoleId && !roles.some((r) => r.role_id === selectedRoleId)) {
      setSelectedRoleId(roles[0]?.role_id || '');
    }
    if (!batchTargetRoleId && roles.length > 0) {
      setBatchTargetRoleId(roles[0].role_id);
    } else if (batchTargetRoleId && !roles.some((r) => r.role_id === batchTargetRoleId)) {
      setBatchTargetRoleId(roles[0]?.role_id || '');
    }
  }, [roles, selectedRoleId, batchTargetRoleId]);

  const loadMembers = useCallback(
    async (roleId: string) => {
      if (!roleId) {
        setMembers([]);
        return;
      }
      setLoadingMembers(true);
      try {
        const res = await api.get<{ ok: boolean; items: RoleMemberItem[] }>(
          `/api/admin/roles/${encodeURIComponent(roleId)}/members`,
        );
        setMembers(res.items || []);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载角色成员失败');
      } finally {
        setLoadingMembers(false);
      }
    },
    [toast],
  );

  const loadUnactivated = useCallback(async () => {
    setLoadingUnactivated(true);
    try {
      const res = await api.get<{ ok: boolean; items: UnactivatedMemberItem[] }>(
        '/api/admin/unactivated-members',
      );
      setUnactivatedList(res.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '加载已激活待归纳人员失败');
    } finally {
      setLoadingUnactivated(false);
    }
  }, [toast]);

  useEffect(() => {
    loadUnactivated();
  }, [loadUnactivated]);

  useEffect(() => {
    if (selectedRoleId) {
      loadMembers(selectedRoleId);
    }
  }, [selectedRoleId, loadMembers]);

  const currentRole: RoleItem | undefined = useMemo(
    () => roles.find((r) => r.role_id === selectedRoleId),
    [roles, selectedRoleId],
  );

  const totalRosterCount = useMemo(
    () => roles.reduce((acc, r) => acc + (r.member_count || 0), 0),
    [roles],
  );

  const filteredMembers = useMemo(() => {
    if (!memberKeyword.trim()) return members;
    const kw = memberKeyword.trim().toLowerCase();
    return members.filter(
      (m) => m.phone.includes(kw) || m.name.toLowerCase().includes(kw),
    );
  }, [members, memberKeyword]);

  const filteredUnactivatedList = useMemo(() => {
    if (!unactivatedKeyword.trim()) return unactivatedList;
    const kw = unactivatedKeyword.trim().toLowerCase();
    return unactivatedList.filter(
      (u) => u.phone.includes(kw) || u.name.toLowerCase().includes(kw),
    );
  }, [unactivatedList, unactivatedKeyword]);

  const getRowTargetRole = (phone: string): string => {
    const chosen = rowTargetRoleMap[phone];
    if (chosen && roles.some((r) => r.role_id === chosen)) {
      return chosen;
    }
    return selectedRoleId || roles[0]?.role_id || '';
  };

  // 一键将单个已激活待归纳人员归入指定角色分组
  const handleMoveSingleUnactivated = async (person: UnactivatedMemberItem) => {
    const targetRoleId = getRowTargetRole(person.phone);
    if (!targetRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    setMovingPhone(person.phone);
    try {
      // 2026-10-28 修：改用后端正式路径 pending-members/move，按 external_id 定位
      // （原 unactivated-members + phone 的组合是历史 mock 契约，后端只有兼容别名）
      await api.post<{ ok: boolean; assignment?: { phone?: string; role_id?: string } }>(
        '/api/admin/pending-members/move',
        { external_id: person.external_id, role_id: targetRoleId },
      );
      await Promise.all([
        loadUnactivated(),
        refreshRoles(),
        selectedRoleId ? loadMembers(selectedRoleId) : Promise.resolve(),
      ]);
      const roleName = roles.find((r) => r.role_id === targetRoleId)?.role_name || targetRoleId;
      toast.success(
        `已将已激活人员「${person.name || person.phone}」(${person.phone}) 一键归入角色分组「${roleName}」。`,
        '一键移入成功',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移入角色分组失败');
    } finally {
      setMovingPhone(null);
    }
  };

  // 一键批量将当前列表中的已激活待归纳人员归入目标角色分组
  const handleBatchMoveAllUnactivated = async () => {
    const targetItems = filteredUnactivatedList.length > 0 ? filteredUnactivatedList : unactivatedList;
    if (targetItems.length === 0) return;
    const targetRoleId = batchTargetRoleId || selectedRoleId || roles[0]?.role_id || '';
    if (!targetRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    setBatchMoving(true);
    try {
      // 2026-10-28 修：后端 pending-members/move 是单人接口，批量逐条调用并统计成功数
      let moved = 0;
      let lastErr = '';
      for (const u of targetItems) {
        if (!u.external_id) {
          lastErr = '存在缺少用户标识的记录，请刷新页面后重试';
          continue;
        }
        try {
          await api.post('/api/admin/pending-members/move', {
            external_id: u.external_id,
            role_id: targetRoleId,
          });
          moved += 1;
        } catch (err) {
          lastErr = err instanceof Error ? plainError(err.message) : '移入失败';
        }
      }
      await Promise.all([
        loadUnactivated(),
        refreshRoles(),
        selectedRoleId ? loadMembers(selectedRoleId) : Promise.resolve(),
      ]);
      const roleName = roles.find((r) => r.role_id === targetRoleId)?.role_name || targetRoleId;
      if (moved > 0) {
        toast.success(
          `已将 ${moved} 名已激活待归纳人员一键归入「${roleName}」分组。`,
          '批量一键移入完成',
        );
      }
      if (lastErr) toast.error(lastErr, '部分未移入');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '批量移入失败');
    } finally {
      setBatchMoving(false);
    }
  };

  // 确认从「已激活待归纳角色人员」移除 -> 解除密信会话绑定，需用户重新输入激活
  const handleConfirmUnbindUnactivated = async () => {
    if (!confirmUnbindPerson) return;
    const person = confirmUnbindPerson;
    setUnbindingPerson(true);
    try {
      // 2026-10-28 修：按 external_id 删除（原来用 phone，列表未刷新时为空会删不掉）
      await api.delete(`/api/admin/pending-members/${encodeURIComponent(person.external_id || person.phone)}`);
      setConfirmUnbindPerson(null);
      await loadUnactivated();
      toast.success(
        `已解除「${person.name}」(${person.phone}) 的量子密信会话绑定并从待归纳列表移除。该用户需在密信对话窗口重新输入激活信息后方可再次激活。`,
        '已解除会话绑定',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移除并解除会话绑定失败');
    } finally {
      setUnbindingPerson(false);
    }
  };

  const handleOpenCreateRole = () => {
    setEditingRoleId('');
    setRoleNameInput('');
    setRoleModalMode('create');
  };

  const handleOpenEditRole = (role: RoleItem) => {
    setEditingRoleId(role.role_id);
    setRoleNameInput(role.role_name);
    setRoleModalMode('edit');
  };

  const handleSaveRole = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!roleNameInput.trim()) {
      toast.warning('请填写角色名称');
      return;
    }
    setSavingRole(true);
    try {
      if (roleModalMode === 'create') {
        const res = await api.post<{ ok: boolean; role_id: string }>('/api/admin/roles', {
          role_name: roleNameInput.trim(),
          description: '',
        });
        await refreshRoles();
        if (res.role_id) {
          setSelectedRoleId(res.role_id);
        }
        setRoleModalMode('none');
        toast.success(`角色「${roleNameInput.trim()}」创建成功。`, '角色已创建');
      } else if (roleModalMode === 'edit') {
        await api.put(`/api/admin/roles/${encodeURIComponent(editingRoleId)}`, {
          role_name: roleNameInput.trim(),
          description: '',
        });
        await refreshRoles();
        setRoleModalMode('none');
        toast.success(`角色名称已更新为「${roleNameInput.trim()}」。`, '修改已保存');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '保存角色失败');
    } finally {
      setSavingRole(false);
    }
  };

  const handleDeleteRole = async (role: RoleItem) => {
    try {
      await api.delete(`/api/admin/roles/${encodeURIComponent(role.role_id)}`);
      await refreshRoles();
      toast.success(`角色「${role.role_name}」已删除。`, '删除成功');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '删除角色失败');
    }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!newName.trim() || !newPhone.trim()) {
      toast.warning('请填写用户名和 11 位手机号');
      return;
    }
    setAddingMember(true);
    try {
      await api.post(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
        phone: newPhone.trim(),
        name: newName.trim(),
        dept: '',
      });
      const addedName = newName.trim();
      const addedPhone = newPhone.trim();
      setNewName('');
      setNewPhone('');
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      toast.success(`成员「${addedName}」(${addedPhone}) 已添加至当前角色。`, '添加成功');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '添加成员未成功');
    } finally {
      setAddingMember(false);
    }
  };

  // P2-1: 打开成员「改名/停用」弹窗
  const openEditMemberModal = (m: RoleMemberItem) => {
    setEditingMember(m);
    setEditMemberName(m.name);
    setEditMemberStatus(m.status);
  };

  // P2-1: 提交 PUT /api/admin/roles/{role_id}/members/{phone}
  const handleSaveMemberEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId || !editingMember) return;
    if (!editMemberName.trim()) {
      toast.warning('用户名不能为空');
      return;
    }
    setSavingMember(true);
    try {
      await api.put(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(editingMember.phone)}`,
        {
          name: editMemberName.trim(),
          dept: '',
          status: editMemberStatus,
        },
      );
      setEditingMember(null);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        `成员「${editMemberName.trim()}」(${editingMember.phone}) 信息已更新（状态：${editMemberStatus === 'active' ? '正常参与派单' : '已停用'}）。`,
        '成员已更新',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '更新成员失败');
    } finally {
      setSavingMember(false);
    }
  };

  // P2-1: 快捷切换成员启用/停用
  const handleQuickToggleMemberStatus = async (m: RoleMemberItem) => {
    if (!selectedRoleId) return;
    const nextStatus = m.status === 'active' ? 'disabled' : 'active';
    try {
      await api.put(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(m.phone)}`,
        {
          name: m.name,
          dept: '',
          status: nextStatus,
        },
      );
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        nextStatus === 'active'
          ? `已恢复成员「${m.name}」参与任务派发。`
          : `已停用成员「${m.name}」，后续新任务周期将不再向其派单。`,
        nextStatus === 'active' ? '成员已启用' : '成员已停用',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '操作失败');
    }
  };

  // 确认从当前角色移除成员 -> 移入右侧「已激活待归纳角色人员」列表（保留会话绑定）
  const handleConfirmRemoveFromRole = async () => {
    if (!selectedRoleId || !confirmRemoveRoleMember) return;
    const member = confirmRemoveRoleMember;
    const roleName = currentRole?.role_name || '当前角色';
    setRemovingRoleMember(true);
    try {
      await api.delete(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(member.phone)}`,
      );
      setConfirmRemoveRoleMember(null);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      toast.success(
        `已将「${member.name}」(${member.phone}) 从角色「${roleName}」移出，并转入右侧「已激活待归纳角色人员」列表（密信会话绑定已保留）。`,
        '已移至待归纳角色列表',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移出角色成员失败');
    } finally {
      setRemovingRoleMember(false);
    }
  };

  const handleImportCsv = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!csvText.trim()) {
      toast.warning('请粘贴或输入需要导入的成员数据');
      return;
    }
    setImporting(true);
    try {
      const res = await api.post<{
        ok: boolean;
        added: number;
        updated: number;
        skipped: number;
        errors: Array<{ line: number; reason: string }>;
      }>(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/import`, {
        csv_text: csvText,
      });
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      setImportModalOpen(false);
      if (res.errors && res.errors.length > 0) {
        toast.warning(
          `新增 ${res.added} 人、更新 ${res.updated} 人；有 ${res.errors.length} 行格式不符已跳过（第 ${res.errors[0].line} 行：${res.errors[0].reason}）。`,
          '导入部分完成',
        );
      } else {
        toast.success(
          `批量导入成功：新增 ${res.added} 名成员，更新 ${res.updated} 名现有成员。`,
          'CSV 导入完成',
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : 'CSV 批量导入未成功');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* 顶部区域：页面标题 + 业务角色分组横向导航带 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-slate-100">
          <div>
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">角色与成员名册</h2>
            <p className="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>管理各业务角色下的成员名单（仅需用户名与手机号）</span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                当前共 <strong className="font-semibold text-slate-800 tabular-nums">{roles.length}</strong> 个角色分组
              </span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                在册成员 <strong className="font-semibold text-slate-800 tabular-nums">{totalRosterCount}</strong> 人
              </span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                已激活待归纳角色 <strong className="font-semibold text-indigo-600 tabular-nums">{unactivatedList.length}</strong> 人
              </span>
            </p>
          </div>
          <button
            onClick={handleOpenCreateRole}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>新建业务角色</span>
          </button>
        </div>

        {/* 业务角色分组横向选择卡片带 */}
        <div>
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-xs font-semibold text-slate-500">
              选择业务角色分组（点击卡片切换下方名册）
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {roles.map((r) => {
              const active = r.role_id === selectedRoleId;
              const ratio =
                r.member_count > 0
                  ? Math.round((r.activated_count / r.member_count) * 100)
                  : 0;
              return (
                <div
                  key={r.role_id}
                  onClick={() => setSelectedRoleId(r.role_id)}
                  className={`group relative rounded-xl p-3.5 border transition-all cursor-pointer ${
                    active
                      ? 'bg-blue-50/50 border-blue-600 ring-2 ring-blue-600/10'
                      : 'bg-slate-50/50 border-slate-200/80 hover:bg-white hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-colors ${
                          active
                            ? 'bg-blue-600 text-white'
                            : 'bg-white text-slate-600 border border-slate-200/80'
                        }`}
                      >
                        <Users className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="font-bold text-slate-900 text-sm truncate">
                          {r.role_name}
                        </div>
                        <div className="text-[11px] text-slate-500 mt-0.5 flex items-center gap-1.5 tabular-nums">
                          <span>共 {r.member_count} 人</span>
                          <span aria-hidden="true">·</span>
                          <span>启用 {r.active_count}</span>
                          <span aria-hidden="true">·</span>
                          <span className="text-emerald-700 font-medium">
                            已激活 {r.activated_count}/{r.member_count}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div
                      className="flex items-center gap-0.5 shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() => handleOpenEditRole(r)}
                        className="p-1.5 rounded-lg hover:bg-white text-slate-400 hover:text-blue-600 cursor-pointer transition-colors"
                        title="修改角色名称"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleDeleteRole(r)}
                        className="p-1.5 rounded-lg hover:bg-white text-slate-400 hover:text-rose-600 cursor-pointer transition-colors"
                        title="删除角色"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* 激活进度细线 */}
                  <div className="mt-3 h-1 w-full bg-slate-200/70 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        active ? 'bg-blue-600' : 'bg-emerald-500'
                      }`}
                      style={{ width: `${ratio}%` }}
                    />
                  </div>
                </div>
              );
            })}

            {/* 快捷新建角色虚线卡片 */}
            <button
              type="button"
              onClick={handleOpenCreateRole}
              className="rounded-xl p-3.5 border border-dashed border-slate-300 hover:border-blue-500 hover:bg-blue-50/20 text-slate-500 hover:text-blue-600 transition-all cursor-pointer flex items-center justify-center gap-2 min-h-[76px]"
            >
              <Plus className="w-4 h-4" />
              <span className="text-xs font-semibold">添加新角色分组</span>
            </button>
          </div>
        </div>
      </div>

      {/* 下方主工作区：左侧 7 列「当前角色成员名册」 + 右侧 5 列「已激活待归纳角色人员」 */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* 左侧主面板 (7 cols)：当前角色成员名册 */}
        <div className="lg:col-span-7 bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
          {currentRole ? (
            <>
              {/* 第一层：角色名册标题 + 搜索框 + CSV 批量导入（合一清爽顶栏） */}
              <div className="p-4 sm:px-5 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-base">
                      {currentRole.role_name}
                    </h3>
                    <span className="text-xs text-slate-400 font-normal tabular-nums">
                      · {filteredMembers.length} / {members.length} 名成员
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    管理该角色下的派单人员名单；从角色「移除」会将成员移回右侧「已激活待归纳角色人员」列表
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <div className="relative">
                    <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                    <input
                      type="text"
                      value={memberKeyword}
                      onChange={(e) => setMemberKeyword(e.target.value)}
                      placeholder="搜索用户名或手机号…"
                      className="bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-1.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44 sm:w-48 transition-colors"
                    />
                  </div>
                  <button
                    onClick={() => setImportModalOpen(true)}
                    className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200/80 text-slate-700 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>CSV 批量导入</span>
                  </button>
                </div>
              </div>

              {/* 第二层：单行快速添加成员表单（仅需用户名与手机号） */}
              <form
                onSubmit={handleAddMember}
                className="px-4 sm:px-5 py-3 bg-slate-50/70 border-b border-slate-100 flex flex-col sm:flex-row items-stretch sm:items-center gap-2.5 text-xs"
              >
                <span className="text-slate-500 font-semibold shrink-0 flex items-center gap-1.5">
                  <UserPlus className="w-3.5 h-3.5 text-blue-600" />
                  <span>快速添加成员：</span>
                </span>
                <div className="relative flex-1">
                  <User className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="用户名 (如：张三)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-800 outline-none focus:border-blue-500"
                  />
                </div>
                <div className="relative flex-1">
                  <Phone className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    placeholder="11位手机号 (必填)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 font-mono text-xs text-slate-800 outline-none focus:border-blue-500 tabular-nums"
                  />
                </div>
                <button
                  type="submit"
                  disabled={addingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-60 shrink-0 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>添加成员</span>
                </button>
              </form>

              {/* 第三层：成员列表表格（宽敞 5 列排版） */}
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-200/80 text-[11px] font-semibold text-slate-500 bg-white">
                      <th className="py-3 pl-5 pr-3">成员姓名</th>
                      <th className="py-3 px-3">手机号码</th>
                      <th className="py-3 px-3">密信激活状态</th>
                      <th className="py-3 px-3">派单状态</th>
                      <th className="py-3 pl-3 pr-5 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-xs">
                    {loadingMembers ? (
                      <tr>
                        <td colSpan={5} className="py-12 text-center text-slate-400">
                          正在加载角色成员名册…
                        </td>
                      </tr>
                    ) : filteredMembers.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="py-12 text-center text-slate-400">
                          该角色下暂无匹配成员，可在上方直接添加或从右侧「已激活待归纳角色人员」一键移入。
                        </td>
                      </tr>
                    ) : (
                      filteredMembers.map((m) => (
                        <tr
                          key={m.phone}
                          className="hover:bg-slate-50/70 transition-colors group"
                        >
                          <td className="py-3.5 pl-5 pr-3">
                            <div className="flex items-center gap-2.5">
                              <div className="w-7 h-7 rounded-lg bg-slate-100 text-slate-700 font-bold text-xs flex items-center justify-center shrink-0">
                                {m.name.slice(0, 1)}
                              </div>
                              <span className="font-bold text-slate-900">{m.name}</span>
                            </div>
                          </td>
                          <td className="py-3.5 px-3 font-mono font-semibold text-slate-700 tabular-nums">
                            {m.phone}
                          </td>
                          <td className="py-3.5 px-3">
                            {m.activation === 'activated' ? (
                              <span
                                className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                                  m.usable === false ? 'text-amber-700' : 'text-emerald-700'
                                }`}
                              >
                                <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
                                <span>{m.usable === false ? '需重激活' : '已激活'}</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                                <Clock className="w-3.5 h-3.5 shrink-0 text-slate-400" />
                                <span>未激活</span>
                              </span>
                            )}
                          </td>
                          <td className="py-3.5 px-3">
                            <button
                              onClick={() => handleQuickToggleMemberStatus(m)}
                              title="点击快速切换派单状态"
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border cursor-pointer transition-colors ${
                                m.status === 'active'
                                  ? 'bg-blue-50/70 text-blue-700 border-blue-200/80 hover:bg-blue-100/70'
                                  : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200/70'
                              }`}
                            >
                              {m.status === 'active' ? (
                                <>
                                  <CheckCircle2 className="w-3.5 h-3.5 text-blue-600" />
                                  <span>正常派单</span>
                                </>
                              ) : (
                                <>
                                  <Ban className="w-3.5 h-3.5 text-slate-400" />
                                  <span>已停用</span>
                                </>
                              )}
                            </button>
                          </td>
                          <td className="py-3.5 pl-3 pr-5 text-right">
                            <div className="inline-flex items-center justify-end gap-1.5">
                              <button
                                onClick={() => openEditMemberModal(m)}
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                              >
                                <Edit3 className="w-3 h-3" />
                                <span>改名/停用</span>
                              </button>
                              <button
                                onClick={() => setConfirmRemoveRoleMember(m)}
                                title="将该成员移出当前角色并转入右侧已激活待归纳角色人员列表"
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-amber-50 text-slate-600 hover:text-amber-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                              >
                                <Trash2 className="w-3 h-3" />
                                <span>移除</span>
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="p-14 text-center text-slate-400 text-xs">
              请先在上方选择或新建一个业务角色分组。
            </div>
          )}
        </div>

        {/* 右侧待归纳池面板 (5 cols)：用户已在密信对话激活、但管理员尚未归纳角色的全部人员 */}
        <div className="lg:col-span-5 bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden flex flex-col">
          {/* 顶部标题、查询搜索框与批量移入控制栏 */}
          <div className="p-4 sm:px-5 border-b border-slate-100 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-emerald-500/10 text-emerald-600 flex items-center justify-center shrink-0">
                  <UserCheck className="w-4 h-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-bold text-slate-900 text-base">已激活待归纳角色人员</h3>
                    <span className="text-xs font-semibold text-indigo-600 tabular-nums">
                      · {filteredUnactivatedList.length} / {unactivatedList.length} 人待归纳
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    员工在量子密信对话激活后自动进入此列表；移除将解除会话绑定，需员工重新输入激活
                  </p>
                </div>
              </div>
            </div>

            {/* 搜索查询框（支持按姓名或手机号查询待归纳人员） */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
              <input
                type="text"
                value={unactivatedKeyword}
                onChange={(e) => setUnactivatedKeyword(e.target.value)}
                placeholder="搜索已激活待归纳人员姓名或手机号…"
                aria-label="搜索已激活待归纳人员"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-16 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 transition-colors"
              />
              {unactivatedKeyword && (
                <button
                  type="button"
                  onClick={() => setUnactivatedKeyword('')}
                  className="absolute right-2.5 top-2 px-1.5 py-0.5 rounded-md text-[11px] text-slate-500 hover:text-slate-800 hover:bg-slate-200/60 cursor-pointer"
                >
                  清空
                </button>
              )}
            </div>

            {/* 批量一键移动工具条 */}
            {filteredUnactivatedList.length > 0 && roles.length > 0 && (
              <div className="p-2.5 rounded-xl bg-slate-50 border border-slate-200/70 flex items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-slate-500 font-medium shrink-0 pl-1">批量归纳至:</span>
                  <select
                    value={batchTargetRoleId || selectedRoleId}
                    onChange={(e) => setBatchTargetRoleId(e.target.value)}
                    className="bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-800 outline-none focus:border-blue-500 truncate flex-1"
                  >
                    {roles.map((r) => (
                      <option key={r.role_id} value={r.role_id}>
                        {r.role_name}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="button"
                  onClick={handleBatchMoveAllUnactivated}
                  disabled={batchMoving}
                  className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg text-xs flex items-center gap-1.5 cursor-pointer shrink-0 disabled:opacity-60 transition-colors"
                >
                  <UserCheck className="w-3.5 h-3.5" />
                  <span>{batchMoving ? '归入中…' : '全部一键移入'}</span>
                </button>
              </div>
            )}
          </div>

          {/* 中间：已激活待归纳角色人员卡片列表（双层宽敞布局，完整展示用户全名绝不遮挡） */}
          <div className="divide-y divide-slate-100 max-h-[460px] overflow-y-auto">
            {loadingUnactivated ? (
              <div className="py-12 text-center text-slate-400 text-xs">
                正在加载已激活待归纳人员列表…
              </div>
            ) : filteredUnactivatedList.length === 0 ? (
              <div className="py-12 px-5 text-center space-y-1.5">
                <div className="w-9 h-9 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center mx-auto">
                  <CheckCircle2 className="w-5 h-5" />
                </div>
                <div className="text-xs font-bold text-slate-700">
                  {unactivatedKeyword.trim()
                    ? '未查询到匹配的已激活待归纳人员'
                    : '当前无已激活待归纳角色人员'}
                </div>
                <p className="text-xs text-slate-400">
                  {unactivatedKeyword.trim()
                    ? '请尝试更换姓名或手机号关键词重新搜索。'
                    : '员工在量子密信对话窗口发送激活指令后将自动进入本列表。'}
                </p>
              </div>
            ) : (
              filteredUnactivatedList.map((person) => {
                const targetRole = getRowTargetRole(person.phone);
                const isMoving = movingPhone === person.phone;
                return (
                  <div
                    key={person.phone}
                    className="px-4 sm:px-5 py-3.5 hover:bg-slate-50/70 transition-colors space-y-2.5 text-xs"
                  >
                    {/* 第一行：完整展示用户全名 + 手机号 + 激活及会话状态标签（无 truncate 遮挡） */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-700 font-bold text-xs flex items-center justify-center shrink-0">
                          {person.name.slice(0, 1)}
                        </div>
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-bold text-slate-900 text-sm break-all">
                              {person.name}
                            </span>
                            <span className="font-mono text-slate-600 font-semibold tabular-nums">
                              {person.phone}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            {person.source_note || '已在量子密信对话激活 · 待管理员归纳角色'}
                          </div>
                        </div>
                      </div>

                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200/70 text-[11px] font-medium shrink-0">
                        <ShieldCheck className="w-3 h-3" />
                        <span>已激活 · 待归纳角色</span>
                      </span>
                    </div>

                    {/* 第二行：目标角色选择 + 一键移入分组 + 移除（解除会话绑定） */}
                    <div className="flex items-center justify-between gap-2 pl-10">
                      <div className="flex items-center gap-1.5 flex-1 min-w-0">
                        <select
                          value={targetRole}
                          onChange={(e) =>
                            setRowTargetRoleMap((prev) => ({
                              ...prev,
                              [person.phone]: e.target.value,
                            }))
                          }
                          aria-label={`选择 ${person.name} 的目标角色分组`}
                          className="bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:bg-white focus:border-blue-500 flex-1 min-w-0"
                        >
                          {roles.map((r) => (
                            <option key={r.role_id} value={r.role_id}>
                              {r.role_name}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => handleMoveSingleUnactivated(person)}
                          disabled={isMoving || roles.length === 0}
                          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 disabled:opacity-60 transition-colors"
                        >
                          <ArrowRightCircle className="w-3.5 h-3.5" />
                          <span>{isMoving ? '移入中…' : '一键移入分组'}</span>
                        </button>
                      </div>

                      <button
                        type="button"
                        onClick={() => setConfirmUnbindPerson(person)}
                        className="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-rose-50 text-slate-600 hover:text-rose-600 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 transition-colors"
                        title="解除该用户的量子密信会话绑定并从列表移除（需用户重新发送对话指令激活）"
                      >
                        <Link2Off className="w-3.5 h-3.5" />
                        <span>移除</span>
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* 底部说明：激活仅支持对话形式 */}
          <div className="px-4 sm:px-5 py-3 bg-slate-50/80 border-t border-slate-100 flex items-start gap-2 text-[11px] text-slate-500">
            <MessageSquare className="w-3.5 h-3.5 text-indigo-600 shrink-0 mt-0.5" />
            <span>
              说明：用户激活仅能通过「量子密信」对话窗口向机器人发送激活指令自动绑定会话，不支持后台人工登记未绑定会话人员。
            </span>
          </div>
        </div>
      </div>

      {/* 交互确认弹窗 1：从当前角色中移除成员 -> 移入右侧「已激活待归纳角色人员」列表 */}
      {confirmRemoveRoleMember && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center shrink-0">
                  <ArrowRightCircle className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认将该成员移出当前角色？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    移出角色后将自动转入「已激活待归纳角色人员」列表
                  </p>
                </div>
              </div>
              <button
                onClick={() => setConfirmRemoveRoleMember(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3 text-xs text-slate-600 leading-relaxed">
              <p>
                您即将把成员{' '}
                <strong className="font-bold text-slate-900">
                  {confirmRemoveRoleMember.name} ({confirmRemoveRoleMember.phone})
                </strong>{' '}
                从角色分组{' '}
                <strong className="font-bold text-blue-700">「{currentRole.role_name}」</strong>{' '}
                中移除。
              </p>
              <div className="p-3 rounded-xl bg-amber-50/80 border border-amber-200/80 text-amber-900 space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-amber-700 shrink-0" />
                  <span>保留量子密信会话绑定 · 转入待归纳列表</span>
                </div>
                <p className="text-[11px] text-amber-800">
                  该操作不会解除用户的密信会话绑定，系统会将其移至右侧「已激活待归纳角色人员」列表中，您可随时将其重新一键归入其他业务角色，无需员工重新输入激活。
                </p>
              </div>
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setConfirmRemoveRoleMember(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmRemoveFromRole}
                disabled={removingRoleMember}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {removingRoleMember ? '正在移出…' : '确认移至待归纳列表'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 交互确认弹窗 2：从「已激活待归纳角色人员」移除 -> 解除量子密信会话绑定，需用户重新输入激活 */}
      {confirmUnbindPerson && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-rose-50 text-rose-600 flex items-center justify-center shrink-0">
                  <Link2Off className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认移除并解除密信会话绑定？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    解除绑定后，用户必须在密信对话中重新输入激活指令
                  </p>
                </div>
              </div>
              <button
                onClick={() => setConfirmUnbindPerson(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3 text-xs text-slate-600 leading-relaxed">
              <p>
                您即将从「已激活待归纳角色人员」列表中移除{' '}
                <strong className="font-bold text-slate-900">
                  {confirmUnbindPerson.name} ({confirmUnbindPerson.phone})
                </strong>
                。
              </p>
              <div className="p-3 rounded-xl bg-rose-50/80 border border-rose-200/80 text-rose-900 space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
                  <span>将清除该用户的量子密信会话绑定</span>
                </div>
                <p className="text-[11px] text-rose-800">
                  移除后，该用户的密信会话绑定（{confirmUnbindPerson.external_id || `zdxlz_${confirmUnbindPerson.phone}`}）将被彻底解除。后续该用户只有在「量子密信」对话窗口重新输入发送激活信息，完成会话绑定后才会再次出现在本列表。
                </p>
              </div>
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setConfirmUnbindPerson(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmUnbindUnactivated}
                disabled={unbindingPerson}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {unbindingPerson ? '正在解除绑定…' : '确认解除绑定并移除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* P2-1: 成员「改名/停用」弹窗（严禁改号，phone 只读，不含所属部门） */}
      {editingMember && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">修改成员信息与派单状态</h3>
                <p className="text-[11px] text-slate-400">
                  支持修改用户名及启用/停用状态
                </p>
              </div>
              <button
                onClick={() => setEditingMember(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveMemberEdit} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                  <span>手机号码（唯一标识）</span>
                  <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700">
                    <Lock className="w-2.5 h-2.5" /> 不可修改
                  </span>
                </label>
                <input
                  type="text"
                  readOnly
                  disabled
                  value={editingMember.phone}
                  className="w-full bg-slate-100 border border-slate-200 rounded-xl p-2.5 font-mono text-slate-500 cursor-not-allowed tabular-nums"
                />
                <div className="text-[11px] text-slate-400 mt-1">
                  如需更换手机号，请先移除该成员后重新添加。
                </div>
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  用户名 / 姓名 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={editMemberName}
                  onChange={(e) => setEditMemberName(e.target.value)}
                  placeholder="请输入成员真实姓名"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1">派单状态</label>
                <select
                  value={editMemberStatus}
                  onChange={(e) => setEditMemberStatus(e.target.value as 'active' | 'disabled')}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                >
                  <option value="active">正常派单 — 参与周期任务派发与提醒</option>
                  <option value="disabled">暂停派单 — 保留历史记录但不再派发新任务</option>
                </select>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditingMember(null)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {savingMember ? '正在保存…' : '保存修改'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 新建/编辑角色弹窗（仅需填写角色名称，移除角色编码） */}
      {roleModalMode !== 'none' && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">
                {roleModalMode === 'create' ? '新建业务角色' : '修改角色名称'}
              </h3>
              <button
                onClick={() => setRoleModalMode('none')}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleSaveRole} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  角色名称 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  autoFocus
                  value={roleNameInput}
                  onChange={(e) => setRoleNameInput(e.target.value)}
                  placeholder="例如：片长/分支局长"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>
              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRoleModalMode('none')}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingRole}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {savingRole ? '正在保存…' : '保存角色'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* CSV 批量导入弹窗（仅需手机号与用户名） */}
      {importModalOpen && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  批量导入成员至「{currentRole.role_name}」
                </h3>
                <p className="text-[11px] text-slate-400">
                  每行一条记录，格式：手机号,用户名（支持自动跳过表头与去重更新）
                </p>
              </div>
              <button
                onClick={() => setImportModalOpen(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleImportCsv} className="space-y-3 text-xs">
              <textarea
                rows={6}
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                placeholder={'手机号,用户名\n13900000000,张三'}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 font-mono text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 leading-relaxed"
              />
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setImportModalOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={importing}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {importing ? '正在导入…' : '开始导入'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
