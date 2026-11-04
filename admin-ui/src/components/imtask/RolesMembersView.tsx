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
  UserMinus,
  Search,
  MessageSquare,
  Link2Off,
  AlertTriangle,
  Building2,
  Globe,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { GlobalActivatedMemberItem, RoleItem, RoleMemberItem } from '../../lib/types';

export const RolesMembersView: React.FC = () => {
  const { roles, refreshRoles, platform, isSuperAdmin } = useAppStore();
  const toast = useToast();
  /* 2026-11-04 多管理员分级：全局搜索已激活成员（激活关系跨管理员共享，免二次激活） */
  const [globalSearchOpen, setGlobalSearchOpen] = useState(false);
  const [globalQuery, setGlobalQuery] = useState('');
  const [globalResults, setGlobalResults] = useState<GlobalActivatedMemberItem[]>([]);
  const [globalBusy, setGlobalBusy] = useState(false);

  const [selectedRoleId, setSelectedRoleId] = useState<string>('');
  const [members, setMembers] = useState<RoleMemberItem[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [memberKeyword, setMemberKeyword] = useState('');

  // 2026-11-03 新激活模型（后台先导入才能激活）：「已激活待归纳角色人员」整体下线，
  // 成员只能从后台 CSV 导入/手动添加进入角色，再由用户自己走 H5 登记比对激活。

  // 从当前角色移除成员的交互确认弹窗（移除=彻底删绑定，用户需重新登记激活）
  const [confirmRemoveRoleMember, setConfirmRemoveRoleMember] = useState<RoleMemberItem | null>(null);
  const [removingRoleMember, setRemovingRoleMember] = useState(false);

  // 单条添加成员表单（姓名 + 手机号 + 部门，部门由后台统一配置）
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [newDept, setNewDept] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  // 2026-11-03 部门选项（后台配置；成员表单、编辑弹窗、H5 登记页共用同一份）
  const [departments, setDepartments] = useState<{ id: number; name: string }[]>([]);
  const [deptModalOpen, setDeptModalOpen] = useState(false);
  const [newDeptName, setNewDeptName] = useState('');
  const [editingDeptId, setEditingDeptId] = useState<number | null>(null);
  const [editingDeptName, setEditingDeptName] = useState('');
  const [deptBusy, setDeptBusy] = useState(false);

  // 新建/编辑角色弹窗（无需角色编码）
  const [roleModalMode, setRoleModalMode] = useState<'none' | 'create' | 'edit'>('none');
  const [editingRoleId, setEditingRoleId] = useState('');
  const [roleNameInput, setRoleNameInput] = useState('');
  const [savingRole, setSavingRole] = useState(false);

  // P2-1: 成员「改名/停用」弹窗（手机号只读不可改，移除所属部门）
  const [editingMember, setEditingMember] = useState<RoleMemberItem | null>(null);
  const [editMemberName, setEditMemberName] = useState('');
  const [editMemberDept, setEditMemberDept] = useState('');
  const [editMemberStatus, setEditMemberStatus] = useState<'active' | 'disabled'>('active');
  /* 2026-10-29：一人多角色 —— 把已在某角色的成员同时加入其他角色 */
  const [assignMember, setAssignMember] = useState<RoleMemberItem | null>(null);
  const [assignRoleIds, setAssignRoleIds] = useState<string[]>([]);
  const [assigningRoles, setAssigningRoles] = useState(false);
  const [memberRoleIds, setMemberRoleIds] = useState<string[]>([]);
  const [savingMember, setSavingMember] = useState(false);

  // CSV 批量导入弹窗（仅手机号与用户名）
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [csvText, setCsvText] = useState('13900000000,张三,市场部');
  const [importing, setImporting] = useState(false);

  const loadMembers = useCallback(
    async (roleId: string) => {
      if (!roleId) {
        setMembers([]);
        return;
      }
      setLoadingMembers(true);
      try {
        const res = await api.get<{ ok: boolean; items: RoleMemberItem[] }>(
          `/api/admin/roles/${encodeURIComponent(roleId)}/members?platform=${encodeURIComponent(platform)}`,
        );
        setMembers(res.items || []);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载角色成员失败');
      } finally {
        setLoadingMembers(false);
      }
    },
    [toast, platform],
  );

  // 2026-11-03 部门选项：后台配置，与 H5 登记页共用
  const loadDepartments = useCallback(async () => {
    try {
      const res = await api.get<{ ok: boolean; items: { id: number; name: string }[] }>('/api/admin/departments');
      setDepartments(res.items || []);
    } catch {
      /* 部门选项加载失败不阻塞页面 */
    }
  }, []);

  useEffect(() => {
    void loadDepartments();
  }, [loadDepartments]);

  const handleAddDepartment = async () => {
    const name = newDeptName.trim();
    if (!name) {
      toast.warning('请填写部门名称');
      return;
    }
    setDeptBusy(true);
    try {
      await api.post('/api/admin/departments', { name });
      setNewDeptName('');
      await loadDepartments();
      toast.success(`部门「${name}」已添加。`, '部门已添加');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '添加部门未成功');
    } finally {
      setDeptBusy(false);
    }
  };

  const handleRenameDepartment = async (id: number) => {
    const name = editingDeptName.trim();
    if (!name) {
      toast.warning('请填写新的部门名称');
      return;
    }
    setDeptBusy(true);
    try {
      await api.put(`/api/admin/departments/${id}`, { name });
      setEditingDeptId(null);
      setEditingDeptName('');
      await loadDepartments();
      toast.success('部门名称已更新。', '修改已保存');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '修改部门未成功');
    } finally {
      setDeptBusy(false);
    }
  };

  const handleDeleteDepartment = async (id: number, name: string) => {
    setDeptBusy(true);
    try {
      await api.delete(`/api/admin/departments/${id}`);
      await loadDepartments();
      toast.success(`部门「${name}」已删除（已登记人员的信息不受影响）。`, '部门已删除');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '删除部门未成功');
    } finally {
      setDeptBusy(false);
    }
  };

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

  // 2026-11-03 新激活模型：「已激活待归纳」列表整体下线，相关状态与处理函数一并移除。

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
          platform,
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

  /* 2026-11-04 多管理员分级：全局搜索已激活成员（跨管理员共享激活关系） */
  const searchGlobalActivated = useCallback(async (keyword: string) => {
    setGlobalBusy(true);
    try {
      const res = await api.get<{ ok: boolean; items: GlobalActivatedMemberItem[] }>(
        `/api/admin/global-activated-members?q=${encodeURIComponent(keyword.trim())}&platform=${platform}`,
      );
      setGlobalResults(res.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '全局搜索失败');
    } finally {
      setGlobalBusy(false);
    }
  }, [platform, toast]);

  const pullGlobalMember = async (item: GlobalActivatedMemberItem) => {
    if (!selectedRoleId) {
      toast.warning('请先在左侧选择要拉入的角色分组');
      return;
    }
    setGlobalBusy(true);
    try {
      const res = await api.post<{ ok: boolean; global_reused?: boolean }>(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`,
        { phone: item.phone, name: item.name || item.phone, dept: item.dept || '' },
      );
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        res.global_reused
          ? `「${item.name || item.phone}」已拉入当前分组（该号码此前已激活，无需二次激活）`
          : `「${item.name || item.phone}」已拉入当前分组`,
        '已拉入',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '拉入失败');
    } finally {
      setGlobalBusy(false);
    }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!newName.trim() || !newPhone.trim()) {
      toast.warning('请填写用户名和 11 位手机号');
      return;
    }    setAddingMember(true);
    try {
      await api.post(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
        phone: newPhone.trim(),
        name: newName.trim(),
        dept: newDept,
      });
      const addedName = newName.trim();
      const addedPhone = newPhone.trim();
      setNewName('');
      setNewPhone('');
      setNewDept('');
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(`成员「${addedName}」(${addedPhone}) 已添加至当前角色。`, '添加成功');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '添加成员未成功');
    } finally {
      setAddingMember(false);
    }
  };

  /* 2026-10-29：一人多角色 —— 打开「加入其他角色」弹窗，先拉该人当前所在角色用于置灰 */
  const openAssignRolesModal = async (m: RoleMemberItem) => {
    setAssignMember(m);
    setAssignRoleIds([]);
    setMemberRoleIds([]);
    try {
      const res = await api.get<{ ok: boolean; items: { role_id: string }[] }>(
        `/api/admin/members/${encodeURIComponent(m.phone)}/roles`,
      );
      setMemberRoleIds((res.items || []).map((r) => String(r.role_id)));
    } catch {
      /* 拉不到就不置灰；提交时后端按 DUPLICATE 跳��，不会重复插入 */
    }
  };

  const toggleAssignRole = (roleId: string) => {
    setAssignRoleIds((prev) =>
      prev.includes(roleId) ? prev.filter((x) => x !== roleId) : [...prev, roleId],
    );
  };

  const handleAssignRoles = async () => {
    if (!assignMember) return;
    const targets = assignRoleIds.filter((rid) => !memberRoleIds.includes(rid));
    if (targets.length === 0) {
      toast.warning('请至少勾选 1 个「尚未加入」的角色');
      return;
    }
    setAssigningRoles(true);
    try {
      const res = await api.post<{
        ok: boolean;
        added: string[];
        skipped: string[];
        failed: { role_id: string; message: string }[];
      }>(`/api/admin/members/${encodeURIComponent(assignMember.phone)}/roles`, {
        role_ids: targets,
        name: assignMember.name,
        dept: assignMember.dept || '',
      });
      const nameOf = (rid: string) => roles.find((r) => r.role_id === rid)?.role_name || rid;
      const addedNames = (res.added || []).map(nameOf);
      const memberName = assignMember.name;
      await Promise.all([refreshRoles(), loadMembers(selectedRoleId)]);
      setAssignMember(null);
      setAssignRoleIds([]);
      if (addedNames.length > 0) {
        toast.success(
          `「${memberName}」已同时加入：${addedNames.join('、')}。原有角色保留不变，两个角色都会收到各自的任务。`,
          '已加入其他角色',
        );
      }
      if ((res.skipped || []).length > 0) {
        toast.info(`已跳过 ${res.skipped.length} 个角色：该成员本来就在里面`);
      }
      if ((res.failed || []).length > 0) {
        toast.error(res.failed.map((f) => `${nameOf(f.role_id)}：${f.message}`).join('；'), '部分角色添加失败');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '加入其他角色失败');
    } finally {
      setAssigningRoles(false);
    }
  };

  // P2-1: 打开成员「改名/停用」弹窗
  const openEditMemberModal = (m: RoleMemberItem) => {
    setEditingMember(m);
    setEditMemberName(m.name);
    setEditMemberDept(m.dept || '');
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
          dept: editMemberDept,
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

  // 确认从当前角色移除成员：彻底删除绑定 + 清未完成实例（用户需重新登记激活）
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
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        `已将「${member.name}」(${member.phone}) 从角色「${roleName}」移出（该平台会话绑定已解除；成员需重新完成 H5 登记激活后才能再接收任务）。`,
        '已移出角色',
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
        departments_created?: string[];
      }>(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/import`, {
        csv_text: csvText,
      });
      /* 导入可能自动新建部门 → 刷新部门列表，否则新部门在下拉/部门管理里看不到 */
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadDepartments()]);
      setImportModalOpen(false);
      const createdDepts = res.departments_created || [];
      const deptNote = createdDepts.length
        ? `，并自动新建 ${createdDepts.length} 个部门（${createdDepts.join('、')}）`
        : '';
      if (res.errors && res.errors.length > 0) {
        toast.warning(
          `新增 ${res.added} 人、更新 ${res.updated} 人${deptNote}；有 ${res.errors.length} 行格式不符已跳过（第 ${res.errors[0].line} 行：${res.errors[0].reason}）。`,
          '导入部分完成',
        );
      } else {
        toast.success(
          `批量导入成功：新增 ${res.added} 名成员，更新 ${res.updated} 名现有成员${deptNote}。`,
          'CSV 导入完成',
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : 'CSV 批量导入未成功', 'CSV 批量导入失败');
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
              <span>管理各业务角色下的成员名单（支持姓名、手机号、部门）</span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                当前共 <strong className="font-semibold text-slate-800 tabular-nums">{roles.length}</strong> 个角色分组
              </span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                在册成员 <strong className="font-semibold text-slate-800 tabular-nums">{totalRosterCount}</strong> 人
              </span>
            </p>
            <p className="text-[11px] text-slate-400 mt-1">
              激活方式：先把成员导入/添加到角色，成员再给机器人发任意消息 → 点击登记链接完成比对激活；未导入的成员无法激活。
            </p>
          </div>
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
                        {/* 2026-11-04 多管理员分级：超管可见「创建人」 */}
                        {isSuperAdmin && (
                          <div className="text-[10px] text-slate-400 font-medium truncate">
                            创建人：{r.owner_display_name || r.owner_username || '—'}
                          </div>
                        )}
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

                  {/* 2026-10-29：统计信息统一放到标题下方独立一行，避免卡片宽度不同时换行不一致 */}
                  <div className="mt-1.5 text-[11px] text-slate-500 flex items-center gap-1.5 tabular-nums whitespace-nowrap">
                    <span>共 {r.member_count} 人</span>
                    <span aria-hidden="true" className="text-slate-300">·</span>
                    <span>启用 {r.active_count}</span>
                    <span aria-hidden="true" className="text-slate-300">·</span>
                    <span className="text-emerald-700 font-medium">
                      已激活 {r.activated_count}/{r.member_count}
                    </span>
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

            {/* 快捷新建角色虚线卡片（2026-11-04：超管只读，不显示新建入口） */}
            {!isSuperAdmin && (
              <button
                type="button"
                onClick={handleOpenCreateRole}
                className="rounded-xl p-3.5 border border-dashed border-slate-300 hover:border-blue-500 hover:bg-blue-50/20 text-slate-500 hover:text-blue-600 transition-all cursor-pointer flex items-center justify-center gap-2 min-h-[76px]"
              >
                <Plus className="w-4 h-4" />
                <span className="text-xs font-semibold">添加新角色分组</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 主工作区：当前角色成员名册（新激活模型：待归纳列表已下线，占满整行） */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
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
                    管理该角色下的派单人员名单；从角色「移除」会同时解除该成员在该平台的会话绑定，需重新登记激活
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
                    onClick={() => setGlobalSearchOpen(true)}
                    className="px-3 py-1.5 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                    title="全局搜索已激活成员：激活关系跨管理员共享，搜到即可直接拉入本分组，无需二次激活"
                  >
                    <Globe className="w-3.5 h-3.5" />
                    <span>全局搜索已激活成员</span>
                  </button>
                  {!isSuperAdmin && (
                    <button
                      onClick={() => setImportModalOpen(true)}
                      className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200/80 text-slate-700 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                    >
                      <Upload className="w-3.5 h-3.5" />
                      <span>CSV 批量导入</span>
                    </button>
                  )}
                </div>
              </div>

              {/* 第二层：单行快速添加成员表单（姓名 + 手机号 + 部门）—— 超管只读，不显示 */}
              {!isSuperAdmin && (
              <form
                onSubmit={handleAddMember}
                className="px-4 sm:px-5 py-3 bg-slate-50/70 border-b border-slate-100 flex flex-col lg:flex-row flex-wrap items-stretch lg:items-center gap-2.5 text-xs"
              >
                <span className="text-slate-500 font-semibold shrink-0 flex items-center gap-1.5">
                  <UserPlus className="w-3.5 h-3.5 text-blue-600" />
                  <span>快速添加成员：</span>
                </span>
                <div className="relative flex-1 min-w-[160px]">
                  <User className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="用户名 (如：张三)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-800 outline-none focus:border-blue-500"
                  />
                </div>
                <div className="relative flex-1 min-w-[160px]">
                  <Phone className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    placeholder="11位手机号 (必填)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 font-mono text-xs text-slate-800 outline-none focus:border-blue-500 tabular-nums"
                  />
                </div>
                <div className="relative flex-1 min-w-[180px]">
                  <Building2 className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <select
                    value={newDept}
                    onChange={(e) => setNewDept(e.target.value)}
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-800 outline-none focus:border-blue-500 appearance-none cursor-pointer"
                    title="部门可不选；选项由「部门管理」维护，也是 H5 登记页的下拉内容"
                  >
                    <option value="">部门（选填）</option>
                    {departments.map((d) => (
                      <option key={d.id} value={d.name}>{d.name}</option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={addingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-60 shrink-0 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>添加成员</span>
                </button>
                <button
                  type="button"
                  onClick={() => setDeptModalOpen(true)}
                  className="px-3 py-2 bg-white border border-slate-200 hover:border-blue-300 text-slate-600 font-semibold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer shrink-0 transition-colors"
                  title="配置部门选项（成员表单与员工 H5 登记页共用）"
                >
                  <Building2 className="w-3.5 h-3.5 text-slate-500" />
                  <span>部门管理</span>
                </button>
              </form>
              )}

              {/* 第三层：成员列表表格（宽敞 5 列排版） */}
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-200/80 text-[11px] font-semibold text-slate-500 bg-white">
                      <th className="py-3 pl-5 pr-3">成员姓名</th>
                      <th className="py-3 px-3">手机号码</th>
                      <th className="py-3 px-3">部门</th>
                      <th className="py-3 px-3">激活状态</th>
                      <th className="py-3 px-3">派单状态</th>
                      <th className="py-3 pl-3 pr-5 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-xs">
                    {loadingMembers ? (
                      <tr>
                        <td colSpan={6} className="py-12 text-center text-slate-400">
                          正在加载角色成员名册…
                        </td>
                      </tr>
                    ) : filteredMembers.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="py-12 text-center text-slate-400">
                          该角色下暂无匹配成员，可在上方直接添加或用「CSV 批量导入」录入。
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
                          <td className="py-3.5 px-3 text-slate-600">
                            {m.dept ? (
                              <span className="inline-flex items-center gap-1">
                                <Building2 className="w-3 h-3 text-slate-400" />
                                <span>{m.dept}</span>
                              </span>
                            ) : (
                              <span className="text-slate-300">—</span>
                            )}
                          </td>
                          <td className="py-3.5 px-3">
                            {(() => {
                              const on = m.activations
                                ? m.activations[platform]
                                : m.activation === 'activated';
                              return on ? (
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
                              );
                            })()}
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
                                onClick={() => void openAssignRolesModal(m)}
                                title="把该成员同时加入其他业务角色（一人可兼多角色，原角色保留）"
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-emerald-50 text-slate-700 hover:text-emerald-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap"
                              >
                                <Users className="w-3 h-3" />
                                <span>加入其他角色</span>
                              </button>
                              <button
                                onClick={() => openEditMemberModal(m)}
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap"
                              >
                                <Edit3 className="w-3 h-3" />
                                <span>改名/停用</span>
                              </button>
                              <button
                                onClick={() => setConfirmRemoveRoleMember(m)}
                                title="将该成员移出当前角色：删除其在当前平台的会话绑定并清除未完成任务（需重新登记激活）"
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

      {/* 2026-11-03 部门管理弹窗：配置部门选项（成员表单与员工 H5 登记页共用） */}
      {deptModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base flex items-center gap-1.5">
                  <Building2 className="w-4 h-4 text-blue-600" />
                  <span>部门管理</span>
                </h3>
                <p className="text-[11px] text-slate-400">员工 H5 登记页与成员表单的下拉选项，都来自这里</p>
              </div>
              <button onClick={() => setDeptModalOpen(false)} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="text"
                value={newDeptName}
                onChange={(e) => setNewDeptName(e.target.value)}
                placeholder="新增部门名称，如：市场部"
                className="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500"
              />
              <button
                type="button"
                disabled={deptBusy}
                onClick={() => void handleAddDepartment()}
                className="px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl text-xs inline-flex items-center gap-1 cursor-pointer disabled:opacity-60 shrink-0"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>添加</span>
              </button>
            </div>

            <div className="max-h-72 overflow-y-auto space-y-1.5">
              {departments.length === 0 ? (
                <div className="py-8 text-center text-xs text-slate-400">还没有配置部门，先添加一个</div>
              ) : (
                departments.map((d) => (
                  <div key={d.id} className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-slate-50/60">
                    {editingDeptId === d.id ? (
                      <input
                        autoFocus
                        type="text"
                        value={editingDeptName}
                        onChange={(e) => setEditingDeptName(e.target.value)}
                        className="flex-1 bg-white border border-blue-300 rounded-lg px-2.5 py-1.5 text-xs outline-none"
                      />
                    ) : (
                      <span className="flex-1 text-xs font-semibold text-slate-800">{d.name}</span>
                    )}
                    {editingDeptId === d.id ? (
                      <>
                        <button
                          type="button"
                          disabled={deptBusy}
                          onClick={() => void handleRenameDepartment(d.id)}
                          className="px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-semibold cursor-pointer disabled:opacity-60"
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          onClick={() => { setEditingDeptId(null); setEditingDeptName(''); }}
                          className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 text-[11px] font-semibold cursor-pointer"
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => { setEditingDeptId(d.id); setEditingDeptName(d.name); }}
                          className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-600 hover:text-blue-700 text-[11px] font-semibold cursor-pointer inline-flex items-center gap-1"
                        >
                          <Edit3 className="w-3 h-3" />
                          <span>改名</span>
                        </button>
                        <button
                          type="button"
                          disabled={deptBusy}
                          onClick={() => void handleDeleteDepartment(d.id, d.name)}
                          className="px-2.5 py-1 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 text-[11px] font-semibold cursor-pointer inline-flex items-center gap-1 disabled:opacity-60"
                        >
                          <Trash2 className="w-3 h-3" />
                          <span>删除</span>
                        </button>
                      </>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* 交互确认弹窗：从当前角色移除成员（移除=删角色+清未完成实例+彻底删该平台绑定） */}
      {confirmRemoveRoleMember && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-amber-100 text-amber-600 flex items-center justify-center shrink-0">
                  <UserMinus className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认将成员移出该角色分组？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    移出后该成员将不再接收本角色任务提醒
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
                  <AlertTriangle className="w-4 h-4 text-amber-700 shrink-0" />
                  <span>将解除该成员在该办公软件的会话绑定</span>
                </div>
                <p className="text-[11px] text-amber-800">
                  移出后：该角色的未完成任务实例一并清除；若该成员在其它平台仍有角色，其它平台不受影响；其在本平台的激活绑定将被彻底删除，需重新给机器人发消息、点击登记链接完成比对后才可再次接收任务（该成员未在后台导入前无法激活）。
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
                {removingRoleMember ? '正在移出…' : '确认移出并解除绑定'}
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
                <label className="font-semibold text-slate-700 block mb-1">部门</label>
                <select
                  value={editMemberDept}
                  onChange={(e) => setEditMemberDept(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
                >
                  <option value="">不填部门</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.name}>{d.name}</option>
                  ))}
                </select>
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

      {/* 2026-10-29：一人多角色 —— 把成员同时加入其他业务角色（原角色保留） */}
      {assignMember && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">加入其他业务角色</h3>
                <p className="text-[11px] text-slate-400">
                  一人可同时属于多个角色；原角色成员关系保留，各角色任务独立派发。
                </p>
              </div>
              <button
                onClick={() => setAssignMember(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="text-xs bg-slate-50 border border-slate-200 rounded-xl p-3 space-y-1">
              <div className="font-semibold text-slate-800">{assignMember.name}</div>
              <div className="font-mono text-slate-500 tabular-nums">{assignMember.phone}</div>
              <div className="text-[11px] text-slate-500 leading-relaxed">
                当前已在：
                {memberRoleIds.length === 0
                  ? '（读取中…）'
                  : memberRoleIds
                      .map((rid) => roles.find((r) => r.role_id === rid)?.role_name || rid)
                      .join('、')}
              </div>
            </div>

            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {roles.map((r) => {
                const already = memberRoleIds.includes(r.role_id);
                const checked = assignRoleIds.includes(r.role_id);
                return (
                  <label
                    key={r.role_id}
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-xl border text-xs ${
                      already
                        ? 'bg-slate-50 border-slate-200 text-slate-400 cursor-not-allowed'
                        : checked
                          ? 'bg-emerald-50 border-emerald-300 text-emerald-800 cursor-pointer'
                          : 'bg-white border-slate-200 text-slate-700 hover:border-emerald-200 cursor-pointer'
                    }`}
                  >
                    <input
                      type="checkbox"
                      disabled={already}
                      checked={already || checked}
                      onChange={() => toggleAssignRole(r.role_id)}
                      className="w-3.5 h-3.5 accent-emerald-600"
                    />
                    <span className="font-semibold flex-1 truncate">{r.role_name}</span>
                    {already ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 text-slate-500">
                        已在其中
                      </span>
                    ) : (
                      <span className="text-[10px] text-slate-400">{r.member_count} 人</span>
                    )}
                  </label>
                );
              })}
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setAssignMember(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                disabled={
                  assigningRoles ||
                  assignRoleIds.filter((rid) => !memberRoleIds.includes(rid)).length === 0
                }
                onClick={() => void handleAssignRoles()}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-50"
              >
                {assigningRoles ? '正在加入…' : '确认加入'}
              </button>
            </div>
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

      {/* 2026-11-04 多管理员分级：全局搜索已激活成员（跨管理员共享激活关系，免二次激活） */}
      {globalSearchOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base flex items-center gap-1.5">
                  <Globe className="w-4 h-4 text-emerald-600" />
                  <span>全局搜索已激活成员</span>
                </h3>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  激活关系全平台共享：搜到的人已被激活过，拉入本分组后无需二次激活
                </p>
              </div>
              <button onClick={() => setGlobalSearchOpen(false)} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void searchGlobalActivated(globalQuery);
              }}
              className="flex items-center gap-2"
            >
              <div className="relative flex-1">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                <input
                  value={globalQuery}
                  onChange={(e) => setGlobalQuery(e.target.value)}
                  placeholder="按手机号或姓名搜索（留空看最近激活的）"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs outline-none focus:bg-white focus:border-blue-500"
                />
              </div>
              <button
                type="submit"
                disabled={globalBusy}
                className="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-xl cursor-pointer disabled:opacity-60 shrink-0"
              >
                {globalBusy ? '搜索中…' : '搜索'}
              </button>
            </form>
            <div className="max-h-[46vh] overflow-y-auto divide-y divide-slate-100">
              {globalResults.length === 0 ? (
                <div className="py-8 text-center text-xs text-slate-400">
                  {globalBusy ? '搜索中…' : '没有匹配的已激活成员（对方需先在密信/企微给机器人发一句话完成登记）'}
                </div>
              ) : (
                globalResults.map((it) => (
                  <div key={`${it.platform}-${it.phone}`} className="py-2.5 flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-xs text-slate-800">{it.name || '（未登记姓名）'}</span>
                        <span className="text-[11px] font-mono text-slate-500">{it.phone}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">
                          {it.platform === 'wecom' ? '企业微信' : '量子密信'}
                        </span>
                        {it.dept && <span className="text-[10px] text-slate-400">{it.dept}</span>}
                      </div>
                      <div className="text-[10px] text-emerald-600 mt-0.5">已激活 · 无需二次激活</div>
                    </div>
                    <button
                      onClick={() => void pullGlobalMember(it)}
                      disabled={globalBusy}
                      className="px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-semibold text-[11px] cursor-pointer disabled:opacity-50 shrink-0"
                    >
                      拉入当前分组
                    </button>
                  </div>
                ))
              )}
            </div>
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
                  每行一条记录，格式：手机号,姓名,部门（部门可留空；支持自动跳过表头与去重更新）
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
                placeholder={'手机号,姓名,部门\n13900000000,张三,市场部'}
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
