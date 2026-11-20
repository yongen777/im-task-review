import { PortalGrantDialog } from './PortalTasksView';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  Search,
  Building2,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { DepartmentTree, type DeptTreeNode } from './DepartmentTree';
import { AdminScopeHeader } from './AdminScopeHeader';
import { OrgPickerModal } from './OrgPickerModal';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';

/**
 * 手机号必填，姓名选填。支持单列手机号和两列自动识别，姓名与部门由后台核验。
 * 跳过空行和注释，首条数据前可带表头。
 */
function parseCsvRows(csvText: string): {
  rows: Array<{ row: number; phone: string; name: string; dept: string }>;
  errors: Array<{ row: number; phone: string; reason: string }>;
} {
  const rows: Array<{ row: number; phone: string; name: string; dept: string }> = [];
  const errors: Array<{ row: number; phone: string; reason: string }> = [];
  const phoneCellRe = /^1[3-9]\d{9}$/;
  const lines = (csvText || '').split(/\r?\n/);
  let firstRecord = true;
  lines.forEach((raw) => {
    const line = raw.trim();
    if (!line || line.startsWith('#')) return;
    const cells = line.split(/[,，\t]/).map((cell) => cell.trim().replace(/^"(.*)"$/, '$1').trim());
    const nonEmpty = cells.filter((c) => c !== '');
    const rowNo = rows.length + errors.length + 1;
    const header = firstRecord && nonEmpty.length > 0 &&
      nonEmpty.every((c) => /^(?:姓名|成员姓名|员工姓名|手机号|手机号码|name|phone)$/i.test(c));
    firstRecord = false;
    if (cells.slice(2).some(Boolean)) {
      errors.push({
        row: rowNo,
        phone: '',
        reason: '手机号必填，姓名选填，请删除额外列。部门由通讯录自动回填。',
      });
      return;
    }
    if (header || !nonEmpty.length) return;
    if (nonEmpty.length === 1) {
      rows.push({ row: rowNo, phone: nonEmpty[0], name: '', dept: '' });
      return;
    }
    let phone: string;
    let name: string;
    if (phoneCellRe.test(cells[0] || '')) {
      phone = cells[0];
      name = cells[1] || '';
    } else {
      name = cells[0] || '';
      phone = cells[1] || '';
    }
    rows.push({ row: rowNo, phone, name, dept: '' });
  });
  return { rows, errors };
}

import {
  GlobalActivatedMemberItem,
  RoleItem,
  RoleMemberItem,
} from '../../lib/types';

/* 2026-11-05 全平台成员池条目（按手机号去重聚合；对应后端 GET /api/admin/member-pool） */
interface MemberPoolItem {
  phone: string;
  display_name: string;
  display_dept: string;
  status: 'active' | 'partial' | 'disabled';
  row_count: number;
  /* 2026-11-06 列口径（用户指定）：角色列=一共加入的全部角色个数；归属列=第一次被哪个管理员创建导入。 */
  role_count: number;
  admin_count: number;
  role_detail: string;
  first_owner_name: string;
  first_owner_username: string;
  activated_platforms: string[];
  name_inconsistent: boolean;
  is_left_org?: boolean;
  dept_inconsistent: boolean;
}

/* 角色列只展示任职个数（2026-11-06 用户指定：具体角色描述不需要） */

/* 成员池分页：每页 10 人（2026-11-06 用户要求，防数据量大时列表过长） */
const POOL_PAGE_SIZE = 10;

const MEMBER_PAGE_SIZE = 20;

export const RolesMembersView: React.FC = () => {
  const [portalPhone, setPortalPhone] = useState<string | null>(null);
  const {
    roles,
    refreshRoles,
    platform,
    isSuperAdmin,
    ownerFilter,
    setOwnerFilter,
    adminAccounts,
    memberDistinctTotal,
  } = useAppStore();
  const toast = useToast();

  const [selectedRoleId, setSelectedRoleId] = useState<string>('');
  const [memberKeyword, setMemberKeyword] = useState('');
  const [memberQuery, setMemberQuery] = useState('');
  const [rosterPager, setRosterPager] = useState({ roleId: '', platform, page: 1, q: '' });
  const memberPage = rosterPager.roleId === selectedRoleId && rosterPager.platform === platform
    && rosterPager.q === memberQuery ? rosterPager.page : 1;
  const activeRoster = useMemo(
    () => ({ roleId: selectedRoleId, platform, page: memberPage, q: memberQuery }),
    [selectedRoleId, platform, memberPage, memberQuery],
  );
  const rosterScope = useRef(activeRoster);
  const rosterRequestId = useRef(0);
  const [memberResult, setMemberResult] = useState<{
    scope: typeof activeRoster;
    items: RoleMemberItem[];
    total: number;
    error?: string;
  } | null>(null);
  const [loadingMembers, setLoadingMembers] = useState(false);

  useEffect(() => {
    rosterScope.current = activeRoster;
    if (rosterPager.roleId !== activeRoster.roleId || rosterPager.platform !== activeRoster.platform
      || rosterPager.q !== activeRoster.q) {
      setRosterPager(activeRoster);
    }
  }, [activeRoster, rosterPager]);
  useEffect(() => {
    const timer = window.setTimeout(() => setMemberQuery(memberKeyword.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [memberKeyword]);

  const visibleMemberResult = memberResult?.scope === activeRoster ? memberResult : null;
  const rosterLoading = loadingMembers || memberKeyword.trim() !== memberQuery || !visibleMemberResult;
  const members = rosterLoading ? [] : visibleMemberResult?.items ?? [];
  const memberTotal = memberResult?.scope.roleId === selectedRoleId && memberResult.scope.platform === platform
    && memberResult.scope.q === memberQuery ? memberResult.total : 0;
  const memberPageCount = Math.max(1, Math.ceil(memberTotal / MEMBER_PAGE_SIZE));

  const goToMemberPage = (page: number) => {
    setRosterPager({ ...activeRoster, page });
    document.getElementById('role-members-table')?.scrollIntoView({ block: 'start' });
  };

  // 从当前角色移除成员交互确认弹窗状态
  const [confirmRemoveRoleMember, setConfirmRemoveRoleMember] = useState<RoleMemberItem | null>(null);
  const [removingRoleMember, setRemovingRoleMember] = useState(false);

  // 单条添加成员表单（姓名 + 手机号 + 部门，部门由后台统一配置）
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  const [deptModalOpen, setDeptModalOpen] = useState(false);
  const [deptTree, setDeptTree] = useState<DeptTreeNode[]>([]);
  const [deptTreeLoading, setDeptTreeLoading] = useState(false);
  const [syncingDirectory, setSyncingDirectory] = useState(false);
  const [memberStatusPending, setMemberStatusPending] = useState<string | null>(null);

  // 2026-11-12 下午：成员列表批量删除（勾选 + 一次移除，用户要求「不要一个一个删」）
  const [selectedMemberPhones, setSelectedMemberPhones] = useState<Set<string>>(new Set());
  const [confirmBulkRemove, setConfirmBulkRemove] = useState(false);
  const [bulkRemoving, setBulkRemoving] = useState(false);

  // 2026-11-05 全平台成员池（仅超管）：按人聚合的全局名册与操作状态
  const [poolItems, setPoolItems] = useState<MemberPoolItem[]>([]);
  const [poolLoading, setPoolLoading] = useState(false);
  const [poolKeyword, setPoolKeyword] = useState('');
  const [poolBusyPhone, setPoolBusyPhone] = useState('');
  const [poolDeleting, setPoolDeleting] = useState<MemberPoolItem | null>(null);

  const loadPool = useCallback(async () => {
    if (!isSuperAdmin) return;
    setPoolLoading(true);
    try {
      /* 2026-11-06 平台口径：池内只展示当前办公软件的名册（张三案例的双平台部门分叉不再出现） */
      const res = await api.get<{ ok: boolean; items: MemberPoolItem[] }>(
        `/api/admin/member-pool?platform=${platform}`,
      );
      setPoolItems(res.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '加载成员池失败');
    } finally {
      setPoolLoading(false);
    }
  }, [isSuperAdmin, platform]);

  useEffect(() => {
    if (isSuperAdmin) void loadPool();
  }, [isSuperAdmin, loadPool]);

  const filteredPoolItems = useMemo(() => {
    if (!poolKeyword.trim()) return poolItems;
    const kw = poolKeyword.trim().toLowerCase();
    return poolItems.filter(
      (p) => p.phone.includes(kw) || (p.display_name || '').toLowerCase().includes(kw),
    );
  }, [poolItems, poolKeyword]);

  /* 分页：搜索词/平台切换回第一页；页码超出总页数时收敛到末页 */
  const [poolPage, setPoolPage] = useState(1);
  useEffect(() => setPoolPage(1), [poolKeyword]);
  useEffect(() => setPoolPage(1), [platform]);
  const poolTotalPages = Math.max(1, Math.ceil(filteredPoolItems.length / POOL_PAGE_SIZE));
  const poolCurrentPage = Math.min(poolPage, poolTotalPages);
  const pagedPoolItems = useMemo(
    () =>
      filteredPoolItems.slice(
        (poolCurrentPage - 1) * POOL_PAGE_SIZE,
        poolCurrentPage * POOL_PAGE_SIZE,
      ),
    [filteredPoolItems, poolCurrentPage],
  );

  const handlePoolToggleStatus = async (p: MemberPoolItem) => {
    setPoolBusyPhone(p.phone);
    try {
      await api.put(`/api/admin/member-pool/${encodeURIComponent(p.phone)}/status?platform=${platform}`, {
        enabled: p.status !== 'active',
      });
      toast.success(
        p.status !== 'active'
          ? `已在当前办公软件下启用「${p.display_name || p.phone}」的名下全部 ${p.row_count} 条名册记录。`
          : `已在当前办公软件下停用「${p.display_name || p.phone}」的名下全部 ${p.row_count} 条名册记录。`,
      );
      await Promise.all([loadPool(), refreshRoles()]);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '操作失败');
    } finally {
      setPoolBusyPhone('');
    }
  };

  const handlePoolDelete = async () => {
    if (!poolDeleting) return;
    setPoolBusyPhone(poolDeleting.phone);
    try {
      const res = await api.delete<{ ok: boolean; rows: number }>(
        `/api/admin/member-pool/${encodeURIComponent(poolDeleting.phone)}?platform=${platform}`,
      );
      toast.success(`已在当前办公软件下从全部名册移除「${poolDeleting.display_name || poolDeleting.phone}」（${res.rows} 条记录）。`);
      setPoolDeleting(null);
      await Promise.all([loadPool(), refreshRoles()]);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '删除失败');
    } finally {
      setPoolBusyPhone('');
    }
  };

  // 新建/编辑角色弹窗（无需角色编码）
  const [roleModalMode, setRoleModalMode] = useState<'none' | 'create' | 'edit'>('none');
  const [editingRoleId, setEditingRoleId] = useState('');
  const [roleNameInput, setRoleNameInput] = useState('');
  const [savingRole, setSavingRole] = useState(false);

  /* 2026-10-29：一人多角色 —— 把已在某角色的成员同时加入其他角色 */
  const [assignMember, setAssignMember] = useState<RoleMemberItem | null>(null);
  const [assignRoleIds, setAssignRoleIds] = useState<string[]>([]);
  const [assigningRoles, setAssigningRoles] = useState(false);
  const [memberRoleIds, setMemberRoleIds] = useState<string[]>([]);

  // CSV 批量导入弹窗
  const [importModalOpen, setImportModalOpen] = useState(false);
  // 2026-11-11 任务二：组织架构选择器弹窗
  const [orgPickerOpen, setOrgPickerOpen] = useState(false);
  // 2026-11-09：导入被拦截时列出全部错误行（等用户修复后再导）
  const [importErrors, setImportErrors] = useState<Array<{ row: number; phone: string; reason: string }>>([]);
  const [csvText, setCsvText] = useState('');
  const [importing, setImporting] = useState(false);

  // 跨管理员全局搜索已激活成员弹窗（若其他管理员已激活该成员，直接复用免二次激活）
  const [globalSearchOpen, setGlobalSearchOpen] = useState(false);
  const [globalKeyword, setGlobalKeyword] = useState('');
  const [globalList, setGlobalList] = useState<GlobalActivatedMemberItem[]>([]);
  const [loadingGlobal, setLoadingGlobal] = useState(false);
  const globalRequestId = useRef(0);
  const [pullingPhone, setPullingPhone] = useState<string | null>(null);
  const [selectedGlobalPhones, setSelectedGlobalPhones] = useState<string[]>([]);
  const [batchPulling, setBatchPulling] = useState(false);

  // 自动选中首个角色
  useEffect(() => {
    if (!selectedRoleId && roles.length > 0) {
      setSelectedRoleId(roles[0].role_id);
      return;
    }
    if (selectedRoleId && !roles.some((r) => r.role_id === selectedRoleId)) {
      setSelectedRoleId(roles[0]?.role_id || '');
    }
  }, [selectedRoleId, roles]);

  const loadMembers = useCallback(
    async (roleId: string) => {
      const scope = rosterScope.current;
      if (!roleId || roleId !== scope.roleId || platform !== scope.platform) return;
      const requestId = ++rosterRequestId.current;
      const isCurrent = () => requestId === rosterRequestId.current && scope === rosterScope.current;
      setLoadingMembers(true);
      try {
        const query = new URLSearchParams({
          platform: scope.platform, q: scope.q, page: String(scope.page), size: String(MEMBER_PAGE_SIZE),
        });
        const res = await api.get<{ ok: boolean; total: number; items: RoleMemberItem[] }>(
          `/api/admin/roles/${encodeURIComponent(roleId)}/members?${query}`,
        );
        if (!isCurrent()) return;
        setMemberResult({ scope, items: res.items, total: res.total });
        const lastPage = Math.max(1, Math.ceil(res.total / MEMBER_PAGE_SIZE));
        if (scope.page > lastPage) setRosterPager({ ...scope, page: lastPage });
      } catch (err) {
        if (isCurrent()) {
          setMemberResult({ scope, items: [], total: 0, error: err instanceof Error ? plainError(err.message) : '加载角色成员失败' });
        }
      } finally {
        if (isCurrent()) setLoadingMembers(false);
      }
    },
    [platform],
  );

  const loadDeptTree = async () => {
    setDeptTreeLoading(true);
    try {
      const query = new URLSearchParams({ platform, owner_id: String(ownerFilter) });
      const res = await api.get<{ ok: boolean; items: DeptTreeNode[] }>(`/api/admin/dept-tree?${query}`);
      setDeptTree(res.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '加载部门架构失败');
    } finally {
      setDeptTreeLoading(false);
    }
  };

  const handleSyncDirectory = async () => {
    setSyncingDirectory(true);
    try {
      const result = await api.post<{ total: number; updated: number; left: number; failed: number }>(
        '/api/admin/directory/sync', { platform },
      );
      const scope = rosterScope.current;
      await Promise.all([scope.platform === platform && loadMembers(scope.roleId), refreshRoles()]);
      const summary = `共核查 ${result.total} 人，刷新 ${result.updated} 人，疑似离职 ${result.left} 人，查询失败 ${result.failed} 人。`;
      if (result.left || result.failed) toast.warning(summary, '通讯录同步完成');
      else toast.success(summary, '通讯录同步完成');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '通讯录同步失败');
    } finally {
      setSyncingDirectory(false);
    }
  };

  useEffect(() => {
    if (selectedRoleId) {
      loadMembers(selectedRoleId);
    }
  }, [selectedRoleId, activeRoster, loadMembers]);

  /* 2026-11-12 下午：切换角色时清空批量勾选（避免把上一个角色的选择带到新角色上误删） */
  useEffect(() => {
    setSelectedMemberPhones(new Set());
  }, [selectedRoleId, platform]);

  const currentRole: RoleItem | undefined = useMemo(
    () => roles.find((r) => r.role_id === selectedRoleId),
    [roles, selectedRoleId],
  );

  /* 2026-11-05 拍板：超管在「全部管理员（全局总览）」视角下，角色卡按创建人分组展示；
     切到具体管理员视角时不分组（数据本来就都是该管理员的）。 */
  const roleOwnerGroups = useMemo(() => {
    if (!(isSuperAdmin && ownerFilter === 'all')) return null;
    const order: string[] = [];
    const by = new Map<string, { key: string; label: string; items: RoleItem[] }>();
    for (const r of roles) {
      const key = String(r.owner_username || '');
      if (!by.has(key)) {
        order.push(key);
        by.set(key, {
          key,
          label: `${r.owner_display_name || r.owner_username}（@${r.owner_username || '未知'}）`,
          items: [],
        });
      }
      by.get(key)!.items.push(r);
    }
    return order.map((k) => by.get(k)!);
  }, [roles, isSuperAdmin, ownerFilter]);


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

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!newName.trim() || !newPhone.trim()) {
      toast.warning('请填写用户名和 11 位手机号');
      return;
    }
    setAddingMember(true);
    try {
      const res = await api.post<{
        ok: boolean;
        global_reused?: boolean;
        source_owner_name?: string | null;
      }>(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
        phone: newPhone.trim(),
        name: newName.trim(),
        // 2026-11-11 用户拍板：部门不再手填——留空由后端官方通讯录反查回填官方部门
      });
      const addedName = newName.trim();
      const addedPhone = newPhone.trim();
      setNewName('');
      setNewPhone('');
      await Promise.all([
        loadMembers(selectedRoleId),
        refreshRoles(),
      ]);
      if (res?.global_reused) {
        toast.success(
          `成员「${addedName}」(${addedPhone}) 已添加至当前角色。全局检测到该手机号已在系统激活，已自动点亮为「已激活」免二次绑定！`,
          '全局激活自动复用',
        );
      } else {
        toast.success(`成员「${addedName}」(${addedPhone}) 已添加至当前角色。`, '添加成功');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '添加成员未成功');
    } finally {
      setAddingMember(false);
    }
  };

  const loadGlobalActivated = useCallback(
    async (q = '') => {
      if (!selectedRoleId) return;
      const requestId = ++globalRequestId.current;
      const isCurrent = () => requestId === globalRequestId.current
        && rosterScope.current.roleId === selectedRoleId && rosterScope.current.platform === platform;
      setLoadingGlobal(true);
      try {
        const query = new URLSearchParams({ q, platform, role_id: selectedRoleId });
        const res = await api.get<{ ok: boolean; items: GlobalActivatedMemberItem[] }>(
          `/api/admin/global-activated-members?${query}`,
        );
        if (isCurrent()) setGlobalList(res.items || []);
      } catch (err) {
        if (isCurrent()) toast.error(err instanceof Error ? plainError(err.message) : '全局检索已激活成员失败');
      } finally {
        if (isCurrent()) setLoadingGlobal(false);
      }
    },
    [platform, selectedRoleId, toast],
  );

  const openGlobalSearchModal = () => {
    setGlobalKeyword('');
    setSelectedGlobalPhones([]);
    setGlobalList([]);
    setGlobalSearchOpen(true);
    void loadGlobalActivated('');
  };

  const toggleSelectGlobalPhone = (phone: string) => {
    setSelectedGlobalPhones((prev) =>
      prev.includes(phone) ? prev.filter((p) => p !== phone) : [...prev, phone],
    );
  };

  const handleBatchPullGlobalMembers = async () => {
    if (!selectedRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    const targets = globalList.filter(
      (g) => selectedGlobalPhones.includes(g.phone) && !g.in_role,
    );
    if (targets.length === 0) {
      toast.warning('请先勾选尚未在当前角色中的已激活员工');
      return;
    }
    setBatchPulling(true);
    let addedCount = 0;
    try {
      for (const person of targets) {
        await api.post(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
          phone: person.phone,
          name: person.name,
          dept: person.dept || '',
        });
        addedCount += 1;
        markGlobalMemberInRole(person.phone);
      }
      toast.success(
        `已批量将 ${targets.length} 名已激活成员拉入角色「${currentRole?.role_name || ''}」，直接保持已激活状态无需二次绑定。`,
        '批量复用激活成功',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '批量拉入成员失败');
    } finally {
      if (addedCount > 0) await Promise.allSettled([loadMembers(selectedRoleId), refreshRoles()]);
      setBatchPulling(false);
    }
  };

  const markGlobalMemberInRole = (phone: string) => {
    setGlobalList((list) => list.map((member) => member.phone === phone ? { ...member, in_role: true } : member));
    setSelectedGlobalPhones((selected) => selected.filter((value) => value !== phone));
  };

  const handlePullGlobalMember = async (person: GlobalActivatedMemberItem) => {
    if (!selectedRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    setPullingPhone(person.phone);
    try {
      await api.post(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
        phone: person.phone,
        name: person.name,
        dept: person.dept || '',
      });
      markGlobalMemberInRole(person.phone);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        `已将全局已激活成员「${person.name}」(${person.phone}) 拉入角色「${currentRole?.role_name || ''}」，直接保持已激活状态无需二次绑定。`,
        '直接复用激活成功',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '拉入成员失败');
    } finally {
      setPullingPhone(null);
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
      /* 拉不到就不置灰；提交时后端按 DUPLICATE 跳过，不会重复插入 */
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

  // P2-1: 快捷切换成员启用/停用
  const handleQuickToggleMemberStatus = async (m: RoleMemberItem) => {
    if (!selectedRoleId) return;
    const nextStatus = m.status === 'active' ? 'disabled' : 'active';
    setMemberStatusPending(m.phone);
    try {
      await api.put(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(m.phone)}`,
        { enabled: nextStatus === 'active' },
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
    } finally {
      setMemberStatusPending(null);
    }
  };

  const handleToggleRoleStatus = async (r: RoleItem) => {
    const nextEnabled = r.enabled === false;
    try {
      await api.put(`/api/admin/roles/${encodeURIComponent(r.role_id)}`, {
        enabled: nextEnabled,
      });
      await refreshRoles();
      toast.success(
        nextEnabled
          ? `角色分组「${r.role_name}」已启用。`
          : `角色分组「${r.role_name}」已停用，名下派单将暂停。`,
        nextEnabled ? '角色已启用' : '角色已停用',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '切换角色状态失败');
    }
  };

  // 确认从当前角色移除成员（保留全局激活绑定与其他角色关系）
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
      setSelectedMemberPhones((previous) => {
        const next = new Set(previous);
        next.delete(member.phone);
        return next;
      });
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        `已将「${member.name}」(${member.phone}) 从角色「${roleName}」中移除（该平台上已无其它角色归属时，激活绑定一并清除）。`,
        '已从角色移除',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移出角色成员失败');
    } finally {
      setRemovingRoleMember(false);
    }
  };

  /* 2026-11-12 下午：批量移除成员（用户要求「不要一个一个删」）——
     按接口的单批上限提交；逐条记录移除结果。 */
  const toggleMemberSelected = (phone: string) => {
    setSelectedMemberPhones((prev) => {
      const next = new Set(prev);
      if (next.has(phone)) next.delete(phone);
      else next.add(phone);
      return next;
    });
  };

  const handleConfirmBulkRemoveFromRole = async () => {
    if (!selectedRoleId || selectedMemberPhones.size === 0) return;
    const roleName = currentRole?.role_name || '当前角色';
    setBulkRemoving(true);
    const phones = [...selectedMemberPhones];
    const deleted = new Set<string>();
    const failed: Array<{ phone: string; message: string }> = [];
    try {
      for (let i = 0; i < phones.length; i += 500) {
        const response = await api.post<{ deleted: string[]; failed: Array<{ phone: string; message: string }> }>(
          `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/batch-delete`,
          { phones: phones.slice(i, i + 500) },
        );
        for (const phone of response.deleted || []) deleted.add(phone);
        failed.push(...(response.failed || []));
      }
      setConfirmBulkRemove(false);
      if (!failed.length) {
        toast.success(`已从角色「${roleName}」批量移除 ${deleted.size} 名成员。`, '批量移除完成');
      } else {
        toast.warning(
          `已移除 ${deleted.size} 人，${failed.length} 人未移除。${failed.map((item) => `${item.phone} ${item.message}`).join('；')}`,
          '批量移除部分完成',
        );
      }
    } catch (err) {
      const error = err instanceof Error ? plainError(err.message) : '批量移除成员失败';
      toast.error(deleted.size ? `已移除 ${deleted.size} 人，后续批次停止。 ${error}` : error);
    } finally {
      setSelectedMemberPhones((current) => new Set([...current].filter((phone) => !deleted.has(phone))));
      setBulkRemoving(false);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
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
      // 2026-11-09：后端接口收的是 csv_rows（数组），原来这里传 csv_text → 协议不匹配、功能实际不可用。
      // 现在本地把 CSV 文本解析成行数组再提交。
      const { rows, errors } = parseCsvRows(csvText);
      // 2026-11-13：行数上限与后端 IMPORT_ROW_LIMIT=5000 对齐——超限本地直接拦截，不发大请求
      if (rows.length > 5000) {
        toast.error(
          `单次最多导入 5000 人（当前 ${rows.length} 行），请分批导入。`,
          '导入被拦截',
        );
        return;
      }
      if (errors.length > 0) {
        // 与后端整批拦截同一交互：列出全部问题行、不关窗、一行都不写入
        setImportErrors(errors);
        toast.error(
          `导入未执行：${errors.length} 行有问题，已全部列出（本次一行都没有写入）。请修复后重新导入。`,
          '导入被拦截',
        );
        return;
      }
      if (!rows.length) {
        toast.warning('请填写至少一条带手机号的成员记录');
        return;
      }
      const res = await api.post<{
        ok: boolean;
        will_add?: number;
        added?: number;
        already_exists?: number;
        already_exists_phones?: string[];
        blocked?: boolean;
        /* 2026-11-10 对齐后端实际返回：global_reused 是布尔、数字在 global_reused_count；
           departments_created 是后端字段（此前误读成 auto_created_depts） */
        global_reused?: boolean;
        global_reused_count?: number;
        departments_created?: string[];
        synced_global_rows?: number;
        invalid?: Array<{ row: number; phone: string; reason: string }>;
      }>(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/import`, {
        csv_rows: rows,
        dry_run: false,
      });
      const invalid = res.invalid || [];
      const reusedN = res.global_reused_count || 0;
      const reusedNote =
        reusedN > 0
          ? `（其中 ${reusedN} 人已复用当前办公软件的通讯录档案与激活绑定）`
          : '';
      // 2026-11-09 用户口径：「待修复后才全量导入成功」——
      // 有任何错误行就整批不写入，把**全部**错误行列出来，且**不关窗**（用户改完再导）。
      if (invalid.length > 0 || res.blocked) {
        setImportErrors(invalid);
        toast.error(
          `导入未执行：${invalid.length} 行有问题，已全部列出（本次一行都没有写入）。请修复后重新导入。`,
          '导入被拦截',
        );
        return;
      }
      await Promise.all([
        loadMembers(selectedRoleId),
        refreshRoles(),
      ]);
      setImportErrors([]);
      setImportModalOpen(false);
      const addedN = res.added ?? res.will_add ?? 0;
      const skippedN = res.already_exists ?? 0;
      /* 2026-11-21 用户拍板：本角色已存在的成员按「跳过」处理，不再整批报错——
         情况 B（新增 0 且全部为已存在）：给清晰提示、不弹红框；情况 A：正常成功提示 + 跳过人数。 */
      if (addedN === 0 && skippedN > 0) {
        toast.success(
          skippedN === 1
            ? '该成员已导入本角色组，无需重复导入。'
            : `这 ${skippedN} 名成员均已导入本角色组，无需重复导入。`,
          '无需重复导入',
        );
        return;
      }
      const syncNote =
        res.synced_global_rows && res.synced_global_rows > 0
          ? `；其中 ${res.synced_global_rows} 条名册记录的姓名/部门已同步到全局`
          : '';
      const skippedNote = skippedN > 0 ? `，已自动跳过本角色已存在的 ${skippedN} 名成员` : '';
      toast.success(
        `批量导入成功：新增 ${addedN} 名成员${skippedNote}${reusedNote}${syncNote}。`,
        'CSV 导入完成',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : 'CSV 批量导入未成功', 'CSV 批量导入失败');
    } finally {
      setImporting(false);
    }
  };



  return (
    <div className="space-y-5">
      {/* 顶部标题与管理员视角（统一标准组件） */}
      <AdminScopeHeader
        title="角色与成员名册"
        badge={`共 ${roles.length} 个角色分组`}
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>管理各业务角色下的成员名单（支持姓名、手机号；部门由官方通讯录自动匹配回填；跨管理员导入自动匹配复用全局已激活状态）</span>
            <span aria-hidden="true" className="text-slate-300">·</span>
            <span>
              在册成员 <strong className="font-semibold text-slate-800 tabular-nums">{memberDistinctTotal}</strong> 人
              <span className="text-slate-400">（按人去重）</span>
            </span>
          </span>
        }
      />

      {/* 业务角色分组横向选择卡片带 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-5 space-y-4">

        {/* 业务角色分组横向选择卡片带 */}
        <div>
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-xs font-semibold text-slate-500">
              选择业务角色分组（点击卡片切换下方名册）
            </span>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setDeptTree([]);
                  setDeptModalOpen(true);
                  void loadDeptTree();
                }}
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer"
              >
                <Building2 className="h-3.5 w-3.5" /> 部门架构
              </button>
              {!isSuperAdmin && (
                <button
                  type="button"
                  disabled={syncingDirectory}
                  onClick={() => void handleSyncDirectory()}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100 cursor-pointer disabled:opacity-60"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${syncingDirectory ? 'animate-spin' : ''}`} />
                  {syncingDirectory ? '正在同步…' : '同步最新部门'}
                </button>
              )}
            </div>
          </div>
          {(roleOwnerGroups ?? [{ key: 'all', label: '', items: roles }]).map((group) => (
          <div key={group.key} className="space-y-2.5">
            {group.label && (
              <div className="flex items-center gap-2 mb-1">
                <User className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                <span className="text-xs font-semibold text-slate-600">创建人：{group.label}</span>
                <span className="text-[10px] text-slate-400">{group.items.length} 个角色分组</span>
                <div className="flex-1 h-px bg-slate-200/70" />
              </div>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {group.items.map((r) => {
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
                        {isSuperAdmin && r.owner_username && (
                          <div className="text-[10px] text-slate-400 truncate mt-0.5">
                            创建人：{r.owner_display_name || r.owner_username}
                          </div>
                        )}
                      </div>
                    </div>

                    <div
                      className="flex items-center gap-1 shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {isSuperAdmin ? (
                        <span
                          className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border ${
                            r.enabled !== false
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                              : 'bg-slate-100 text-slate-500 border-slate-200'
                          }`}
                        >
                          {r.enabled !== false ? '已启用' : '已停用'}
                        </span>
                      ) : (
                      <button
                        type="button"
                        onClick={() => void handleToggleRoleStatus(r)}
                        className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border cursor-pointer transition-colors ${
                          r.enabled !== false
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                            : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200'
                        }`}
                        title={r.enabled !== false ? '点击停用整个角色分组' : '点击启用整个角色分组'}
                      >
                        {r.enabled !== false ? '已启用' : '已停用'}
                      </button>
                      )}
                      {!isSuperAdmin && (
                        <>
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
                        </>
                      )}
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
            {!group.label && !isSuperAdmin && (
              /* 快捷新建角色虚线卡片（仅分级管理员可新建角色；不分组场景才渲染） */
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
          ))}
        </div>
      </div>

      {/* 下方主工作区：全宽展示当前角色成员名册（已按线上最新版本移除右侧待归纳池面板） */}
      <div id="role-members-section" className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
        {currentRole ? (
          <>
            {/* 第一层：角色名册标题 + 搜索框 + 全局搜索已激活成员 + CSV 批量导入 */}
            <div className="p-4 sm:px-5 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-slate-900 text-base">
                    {currentRole.role_name}
                  </h3>
                  <span className="text-xs text-slate-400 font-normal tabular-nums">
                    · 共 {currentRole.member_count} 名成员
                    {memberQuery && !rosterLoading && !visibleMemberResult?.error ? `（匹配 ${memberTotal} 人）` : ''}
                  </span>
                </div>
                <p className="text-xs text-slate-500 mt-0.5">
                  管理该角色下的派单人员名单；添加或导入手机号时自动全局匹配已激活人员，免二次激活
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2 shrink-0">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={memberKeyword}
                    onChange={(e) => setMemberKeyword(e.target.value)}
                    placeholder="搜索姓名、手机号或部门…"
                    className="bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-1.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44 sm:w-52 transition-colors"
                  />
                </div>
                {!isSuperAdmin && (
                  <>
                    <button
                      type="button"
                      onClick={openGlobalSearchModal}
                      className="px-3 py-1.5 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200/80 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                      title="跨管理员全局搜索已在系统激活过的成员，一键拉入当前角色免二次激活"
                    >
                      <ShieldCheck className="w-3.5 h-3.5" />
                      <span>全局搜索已激活成员</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setImportErrors([]);
                        setImportModalOpen(true);
                      }}
                      className="px-3 py-1.5 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200/80 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                      title="手机号必填，姓名选填。核验后自动回填姓名和部门。"
                    >
                      <Upload className="w-3.5 h-3.5" />
                      <span>CSV 批量导入</span>
                    </button>
                    {/* 2026-11-12 A1：组织架构添加入口暂时隐藏（应用户要求收敛入口，代码保留待明确启用）。
                    <button
                      onClick={() => setOrgPickerOpen(true)}
                      className="px-3 py-1.5 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200/80 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                      title="从官方通讯录组织架构按部门/人员勾选拉入，入册即自动激活"
                    >
                      <Network className="w-3.5 h-3.5" />
                      <span>从组织架构添加</span>
                    </button>
                    */}
                  </>
                )}
              </div>
            </div>

            {/* 第二层：单行快速添加成员表单（仅分级管理员可添加成员） */}
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
                {/* 2026-11-11 用户拍板：部门不再手填——填入姓名+手机号后由官方通讯录反查回填官方部门 */}
                <button
                  type="submit"
                  disabled={addingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-60 shrink-0 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>添加成员</span>
                </button>
              </form>
            )}

            {/* 2026-11-12 下午：批量移除工具条（勾选后出现；超管只读不参与） */}
            {!isSuperAdmin && selectedMemberPhones.size > 0 && (
              <div className="px-4 sm:px-5 py-2.5 bg-rose-50/70 border-b border-rose-100 flex flex-wrap items-center gap-2.5 text-xs">
                <span className="font-semibold text-rose-700 flex items-center gap-1.5">
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>已勾选 {selectedMemberPhones.size} 名成员</span>
                </span>
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={() => setSelectedMemberPhones(new Set())}
                  className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 font-semibold cursor-pointer shrink-0"
                >
                  取消勾选
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmBulkRemove(true)}
                  className="px-3 py-1.5 rounded-xl bg-rose-600 hover:bg-rose-700 text-white font-semibold cursor-pointer shrink-0 inline-flex items-center gap-1.5"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>批量移除（{selectedMemberPhones.size} 人）</span>
                </button>
              </div>
            )}

            {/* 第三层：成员列表表格 */}
            <div id="role-members-table" className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200/80 text-[11px] font-semibold text-slate-500 bg-white">
                    {!isSuperAdmin && (
                      <th className="py-3 pl-5 pr-2 w-10">
                        <input
                          type="checkbox"
                          aria-label="全选/取消全选当前页成员"
                          disabled={members.length === 0}
                          checked={members.length > 0 && members.every((m) => selectedMemberPhones.has(m.phone))}
                          ref={(input) => {
                            if (input) input.indeterminate = !input.checked && members.some((m) => selectedMemberPhones.has(m.phone));
                          }}
                          onChange={(e) => {
                            const checked = e.currentTarget.checked;
                            setSelectedMemberPhones((previous) => {
                              const next = new Set(previous);
                              for (const member of members) {
                                if (checked) next.add(member.phone);
                                else next.delete(member.phone);
                              }
                              return next;
                            });
                          }}
                          className="w-3.5 h-3.5 accent-rose-600 cursor-pointer"
                        />
                      </th>
                    )}
                    <th className={`py-3 ${isSuperAdmin ? 'pl-5' : 'pl-2'} pr-3`}>成员姓名</th>
                    <th className="py-3 px-3">手机号码</th>
                    <th className="py-3 px-3">部门</th>
                    <th className="py-3 px-3">激活状态</th>
                    <th className="py-3 px-3">派单状态</th>
                    <th className="py-3 pl-3 pr-5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-xs">
                  {rosterLoading ? (
                    <tr>
                      <td colSpan={isSuperAdmin ? 6 : 7} className="py-12 text-center text-slate-400">
                        正在加载角色成员名册…
                      </td>
                    </tr>
                  ) : visibleMemberResult?.error ? (
                    <tr>
                      <td colSpan={isSuperAdmin ? 6 : 7} className="py-8 text-center text-slate-600">
                        {visibleMemberResult.error}
                        <button type="button" onClick={() => void loadMembers(selectedRoleId)} className="ml-3 font-semibold text-blue-700 hover:underline">
                          重试
                        </button>
                      </td>
                    </tr>
                  ) : members.length === 0 ? (
                    <tr>
                      <td colSpan={isSuperAdmin ? 6 : 7} className="py-12 text-center text-slate-400">
                        该角色下暂无匹配成员，可在上方直接添加、CSV 批量导入或点击「全局搜索已激活成员」拉入。
                      </td>
                    </tr>
                  ) : (
                    members.map((m) => (
                      <tr
                        key={m.phone}
                        className="hover:bg-slate-50/70 transition-colors group"
                      >
                        {!isSuperAdmin && (
                          <td className="py-3.5 pl-5 pr-2">
                            <input
                              type="checkbox"
                              aria-label={`勾选成员 ${m.name}`}
                              checked={selectedMemberPhones.has(m.phone)}
                              onChange={() => toggleMemberSelected(m.phone)}
                              onClick={(e) => e.stopPropagation()}
                              className="w-3.5 h-3.5 accent-rose-600 cursor-pointer"
                            />
                          </td>
                        )}
                        <td className={`py-3.5 ${isSuperAdmin ? 'pl-5' : 'pl-2'} pr-3`}>
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-lg bg-slate-100 text-slate-700 font-bold text-xs flex items-center justify-center shrink-0">
                              {m.name.slice(0, 1)}
                            </div>
                            <span className="font-bold text-slate-900">{m.name}</span>
                            {m.is_left_org && <span className="text-xs font-semibold text-rose-700">已移出通讯录（疑似离职）</span>}
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
                          {isSuperAdmin ? (
                            /* 2026-11-05 拍板：超管对业务数据只读 —— 原来这里的「快速切换派单状态」
                               是假入口（后端一律 SUPER_ADMIN_READONLY 403），改为纯状态徽章。 */
                            <span
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border ${
                                m.status === 'active'
                                  ? 'bg-blue-50/70 text-blue-700 border-blue-200/80'
                                  : 'bg-slate-100 text-slate-500 border-slate-200'
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
                            </span>
                          ) : (
                          /* 2026-11-10 用户口径：移除点击快速切换（能力统一收进「修改」弹窗），改纯徽章 */
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border ${
                              m.status === 'active'
                                ? 'bg-blue-50/70 text-blue-700 border-blue-200/80'
                                : 'bg-slate-100 text-slate-500 border-slate-200'
                            }`}
                            title="派单状态在右侧「修改」里调整"
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
                          </span>
                          )}
                        </td>
                        <td className="py-3.5 pl-3 pr-5 text-right">
                          {isSuperAdmin ? (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-500 text-xs font-medium border border-slate-200 whitespace-nowrap">
                              <Lock className="w-3 h-3" />
                              <span>只读</span>
                            </span>
                          ) : (
                            <div className="inline-flex items-center justify-end gap-1.5">
                              <button className="text-xs text-blue-600 px-2 py-1" onClick={() => setPortalPhone(m.phone)}>H5 创建任务授权</button>
                              <button
                                onClick={() => void openAssignRolesModal(m)}
                                title="把该成员同时加入其他业务角色（一人可兼多角色，原角色保留）"
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-emerald-50 text-slate-700 hover:text-emerald-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap"
                              >
                                <Users className="w-3 h-3" />
                                <span>加入其他角色</span>
                              </button>
                              <button
                                type="button"
                                disabled={memberStatusPending !== null}
                                onClick={() => void handleQuickToggleMemberStatus(m)}
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap disabled:opacity-60"
                              >
                                {memberStatusPending === m.phone
                                  ? '处理中…' : m.status === 'active' ? '停用派单' : '恢复派单'}
                              </button>
                              <button
                                onClick={() => setConfirmRemoveRoleMember(m)}
                                title="将该成员从当前角色分组移除"
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-rose-50 text-slate-600 hover:text-rose-600 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                              >
                                <Trash2 className="w-3 h-3" />
                                <span>移除</span>
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {memberTotal > 0 && (
              <nav aria-label="角色成员分页" className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-5 py-3 text-xs">
                <span className="text-slate-500 tabular-nums">共 {memberTotal} 人 · 每页 {MEMBER_PAGE_SIZE} 人</span>
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" disabled={rosterLoading || memberPage <= 1}
                    onClick={() => goToMemberPage(1)}
                    className="rounded-lg bg-slate-100 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-45">首页</button>
                  <button type="button" disabled={rosterLoading || memberPage <= 1}
                    onClick={() => goToMemberPage(memberPage - 1)}
                    className="rounded-lg bg-slate-100 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-45">上一页</button>
                  <span aria-live="polite" className="px-1 font-semibold tabular-nums text-slate-600">{memberPage} / {memberPageCount} 页</span>
                  <button type="button" disabled={rosterLoading || memberPage >= memberPageCount}
                    onClick={() => goToMemberPage(memberPage + 1)}
                    className="rounded-lg bg-slate-100 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-45">下一页</button>
                  <button type="button" disabled={rosterLoading || memberPage >= memberPageCount}
                    onClick={() => goToMemberPage(memberPageCount)}
                    className="rounded-lg bg-slate-100 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-45">末页</button>
                </div>
              </nav>
            )}
          </>
        ) : (
          <div className="p-14 text-center text-slate-400 text-xs">
            请先在上方选择或新建一个业务角色分组。
          </div>
        )}
      </div>

      {/* TODO 2026-11-05：全平台已激活成员总览与「停用/恢复派单」管控功能
           依赖后端 PUT /api/admin/global-activated-members/{phone}/status 端点（当前未实现），
           后端补齐后可恢复此区块（源码见 Google 交付包 RolesMembersView）。 */}

      {deptModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <section role="dialog" aria-modal="true" aria-labelledby="department-tree-title" className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 id="department-tree-title" className="font-bold text-slate-900">部门架构</h3>
              <button type="button" aria-label="关闭部门架构" onClick={() => setDeptModalOpen(false)} className="p-1 rounded-lg hover:bg-slate-100 text-slate-500">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="mt-3 max-h-[60vh] overflow-y-auto">
              {deptTreeLoading ? <p role="status" className="text-xs text-slate-500">正在加载部门架构…</p>
                : deptTree.length ? <DepartmentTree nodes={deptTree} />
                : <p className="text-xs text-slate-500">当前名册暂无部门数据</p>}
            </div>
          </section>
        </div>
      )}

      {/* 交互确认弹窗：从当前角色中移除成员 */}
      {confirmRemoveRoleMember && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-rose-50 text-rose-600 flex items-center justify-center shrink-0">
                  <Trash2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认将该成员移出当前角色？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    移出后从当前角色名册剔除；未完成任务实例转「已取消」
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
              <div className="p-3 rounded-xl bg-emerald-50/80 border border-emerald-200/80 text-emerald-900 space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-emerald-700 shrink-0" />
                  <span>激活状态按平台判断，跨组保留</span>
                </div>
                <p className="text-[11px] text-emerald-800">
                  该手机号在当前办公软件下若还有其它角色分组，激活绑定保持不动；若已无任何角色归属，
                  该平台绑定一并清除，后续需重新导入（导入即自动反查通讯录重新激活）。
                  其未完成的任务实例转「已取消」，已完成的按既定口径一并清除。
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
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {removingRoleMember ? '正在移除…' : '确认从当前角色移除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 2026-11-12 下午：批量移除成员确认弹窗（语义与单删一致，一次移除勾选的全部成员） */}
      {confirmBulkRemove && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-rose-50 text-rose-600 flex items-center justify-center shrink-0">
                  <Trash2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认批量移除 {selectedMemberPhones.size} 名成员？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    从角色「{currentRole.role_name}」名册中移出；语义与单个移除完全一致
                  </p>
                </div>
              </div>
              <button
                onClick={() => setConfirmBulkRemove(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-3 rounded-xl bg-rose-50/80 border border-rose-200/80 text-rose-900 space-y-1 text-xs leading-relaxed">
              <p className="font-semibold">移除后果（与单个移除同款）：</p>
              <p>· 名册行立即删除；该成员在当前平台已无其它角色归属时，激活绑定一并清除（后续重新导入即自动反查激活）。</p>
              <p>· 未完成任务实例转「已取消」，已完成实例按既定口径一并清除。</p>
              <p>· 单个成员移除失败不影响其余成员，结果逐条回报。</p>
            </div>
            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setConfirmBulkRemove(false)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmBulkRemoveFromRole()}
                disabled={bulkRemoving || selectedMemberPhones.size === 0}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {bulkRemoving ? '正在批量移除…' : `确认批量移除（${selectedMemberPhones.size} 人）`}
              </button>
            </div>
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

      {portalPhone && <PortalGrantDialog key={platform + portalPhone} phone={portalPhone} platform={platform} onClose={() => setPortalPhone(null)} />}

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

      {/* CSV 批量导入，姓名与部门可由通讯录回填。 */}
      {importModalOpen && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  批量导入成员至「{currentRole.role_name}」
                </h3>
                <p className="text-[11px] text-slate-400">
                  每行一条记录。可只填手机号，也可填写「姓名,手机号」或「手机号,姓名」。姓名留空时自动补全，填写姓名时会核对一致性。部门自动回填。
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
                placeholder={'手机号\n13900000000\n13900000001'}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 font-mono text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 leading-relaxed"
              />
              {/* 2026-11-09：导入被拦截 → 列出全部错误行（不关窗，等用户修复后再导） */}
              {importErrors.length > 0 && (
                <div className="rounded-xl border border-rose-200 bg-rose-50/70 p-3 text-rose-800">
                  <div className="font-semibold text-xs mb-1.5">
                    本次导入未执行（{importErrors.length} 行有问题，一行都没有写入）
                  </div>
                  <ul className="space-y-1 max-h-40 overflow-y-auto">
                    {importErrors.map((e, i) => (
                      <li key={`${e.row}-${i}`} className="text-[11px] leading-5">
                        <span className="font-mono font-bold">第 {e.row} 行</span>
                        {e.phone ? <span className="font-mono">（{e.phone}）</span> : null}
                        <span>：{e.reason}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 text-[11px] text-rose-700">
                    请按上面的提示修正后再点「开始导入」；修正前不会写入任何一行。
                  </div>
                </div>
              )}
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
                  {importing ? (
                    <span role="status" className="inline-flex items-center gap-1.5">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> 正在导入…
                    </span>
                  ) : '开始导入'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 跨管理员全局搜索已激活成员弹窗（其他管理员已激活则免二次激活直接拉入） */}
      {globalSearchOpen && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-xl w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-emerald-600" />
                  <span>全局搜索已激活成员 · 拉入「{currentRole.role_name}」</span>
                </h3>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  若其他管理员或机器人已激活过某位员工，无需员工二次激活，可直接搜索手机号或姓名拉入本角色
                </p>
              </div>
              <button
                onClick={() => setGlobalSearchOpen(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                <input
                  type="text"
                  autoFocus
                  value={globalKeyword}
                  onChange={(e) => {
                    setGlobalKeyword(e.target.value);
                    void loadGlobalActivated(e.target.value);
                  }}
                  placeholder="输入手机号、姓名或部门全局搜索已激活员工…"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>
              <button
                type="button"
                onClick={() => void loadGlobalActivated(globalKeyword)}
                className="px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold rounded-xl text-xs cursor-pointer"
              >
                检索
              </button>
            </div>

            <div className="max-h-80 overflow-y-auto divide-y divide-slate-100 border border-slate-200/80 rounded-xl">
              {loadingGlobal ? (
                <div className="py-10 text-center text-xs text-slate-400">
                  正在检索全平台已激活成员库…
                </div>
              ) : globalList.length === 0 ? (
                <div className="py-10 text-center text-xs text-slate-400">
                  未检索到匹配的已激活员工，也可直接在单条添加或 CSV 导入中填入手机号。
                </div>
              ) : (
                globalList.map((g) => {
                  const alreadyInRole = Boolean(g.in_role);
                  const isPulling = pullingPhone === g.phone;
                  const checked = selectedGlobalPhones.includes(g.phone);
                  return (
                    <div
                      key={g.phone}
                      className="p-3.5 flex items-center justify-between gap-3 hover:bg-slate-50/70 text-xs"
                    >
                      <label className="flex items-center gap-3 min-w-0 cursor-pointer flex-1">
                        <input
                          type="checkbox"
                          disabled={alreadyInRole}
                          checked={alreadyInRole || checked}
                          onChange={() => toggleSelectGlobalPhone(g.phone)}
                          className="w-3.5 h-3.5 accent-blue-600 shrink-0"
                        />
                        <div className="min-w-0 space-y-0.5">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-bold text-slate-900">{g.name}</span>
                            <span className="font-mono font-semibold text-slate-700 tabular-nums">
                              {g.phone}
                            </span>
                            {g.dept && (
                              <span className="text-slate-500">· {g.dept}</span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-400 flex items-center gap-2 flex-wrap">
                            <span className="text-emerald-700 font-medium inline-flex items-center gap-1">
                              <ShieldCheck className="w-3 h-3" />
                              <span>已在系统激活（免二次激活）</span>
                            </span>
                          </div>
                        </div>
                      </label>

                      {alreadyInRole ? (
                        <span className="px-2.5 py-1.5 rounded-lg bg-slate-100 text-slate-400 font-semibold text-xs shrink-0">
                          已在当前角色
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={isPulling || batchPulling}
                          onClick={() => void handlePullGlobalMember(g)}
                          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 disabled:opacity-60"
                        >
                          <Plus className="w-3.5 h-3.5" />
                          <span>{isPulling ? '拉入中…' : '直接拉入当前角色'}</span>
                        </button>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            <div className="flex items-center justify-between pt-1 text-xs">
              <span className="text-slate-500">
                已勾选 <strong className="text-slate-800">{selectedGlobalPhones.length}</strong> 人
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setGlobalSearchOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  关闭
                </button>
                <button
                  type="button"
                  disabled={batchPulling || selectedGlobalPhones.length === 0}
                  onClick={() => void handleBatchPullGlobalMembers()}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-xl cursor-pointer disabled:opacity-50"
                >
                  {batchPulling ? '批量拉入中…' : `批量拉入已选成员 (${selectedGlobalPhones.length})`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 2026-11-05 全平台成员池（仅超管可见）：按「人」管理的全局名册。同一人在多个管理员/多个
          角色里是不同的行，这里按手机号聚合成一行；所有操作全局同步到该手机号的全部名册行，
          与分级管理员在角色内的操作（仅作用于自己名下）相区分。 */}
      {isSuperAdmin && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
          <div className="p-4 sm:px-5 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-slate-900 text-base">成员池（{platform === 'wecom' ? '企业微信' : '量子密信'}）</h3>
                  <span className="text-xs text-slate-400 font-normal tabular-nums">
                    · {filteredPoolItems.length} / {poolItems.length} 人（按手机号去重）
                  </span>
                </div>
                <p className="text-xs text-slate-500 mt-0.5">
                  展示当前所选办公软件下全部分级管理员的在册成员（按手机号去重）；停用 / 恢复 / 删除
                  <strong className="text-slate-700">仅作用于该平台下全部管理员名下的名册记录</strong>，其它平台不受影响；手机号不可修改。
                </p>
              </div>
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              <div className="relative">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                <input
                  type="text"
                  value={poolKeyword}
                  onChange={(e) => setPoolKeyword(e.target.value)}
                  placeholder="搜索姓名或手机号…"
                  className="bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-1.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44 sm:w-52 transition-colors"
                />
              </div>
              <button
                type="button"
                onClick={() => void loadPool()}
                disabled={poolLoading}
                className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold text-xs inline-flex items-center gap-1.5 cursor-pointer transition-colors disabled:opacity-60"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${poolLoading ? 'animate-spin' : ''}`} />
                <span>刷新</span>
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/70 border-b border-slate-100 text-left">
                  <th className="px-5 py-2.5 text-xs font-semibold text-slate-500">姓名</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">手机号</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">部门</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">角色</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">归属</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">激活状态</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500">派单状态</th>
                  <th className="px-3 py-2.5 text-xs font-semibold text-slate-500 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredPoolItems.length === 0 && (
                  <tr>
                    <td colSpan={8} className="py-10 text-center text-xs text-slate-400">
                      {poolLoading ? '正在加载成员池…' : '暂无在册成员（或无匹配搜索的结果）'}
                    </td>
                  </tr>
                )}
                {pagedPoolItems.map((p) => {
                  const busy = poolBusyPhone === p.phone;
                  return (
                    <tr key={p.phone} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-1.5">
                          <span className="font-semibold text-slate-900 whitespace-nowrap">{p.display_name || '—'}</span>
                          {p.is_left_org && <span className="text-xs font-semibold text-rose-700">已移出通讯录（疑似离职）</span>}
                          {p.name_inconsistent && (
                            <span
                              title={`该手机号在 ${p.row_count} 个角色行中，姓名不一致（点「修改」可统一）`}
                              className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 font-medium whitespace-nowrap"
                            >
                              姓名不一致
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-3.5 font-mono text-xs text-slate-700 tabular-nums whitespace-nowrap">
                        {p.phone}
                      </td>
                      <td className="px-3 py-3.5 text-xs text-slate-600">
                        <div className="flex items-center gap-1.5">
                          <span>{p.display_dept || '—'}</span>
                          {p.dept_inconsistent && (
                            <span
                              title="该手机号在多个角色行中，部门不一致（点「修改」可统一）"
                              className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 font-medium whitespace-nowrap"
                            >
                              部门不一致
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-3.5 text-xs whitespace-nowrap">
                        <div className="font-semibold text-slate-700 inline-flex items-center gap-1">
                          <Users className="w-3.5 h-3.5 text-slate-400" />
                          <span>{p.role_count} 个</span>
                        </div>
                      </td>
                      <td className="px-3 py-3.5 text-xs whitespace-nowrap">
                        <span className="font-semibold text-slate-700">
                          {p.first_owner_name || '—'}
                        </span>
                        <div className="text-[10px] text-slate-400">
                          @{p.first_owner_username || '—'} 首次导入
                        </div>
                      </td>
                      <td className="px-3 py-3.5 text-xs whitespace-nowrap">
                        {p.activated_platforms.includes(platform) ? (
                          <span className="inline-flex items-center gap-1 text-emerald-700 font-medium">
                            <ShieldCheck className="w-3.5 h-3.5" />
                            <span>已激活</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-slate-400">
                            <Clock className="w-3.5 h-3.5" />
                            <span>未激活</span>
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-3.5 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border ${
                            p.status === 'active'
                              ? 'bg-blue-50/70 text-blue-700 border-blue-200/80'
                              : p.status === 'partial'
                                ? 'bg-amber-50 text-amber-700 border-amber-200'
                                : 'bg-slate-100 text-slate-500 border-slate-200'
                          }`}
                        >
                          {p.status === 'active' ? (
                            <><CheckCircle2 className="w-3.5 h-3.5" /><span>启用</span></>
                          ) : p.status === 'partial' ? (
                            <><Clock className="w-3.5 h-3.5" /><span>部分停用</span></>
                          ) : (
                            <><Ban className="w-3.5 h-3.5" /><span>全部停用</span></>
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-3.5 text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            disabled={busy || poolLoading}
                            onClick={() => void handlePoolToggleStatus(p)}
                            className={`px-2.5 py-1 rounded-lg font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors border ${
                              p.status === 'active'
                                ? 'bg-rose-50 text-rose-700 hover:bg-rose-100 border-rose-200'
                                : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border-emerald-200'
                            } disabled:opacity-50`}
                          >
                            {p.status === 'active' ? <><Ban className="w-3 h-3" /><span>全部停用</span></> : <><CheckCircle2 className="w-3 h-3" /><span>全部启用</span></>}
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setPoolDeleting(p)}
                            className="px-2.5 py-1 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                          >
                            <Trash2 className="w-3 h-3" />
                            <span>删除</span>
                          </button>
                        </div>
                      </td>
                     </tr>
                   );
                 })}
              </tbody>
            </table>
          </div>

          {/* 分页（每页 10 人，2026-11-06）：只有一页时不展示，避免多余控件 */}
          {poolTotalPages > 1 && (
            <div className="px-5 py-3 border-t border-slate-100 flex items-center justify-between text-xs">
              <span className="text-slate-500">
                共 <strong className="text-slate-800 tabular-nums">{filteredPoolItems.length}</strong> 人 · 每页 {POOL_PAGE_SIZE} 人
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={poolCurrentPage <= 1}
                  onClick={() => setPoolPage((prev) => Math.max(1, prev - 1))}
                  className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed transition-colors"
                >
                  上一页
                </button>
                <span className="text-slate-600 tabular-nums font-semibold">
                  {poolCurrentPage} / {poolTotalPages} 页
                </span>
                <button
                  type="button"
                  disabled={poolCurrentPage >= poolTotalPages}
                  onClick={() => setPoolPage((prev) => Math.min(poolTotalPages, prev + 1))}
                  className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed transition-colors"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 成员池：删除确认（全局删除所有名册行 + 条件清绑定） */}
      {poolDeleting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
            <div>
              <h3 className="font-bold text-slate-900 text-base">删除成员（当前办公软件）</h3>
              <p className="text-xs text-slate-600 mt-2 leading-relaxed">
                将删除该手机号在当前办公软件（{platform === 'wecom' ? '企业微信' : '量子密信'}）下
                <strong>全部管理员名下的 {poolDeleting.row_count} 条名册记录</strong>；
                若该手机号在该平台再无任何归属，其该平台的激活绑定也将一并清除（需重新登记）；
                <strong>其它平台的名册与激活不受影响</strong>。已产生的任务实例与提交记录不受影响。
              </p>
              <p className="text-xs text-rose-600 font-medium mt-2">此操作不可撤销，请确认。</p>
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setPoolDeleting(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-sm cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                disabled={poolBusyPhone === poolDeleting.phone}
                onClick={() => void handlePoolDelete()}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white font-semibold rounded-xl text-sm cursor-pointer disabled:opacity-60"
              >
                {poolBusyPhone === poolDeleting.phone ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 2026-11-11 任务二：组织架构选择器弹窗（面包屑下钻 + 部门/人员勾选 + 入册即激活） */}
      {orgPickerOpen && currentRole && (
        <OrgPickerModal
          open={orgPickerOpen}
          roleId={currentRole.role_id}
          roleName={currentRole.role_name}
          platform={platform}
          onClose={() => setOrgPickerOpen(false)}
          onImported={(summary) => {
            const extra = summary.skipped > 0 ? `，${summary.skipped} 人已在册跳过` : '';
            toast.success(
              `从组织架构加入 ${summary.added} 人，自动激活 ${summary.activated} 人${extra}。`,
              '组织架构导入完成',
            );
            void loadMembers(currentRole.role_id);
          }}
        />
      )}
    </div>
  );
};
