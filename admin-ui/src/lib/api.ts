import {
  AdminAuditItem,
  AdminHealth,
  FormFieldSpec,
  InstanceStats,
  InstanceStatus,
  ReminderLogItem,
  RoleItem,
  RoleMemberItem,
  SubmissionData,
  TaskInstanceDetail,
  TaskInstanceItem,
  TemplateItem,
  TemplateKind,
  TemplatePeriod,
  TemplateSpec,
  UnactivatedMemberItem,
  getTemplateRoleIds,
} from './types';
import { getCsrfToken as _getCsrf, realApi, setCsrfToken as _setCsrf } from './realApi';
import { humanizeMessage } from './toast';

export function setCsrfToken(token: string) {
  _setCsrf(token);
}

export function getCsrfToken(): string {
  return _getCsrf();
}

export function plainError(raw: string): string {
  return humanizeMessage(raw, '操作未成功，请稍后重试');
}

/* ================================================================== */
/* IMTask 内存态后端状态机（已清除全部假测试数据，接入市场部春促真实清单） */
/* ================================================================== */

const PHONE_RE = /^1[3-9]\d{9}$/;
const ROLE_CODE_RE = /^[a-zA-Z0-9_]{2,32}$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

function nowText(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function todayKey(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

interface InternalAdminState {
  username: string;
  password: string;
  must_change_password: boolean;
  loggedIn: boolean;
  health: AdminHealth;
  nextTemplateId: number;
  nextInstanceId: number;
  nextLogId: number;
  nextAuditId: number;
  templates: TemplateItem[];
  roles: Array<{
    role_id: string;
    role_name: string;
    description: string;
    created_at: string;
  }>;
  members: Record<string, RoleMemberItem[]>;
  unactivatedPool: UnactivatedMemberItem[];
  instances: TaskInstanceItem[];
  submissions: Record<number, SubmissionData>;
  reminders: ReminderLogItem[];
  audits: AdminAuditItem[];
}

const TODAY = todayKey();

const state: InternalAdminState = {
  username: 'admin',
  password: 'Admin1234',
  must_change_password: false,
  loggedIn: true,
  health: {
    ok: true,
    service: 'im-task-admin',
    channel: 'ready',
    event_crypto: 'ready',
    public_base_url: 'https://imtask.corp.example.com',
    is_loopback_public_url: false,
  },
  nextTemplateId: 6,
  nextInstanceId: 6,
  nextLogId: 6,
  nextAuditId: 4,
  roles: [
    {
      role_id: 'role_branch_chief',
      role_name: '片长/分支局长',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_marketing_director',
      role_name: '行销总监',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_grid_leader',
      role_name: '网格长',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_installer',
      role_name: '装维',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_smart_home_eng',
      role_name: '智家工程师',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_clerk',
      role_name: '营业员',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_biz_manager',
      role_name: '商客经理',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_gov_ent_manager',
      role_name: '政企经理',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_area_leader',
      role_name: '包区领导',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
  ],
  members: {
    role_branch_chief: [
      {
        phone: '13900000000',
        name: '张三',
        dept: '',
        status: 'active',
        activation: 'activated',
        usable: true,
        external_id: 'zdxlz_zhengjia_13900000000',
        activated_at: `${TODAY} 08:00:00`,
        created_at: `${TODAY} 08:00:00`,
      },
    ],
  },
  unactivatedPool: [
    {
      phone: '13880123456',
      name: '林建国',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_13880123456',
      activated_at: `${TODAY} 08:05:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:05:00`,
    },
    {
      phone: '13980654321',
      name: '周敏',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_13980654321',
      activated_at: `${TODAY} 08:06:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:06:00`,
    },
    {
      phone: '18980998877',
      name: '王海涛',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_18980998877',
      activated_at: `${TODAY} 08:08:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:08:00`,
    },
  ],
  templates: [
    {
      id: 1,
      name: '晨会安排',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '08:15',
      deadline: '',
      text_tpl: `一、文字提醒：
1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理
2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够
3、昨天卖得差的队伍，今天帮扶跟到人
二、数据提醒：
1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数
2、商机外呼及意向单到人数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_1_1',
            format: 'text',
            text_content: `一、文字提醒：
1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理
2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够
3、昨天卖得差的队伍，今天帮扶跟到人`,
          },
          {
            id: 'notice_1_2',
            format: 'file',
            file_name: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
            file_caption: `二、数据提醒：
1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数
2、商机外呼及意向单到人数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 2,
      name: '上午业务点检、午间复盘',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '12:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检
2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们
2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数
二、数据提醒：
1、根据农村、城市2号群晒图，统计该分支局上午上门次数
2、商机意向单及回数量，是否达成进度
3、该支局上午1+1155重点指标实时受理数据`,
      spec: {
        notice_items: [
          {
            id: 'notice_2_1',
            format: 'text',
            text_content: `一、文字提醒：
1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检
2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们
2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数`,
          },
          {
            id: 'notice_2_2',
            format: 'file',
            file_name: '1200_分支局上午上门次数与1+1155实时受理数据.png',
            file_caption: `二、数据提醒：
1、根据农村、城市2号群晒图，统计该分支局上午上门次数
2、商机意向单及回数量，是否达成进度
3、该支局上午1+1155重点指标实时受理数据`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 3,
      name: '上门协同',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '15:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑
2、没破零的队伍，跟着一起上门，边跑边纠偏
二、数据提醒：
商机意向单及回数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_3_1',
            format: 'text',
            text_content: `一、文字提醒：
1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑
2、没破零的队伍，跟着一起上门，边跑边纠偏`,
          },
          {
            id: 'notice_3_2',
            format: 'file',
            file_name: '1500_商机意向单及回单进度表.xlsx',
            file_caption: `二、数据提醒：
商机意向单及回数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 4,
      name: '夜间补差',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '18:00',
      deadline: '',
      text_tpl: `一、文字提醒：请关注当日业务发展，有差距的再夜间补齐
二、数据提醒：
1、该分支局上午1+1155重点指标实时受理数据`,
      spec: {
        notice_items: [
          {
            id: 'notice_4_1',
            format: 'text',
            text_content: `一、文字提醒：请关注当日业务发展，有差距的再夜间补齐`,
          },
          {
            id: 'notice_4_2',
            format: 'file',
            file_name: '1800_分支局1+1155重点指标实时受理数据.png',
            file_caption: `二、数据提醒：
1、该分支局上午1+1155重点指标实时受理数据`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 5,
      name: '下午业务点检、晚间复盘',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '20:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、盯次日预约数量、质量
2、总结今日得失、差距
3、梳理明日提升板块
4、明天要打哪里、谁需要你协同上门，今晚把时间约好
二、数据提醒：
1、该支局当日1+1155重点指标受理数据
2、根据农村、城市2号群晒图，统计该分支局全天上门次数
3、商机意向单及回数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_5_1',
            format: 'text',
            text_content: `一、文字提醒：
1、盯次日预约数量、质量
2、总结今日得失、差距
3、梳理明日提升板块
4、明天要打哪里、谁需要你协同上门，今晚把时间约好`,
          },
          {
            id: 'notice_5_2',
            format: 'file',
            file_name: '2000_当日1+1155受理数据及全天上门次数汇总.png',
            file_caption: `二、数据提醒：
1、该支局当日1+1155重点指标受理数据
2、根据农村、城市2号群晒图，统计该分支局全天上门次数
3、商机意向单及回数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
  ],
  instances: [
    {
      id: 1,
      template_id: 1,
      template_name: '晨会安排',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 08:15:02`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 2,
      template_id: 2,
      template_name: '上午业务点检、午间复盘',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 12:00:01`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 3,
      template_id: 3,
      template_name: '上门协同',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 15:00:02`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 4,
      template_id: 4,
      template_name: '夜间补差',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'pending',
      submitted_at: null,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 5,
      template_id: 5,
      template_name: '下午业务点检、晚间复盘',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'pending',
      submitted_at: null,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
  ],
  submissions: {
    1: {
      id: 1,
      instance_id: 1,
      kind: 'notice',
      form_values: {
        推送时间点: '08:15',
        事项名称: '晨会安排',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据图片）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理\n2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够\n3、昨天卖得差的队伍，今天帮扶跟到人\n二、数据提醒：\n1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数\n2、商机外呼及意向单到人数量，是否达成进度`,
      attachment_ref: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
      attachment_name: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
      submitted_at: `${TODAY} 08:15:02`,
    },
    2: {
      id: 2,
      instance_id: 2,
      kind: 'notice',
      form_values: {
        推送时间点: '12:00',
        事项名称: '上午业务点检、午间复盘',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据图片）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检\n2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们\n2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数\n二、数据提醒：\n1、根据农村、城市2号群晒图，统计该分支局上午上门次数\n2、商机意向单及回数量，是否达成进度\n3、该支局上午1+1155重点指标实时受理数据`,
      attachment_ref: '1200_分支局上午上门次数与1+1155实时受理数据.png',
      attachment_name: '1200_分支局上午上门次数与1+1155实时受理数据.png',
      submitted_at: `${TODAY} 12:00:01`,
    },
    3: {
      id: 3,
      instance_id: 3,
      kind: 'notice',
      form_values: {
        推送时间点: '15:00',
        事项名称: '上门协同',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据文件）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑\n2、没破零的队伍，跟着一起上门，边跑边纠偏\n二、数据提醒：\n商机意向单及回数量，是否达成进度`,
      attachment_ref: '1500_商机意向单及回单进度表.xlsx',
      attachment_name: '1500_商机意向单及回单进度表.xlsx',
      submitted_at: `${TODAY} 15:00:02`,
    },
  },
  reminders: [
    {
      id: 1,
      instance_id: 1,
      slot: '08:15',
      retry_no: 0,
      planned_at: `${TODAY} 08:15:00`,
      attempted_at: `${TODAY} 08:15:02`,
      sent_at: `${TODAY} 08:15:02`,
      result: 'sent',
      error: null,
      template_name: '晨会安排',
      phone: '13900000000',
      member_name: '张三',
    },
    {
      id: 2,
      instance_id: 2,
      slot: '12:00',
      retry_no: 0,
      planned_at: `${TODAY} 12:00:00`,
      attempted_at: `${TODAY} 12:00:01`,
      sent_at: `${TODAY} 12:00:01`,
      result: 'sent',
      error: null,
      template_name: '上午业务点检、午间复盘',
      phone: '13900000000',
      member_name: '张三',
    },
    {
      id: 3,
      instance_id: 3,
      slot: '15:00',
      retry_no: 0,
      planned_at: `${TODAY} 15:00:00`,
      attempted_at: `${TODAY} 15:00:02`,
      sent_at: `${TODAY} 15:00:02`,
      result: 'sent',
      error: null,
      template_name: '上门协同',
      phone: '13900000000',
      member_name: '张三',
    },
  ],
  audits: [
    {
      id: 1,
      at: `${TODAY} 08:00:00`,
      actor: 'admin',
      action: 'role.create',
      target: 'role:role_branch_chief',
      detail: '导入市场部《春促角色清单》：创建角色「片长/分支局长」(role_branch_chief)',
    },
    {
      id: 2,
      at: `${TODAY} 08:00:05`,
      actor: 'admin',
      action: 'member.add',
      target: 'role:role_branch_chief/13900000000',
      detail: '绑定片长/分支局长责任人：张三 (13900000000)',
    },
    {
      id: 3,
      at: `${TODAY} 08:00:10`,
      actor: 'admin',
      action: 'template.create',
      target: 'template:1~5',
      detail: '载入春促 5 个时点任务模板（08:15 晨会安排 / 12:00 午间复盘 / 15:00 上门协同 / 18:00 夜间补差 / 20:00 晚间复盘）',
    },
  ],
};

function appendAudit(action: string, target: string, detail: string) {
  state.audits.unshift({
    id: state.nextAuditId++,
    at: nowText(),
    actor: state.username,
    action,
    target,
    detail,
  });
}

function computeRoles(): RoleItem[] {
  return state.roles.map((r) => {
    const list = state.members[r.role_id] || [];
    const activeCount = list.filter((m) => m.status === 'active').length;
    const activatedCount = list.filter((m) => m.activation === 'activated').length;
    return {
      ...r,
      member_count: list.length,
      active_count: activeCount,
      activated_count: activatedCount,
    };
  });
}

function filterInstances(query: URLSearchParams): TaskInstanceItem[] {
  const status = (query.get('status') || '').trim();
  const kind = (query.get('kind') || '').trim();
  const templateId = (query.get('template_id') || '').trim();
  const roleId = (query.get('role_id') || '').trim();
  const phone = (query.get('phone') || '').trim();
  const periodKey = (query.get('period_key') || '').trim();

  return state.instances.filter((item) => {
    if (status && item.status !== status) return false;
    if (kind) {
      if (kind === 'notice' && item.kind !== 'notice') return false;
      if (kind === 'upload' && item.kind === 'notice') return false;
      if (kind !== 'notice' && kind !== 'upload' && item.kind !== kind) return false;
    }
    if (templateId && String(item.template_id) !== templateId) return false;
    if (roleId && item.role_id !== roleId) return false;
    if (phone && !item.phone.includes(phone) && !item.member_name_snap.includes(phone)) return false;
    if (periodKey && !item.period_key.includes(periodKey)) return false;
    return true;
  });
}

function computeStats(items: TaskInstanceItem[]): InstanceStats {
  const by_status: Record<InstanceStatus, number> = {
    pending: 0,
    sent: 0,
    submitted: 0,
    approved: 0,
    rejected: 0,
    overdue: 0,
    expired: 0,
  };
  for (const it of items) {
    by_status[it.status] = (by_status[it.status] || 0) + 1;
  }
  const total = items.length;
  const completed = by_status.submitted + by_status.approved;
  const completion_rate = total > 0 ? Number(((completed / total) * 100).toFixed(1)) : 0;
  const on_time_rate =
    total > 0 ? Number((((by_status.approved + by_status.submitted) / total) * 100).toFixed(1)) : 0;
  const overdue_rate =
    total > 0
      ? Number((((by_status.overdue + by_status.expired) / total) * 100).toFixed(1))
      : 0;

  return {
    ok: true,
    total,
    by_status,
    completion_rate,
    on_time_rate,
    overdue_rate,
  };
}

function validateRemindAt(
  kind: TemplateKind,
  period: TemplatePeriod,
  remindAt: string,
  deadline: string,
) {
  const r = remindAt.trim();
  const d = deadline.trim();
  if (!r) throw new Error('REMIND_AT_INVALID');

  if (kind === 'notice') {
    const slots = r.split(',').map((s) => s.trim()).filter(Boolean);
    if (slots.length === 0) throw new Error('REMIND_AT_INVALID');
    for (const s of slots) {
      if (!TIME_RE.test(s)) throw new Error('REMIND_AT_INVALID');
    }
    return;
  }

  if (!d) throw new Error('DEADLINE_INVALID');
  if (period === 'day') {
    if (!TIME_RE.test(r)) throw new Error('REMIND_AT_INVALID');
    if (!TIME_RE.test(d)) throw new Error('DEADLINE_INVALID');
    if (r >= d) throw new Error('DEADLINE_ORDER');
  }
}

function normalizeRoleIdsFromBody(body: any, fallbackRoleIds: string[] = []): string[] {
  if (Array.isArray(body?.role_ids)) {
    const rows: unknown[] = body.role_ids;
    const cleaned: string[] = Array.from(
      new Set(rows.map((r) => String(r || '').trim()).filter(Boolean)),
    );
    return cleaned;
  }
  if (typeof body?.role_ids === 'string' && body.role_ids.trim()) {
    return Array.from(
      new Set(
        body.role_ids
          .split(',')
          .map((r: string) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  if (typeof body?.role_id === 'string' && body.role_id.trim()) {
    return Array.from(
      new Set(
        body.role_id
          .split(',')
          .map((r: string) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  return fallbackRoleIds;
}

/* ================================================================== */
/* CSV 导出辅助（同时支持后端 /api/admin/instances/export 与前端即时下载） */
/* ================================================================== */

export function exportInstancesCsv(params: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {
      qs.set(k, String(v));
    }
  }
  const filtered = filterInstances(qs);
  const statusMap: Record<string, string> = {
    pending: '待推送/待提交',
    submitted: '待审核',
    approved: '已下发/已通过',
    rejected: '已驳回',
    overdue: '已逾期',
    expired: '已过期',
  };
  const header = [
    '实例ID',
    '模板ID',
    '事项名称',
    '任务类型',
    '周期标识',
    '角色编码',
    '成员姓名',
    '手机号',
    '所属部门',
    '当前状态',
    '下发/提交时间',
    '截止时间',
    '审核人',
    '审核时间',
    '驳回原因',
  ];
  const escapeCsv = (val: unknown) => {
    const s = val === null || val === undefined ? '' : String(val);
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines = [
    header.join(','),
    ...filtered.map((r) =>
      [
        r.id,
        r.template_id,
        r.template_name,
        r.kind,
        r.period_key,
        r.role_id,
        r.member_name_snap,
        r.phone,
        r.dept_snap || '',
        statusMap[r.status] || r.status,
        r.submitted_at || '',
        r.deadline_at || '',
        r.reviewed_by || '',
        r.reviewed_at || '',
        r.reject_reason || '',
      ]
        .map(escapeCsv)
        .join(','),
    ),
  ];
  const csvContent = '\uFEFF' + lines.join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `imtask_instances_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ================================================================== */
/* 统一请求路由处理                                                     */
/* ================================================================== */

async function handleImTaskRequest(method: string, rawPath: string, body?: any): Promise<any> {
  const urlObj = new URL(rawPath, 'http://localhost');
  const pathname = urlObj.pathname;
  const query = urlObj.searchParams;

  // 1. Health & Auth
  if (method === 'GET' && pathname === '/api/admin/health') {
    return { ...state.health };
  }

  if (method === 'POST' && pathname === '/api/admin/login') {
    const u = String(body?.username || '').trim();
    const p = String(body?.password || '');
    if (!u || !p) {
      throw new Error('INVALID_CREDENTIALS');
    }
    if (u !== state.username && u !== 'admin') {
      throw new Error('INVALID_CREDENTIALS');
    }
    if (p.length < 4) {
      throw new Error('INVALID_CREDENTIALS');
    }
    state.username = u;
    state.password = p;
    state.loggedIn = true;
    const token = `csrf-${Date.now()}`;
    setCsrfToken(token);
    appendAudit('login', u, '管理员登录后台');
    return {
      ok: true,
      username: state.username,
      must_change_password: state.must_change_password,
      csrf_token: token,
    };
  }

  if (method === 'POST' && pathname === '/api/admin/logout') {
    appendAudit('logout', state.username, '管理员退出登录');
    state.loggedIn = false;
    return { ok: true };
  }

  if (method === 'GET' && pathname === '/api/admin/me') {
    if (!state.loggedIn) {
      throw new Error('UNAUTHORIZED');
    }
    return {
      ok: true,
      username: state.username,
      must_change_password: state.must_change_password,
      csrf_token: getCsrfToken() || 'imtask-csrf-token',
    };
  }

  if (method === 'POST' && pathname === '/api/admin/change-password') {
    const oldPwd = String(body?.old_password || '');
    const newPwd = String(body?.new_password || '');
    if (!oldPwd) throw new Error('WRONG_PASSWORD');
    if (newPwd === oldPwd) throw new Error('PASSWORD_SAME');
    if (newPwd.length < 8 || !/[a-zA-Z]/.test(newPwd) || !/\d/.test(newPwd)) {
      throw new Error('WEAK_PASSWORD');
    }
    state.password = newPwd;
    state.must_change_password = false;
    appendAudit('change_password', state.username, '管理员修改了登录密码');
    return { ok: true };
  }

  if (method === 'POST' && pathname === '/api/admin/username') {
    const newName = String(body?.new_username || '').trim();
    if (newName.length < 2 || newName.length > 32) {
      throw new Error('账号名长度需在 2~32 位之间');
    }
    const oldName = state.username;
    state.username = newName;
    appendAudit('change_username', newName, `账号名由 ${oldName} 修改为 ${newName}`);
    return { ok: true, username: newName };
  }

  if (method === 'POST' && pathname === '/api/admin/callback-url') {
    if (!body?.password) {
      throw new Error('WRONG_PASSWORD');
    }
    return {
      ok: true,
      url: `${state.health.public_base_url}/api/events/zdxlz`,
      note: '请将该回调地址配置在量子密信开放平台「事件订阅」中，用于接收员工消息自动完成手机号与 external_id 激活绑定。',
    };
  }

  // 2. Templates (/api/admin/templates)
  if (method === 'GET' && pathname === '/api/admin/templates') {
    return {
      ok: true,
      items: [...state.templates].sort((a, b) => a.id - b.id),
    };
  }

  if (method === 'POST' && pathname === '/api/admin/templates') {
    const name = String(body?.name || '').trim();
    const kind = String(body?.kind || 'upload') as TemplateKind;
    const period = String(body?.period || 'day') as TemplatePeriod;
    const role_ids = normalizeRoleIdsFromBody(body);
    const role_id = role_ids[0] || '';
    const remind_at = String(body?.remind_at || body?.notice_times || '').trim();
    const deadline = kind === 'notice' ? '' : String(body?.deadline || '').trim();
    const text_tpl = String(body?.text_tpl || '').trim();
    const enabled = Boolean(body?.enabled ?? true);

    if (!name) throw new Error('NAME_REQUIRED');
    if (!['notice', 'upload', 'form', 'image', 'file'].includes(kind)) throw new Error('INVALID_KIND');
    if (!['day', 'week', 'month'].includes(period)) throw new Error('INVALID_PERIOD');
    if (kind === 'notice' && period === 'month') throw new Error('INVALID_PERIOD');
    if (role_ids.length === 0 || !role_ids.every((rid) => state.roles.some((r) => r.role_id === rid))) {
      throw new Error('ROLE_NOT_FOUND');
    }
    if (!text_tpl) throw new Error('TEXT_TPL_REQUIRED');
    if (enabled && state.health.is_loopback_public_url) throw new Error('LOOPBACK_PUBLIC_URL');

    validateRemindAt(kind, period, remind_at, deadline);

    const spec: TemplateSpec = {
      ...(body?.spec || {}),
      require_review: kind === 'notice' ? false : Boolean(body?.spec?.require_review ?? true),
    };

    const newItem: TemplateItem = {
      id: state.nextTemplateId++,
      name,
      kind,
      period,
      role_id,
      role_ids,
      remind_at,
      deadline,
      text_tpl,
      spec,
      enabled,
      created_at: nowText(),
      updated_at: nowText(),
    };
    state.templates.push(newItem);
    const roleNames = role_ids
      .map((rid) => state.roles.find((r) => r.role_id === rid)?.role_name || rid)
      .join('、');
    appendAudit(
      'template.create',
      `template:${newItem.id}`,
      `新建任务模板「${name}」(${kind}/${period}，目标角色：${roleNames})`,
    );
    return { ok: true, id: newItem.id, item: newItem };
  }

  // 在线测试：立即触发模板派单与推送测试 (/api/admin/templates/{id}/trigger)
  const tplTriggerMatch = pathname.match(/^\/api\/admin\/templates\/(\d+)\/trigger$/);
  if (method === 'POST' && tplTriggerMatch) {
    const id = Number(tplTriggerMatch[1]);
    const tpl = state.templates.find((t) => t.id === id);
    if (!tpl) throw new Error('TEMPLATE_NOT_FOUND');
    const tplRoleIds = getTemplateRoleIds(tpl);
    const roleMemberEntries: Array<{ roleId: string; member: RoleMemberItem }> = [];
    for (const rid of tplRoleIds) {
      const list = (state.members[rid] || []).filter((m) => m.status === 'active');
      for (const m of list) {
        roleMemberEntries.push({ roleId: rid, member: m });
      }
    }
    if (roleMemberEntries.length === 0) {
      throw new Error('所选角色下暂无启用中的成员，请先在「角色与成员名册」中添加成员');
    }

    const now = nowText();
    const pKey = todayKey();
    let pushedCount = 0;
    const noticeItems = Array.isArray(tpl.spec?.notice_items) ? tpl.spec.notice_items : [];
    const firstFileItem = noticeItems.find((ni) => ni.format === 'file' && ni.file_name);
    const needReview = tpl.kind === 'notice' ? false : tpl.spec?.require_review !== false;

    for (const { roleId: memberRoleId, member: mem } of roleMemberEntries) {
      let inst = state.instances.find(
        (it) =>
          it.template_id === tpl.id &&
          it.period_key === pKey &&
          it.phone === mem.phone &&
          it.role_id === memberRoleId,
      );
      const nextStatus: InstanceStatus = needReview ? 'submitted' : 'approved';
      if (!inst) {
        inst = {
          id: state.nextInstanceId++,
          template_id: tpl.id,
          template_name: tpl.name,
          kind: tpl.kind,
          period: tpl.period,
          period_key: pKey,
          role_id: memberRoleId,
          phone: mem.phone,
          member_name_snap: mem.name,
          dept_snap: '',
          require_review: needReview,
          status: nextStatus,
          submitted_at: now,
          reviewed_by: null,
          reviewed_at: null,
          reject_reason: null,
          deadline_at: tpl.kind === 'notice' || !tpl.deadline ? null : `${pKey} ${tpl.deadline}:00`,
          created_at: now,
        };
        state.instances.unshift(inst);
      } else {
        inst.require_review = needReview;
        inst.status = nextStatus;
        inst.submitted_at = now;
        inst.reviewed_by = null;
        inst.reviewed_at = null;
      }

      const roleObj = state.roles.find((r) => r.role_id === memberRoleId);
      const roleName = roleObj ? roleObj.role_name : memberRoleId;
      state.submissions[inst.id] = {
        id: inst.id,
        instance_id: inst.id,
        kind: tpl.kind,
        form_values: {
          推送时间点: tpl.remind_at,
          事项名称: tpl.name,
          接收人: `${mem.name} (${mem.phone}) · ${roleName}`,
          ...(noticeItems.length > 0
            ? {
                并列下发任务数: `${noticeItems.length} 条（${noticeItems
                  .map((ni, idx) => `任务${idx + 1}:${ni.format === 'file' ? '文件/图片' : '文本'}`)
                  .join(' + ')}）`,
              }
            : {}),
        },
        note: `【已向 ${mem.name}(${mem.phone}) 下发原文】\n${tpl.text_tpl}`,
        attachment_ref: firstFileItem?.file_name || null,
        attachment_name: firstFileItem?.file_name || null,
        attachment_size: firstFileItem?.file_size || null,
        submitted_at: now,
      };

      state.reminders.unshift({
        id: state.nextLogId++,
        instance_id: inst.id,
        slot: tpl.remind_at.split(',')[0].trim(),
        retry_no: 0,
        planned_at: `${pKey} ${tpl.remind_at.split(',')[0].trim()}:00`,
        attempted_at: now,
        sent_at: mem.activation === 'activated' && mem.usable !== false ? now : null,
        result:
          mem.activation !== 'activated'
            ? 'no_target'
            : mem.usable === false
              ? 'need_activate'
              : 'sent',
        error:
          mem.activation !== 'activated'
            ? 'NO_TARGET'
            : mem.usable === false
              ? 'NEED_ACTIVATE'
              : null,
        template_name: tpl.name,
        phone: mem.phone,
        member_name: mem.name,
      });

      pushedCount++;
    }

    appendAudit(
      'template.trigger',
      `template:${tpl.id}`,
      `手动触发「${tpl.name}」即时推送测试，共向 ${pushedCount} 名成员（${roleMemberEntries.map((e) => `${e.member.name}:${e.member.phone}`).join(', ')}）下发提醒`,
    );
    return {
      ok: true,
      pushed_count: pushedCount,
      dispatched_count: pushedCount,
      template_name: tpl.name,
      remind_at: tpl.remind_at,
      targets: roleMemberEntries.map((e) => `${e.member.name} (${e.member.phone})`),
    };
  }

  const tplMatch = pathname.match(/^\/api\/admin\/templates\/(\d+)$/);
  if (tplMatch) {
    const id = Number(tplMatch[1]);
    const idx = state.templates.findIndex((t) => t.id === id);
    if (idx === -1) throw new Error('TEMPLATE_NOT_FOUND');
    const existing = state.templates[idx];

    if (method === 'PUT') {
      if (body?.kind !== undefined && body.kind !== existing.kind) {
        throw new Error('PERIOD_IMMUTABLE');
      }
      if (body?.period !== undefined && body.period !== existing.period) {
        throw new Error('PERIOD_IMMUTABLE');
      }
      const name = body?.name !== undefined ? String(body.name).trim() : existing.name;
      const role_ids =
        body?.role_ids !== undefined || body?.role_id !== undefined
          ? normalizeRoleIdsFromBody(body)
          : getTemplateRoleIds(existing);
      const role_id = role_ids[0] || '';
      const remind_at =
        body?.remind_at !== undefined
          ? String(body.remind_at).trim()
          : body?.notice_times !== undefined
            ? String(body.notice_times).trim()
            : existing.remind_at;
      const deadline =
        existing.kind === 'notice'
          ? ''
          : body?.deadline !== undefined
            ? String(body.deadline).trim()
            : existing.deadline;
      const text_tpl =
        body?.text_tpl !== undefined ? String(body.text_tpl).trim() : existing.text_tpl;
      const enabled = body?.enabled !== undefined ? Boolean(body.enabled) : existing.enabled;
      const rawSpec = body?.spec !== undefined ? body.spec : existing.spec;
      const spec: TemplateSpec = {
        ...rawSpec,
        require_review:
          existing.kind === 'notice' ? false : Boolean(rawSpec?.require_review ?? true),
      };

      if (!name) throw new Error('NAME_REQUIRED');
      if (
        role_ids.length === 0 ||
        !role_ids.every((rid) => state.roles.some((r) => r.role_id === rid))
      ) {
        throw new Error('ROLE_NOT_FOUND');
      }
      if (!text_tpl) throw new Error('TEXT_TPL_REQUIRED');
      if (enabled && state.health.is_loopback_public_url) throw new Error('LOOPBACK_PUBLIC_URL');

      validateRemindAt(existing.kind, existing.period, remind_at, deadline);

      const updated: TemplateItem = {
        ...existing,
        name,
        role_id,
        role_ids,
        remind_at,
        deadline,
        text_tpl,
        spec,
        enabled,
        updated_at: nowText(),
      };
      state.templates[idx] = updated;
      for (const inst of state.instances) {
        if (inst.template_id === id) {
          inst.template_name = name;
          if (!role_ids.includes(inst.role_id)) {
            inst.role_id = role_id;
          }
        }
      }
      appendAudit(
        'template.update',
        `template:${id}`,
        `编辑模板「${name}」(提醒=${remind_at}, 角色数=${role_ids.length}, 启用=${enabled ? '是' : '否'})`,
      );
      return { ok: true, item: updated };
    }

    if (method === 'DELETE') {
      const hasInst = state.instances.some((inst) => inst.template_id === id);
      if (hasInst) {
        throw new Error('TEMPLATE_HAS_INSTANCES');
      }
      state.templates.splice(idx, 1);
      appendAudit('template.delete', `template:${id}`, `删除模板「${existing.name}」`);
      return { ok: true };
    }
  }

  // 3. Roles & Members (/api/admin/roles)
  if (method === 'GET' && pathname === '/api/admin/roles') {
    return {
      ok: true,
      items: computeRoles(),
    };
  }

  if (method === 'POST' && pathname === '/api/admin/roles') {
    const role_name = String(body?.role_name || '').trim();
    const description = String(body?.description || '').trim();
    if (!role_name) throw new Error('ROLE_NAME_REQUIRED');

    const rawCode = String(body?.role_id || '').trim();
    const role_id = rawCode || `role_${Date.now()}`;
    if (!ROLE_CODE_RE.test(role_id)) throw new Error('ROLE_CODE_INVALID');
    if (state.roles.some((r) => r.role_id === role_id)) throw new Error('ROLE_EXISTS');

    state.roles.push({
      role_id,
      role_name,
      description,
      created_at: nowText(),
    });
    state.members[role_id] = [];
    appendAudit('role.create', `role:${role_id}`, `创建角色「${role_name}」`);
    return { ok: true, role_id };
  }

  const roleMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)$/);
  if (roleMatch) {
    const roleId = decodeURIComponent(roleMatch[1]);
    const rIdx = state.roles.findIndex((r) => r.role_id === roleId);
    if (rIdx === -1) throw new Error('ROLE_NOT_FOUND');

    if (method === 'PUT') {
      const role_name = String(body?.role_name || '').trim();
      const description =
        body?.description !== undefined
          ? String(body.description).trim()
          : state.roles[rIdx].description;
      if (!role_name) throw new Error('ROLE_NAME_REQUIRED');
      state.roles[rIdx] = {
        ...state.roles[rIdx],
        role_name,
        description,
      };
      appendAudit('role.update', `role:${roleId}`, `更新角色名称为「${role_name}」`);
      return { ok: true };
    }

    if (method === 'DELETE') {
      if (state.templates.some((t) => getTemplateRoleIds(t).includes(roleId))) {
        throw new Error('ROLE_HAS_TEMPLATES');
      }
      const removed = state.roles[rIdx];
      state.roles.splice(rIdx, 1);
      delete state.members[roleId];
      appendAudit('role.delete', `role:${roleId}`, `删除角色「${removed.role_name}」`);
      return { ok: true };
    }
  }

  const membersMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members$/);
  if (membersMatch) {
    const roleId = decodeURIComponent(membersMatch[1]);
    if (!state.roles.some((r) => r.role_id === roleId)) throw new Error('ROLE_NOT_FOUND');
    const list = state.members[roleId] || [];

    if (method === 'GET') {
      return {
        ok: true,
        items: [...list],
      };
    }

    if (method === 'POST') {
      const phone = String(body?.phone || '').trim();
      const name = String(body?.name || '').trim();
      const dept = String(body?.dept || '').trim();
      if (!PHONE_RE.test(phone)) throw new Error('PHONE_INVALID');
      if (!name) throw new Error('MEMBER_NAME_REQUIRED');

      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      const pooledPerson = poolIdx >= 0 ? state.unactivatedPool[poolIdx] : undefined;
      if (poolIdx >= 0) {
        state.unactivatedPool.splice(poolIdx, 1);
      }

      const existingIdx = list.findIndex((m) => m.phone === phone);
      if (existingIdx >= 0) {
        list[existingIdx] = {
          ...list[existingIdx],
          name,
          dept,
          status: 'active',
        };
        appendAudit('member.upsert', `role:${roleId}/${phone}`, `更新成员「${name}」(${phone})`);
      } else {
        list.push({
          phone,
          name,
          dept,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: pooledPerson?.external_id || `zdxlz_${phone}`,
          activated_at: pooledPerson?.activated_at || nowText(),
          created_at: nowText(),
        });
        state.members[roleId] = list;
        appendAudit('member.add', `role:${roleId}/${phone}`, `添加成员「${name}」(${phone})`);
      }
      return { ok: true };
    }
  }

  const memberSingleMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members\/([^/]+)$/);
  if (memberSingleMatch) {
    const roleId = decodeURIComponent(memberSingleMatch[1]);
    const phone = decodeURIComponent(memberSingleMatch[2]);
    const roleObj = state.roles.find((r) => r.role_id === roleId);
    if (!roleObj) throw new Error('ROLE_NOT_FOUND');
    const list = state.members[roleId] || [];
    const mIdx = list.findIndex((m) => m.phone === phone);

    if (method === 'PUT' && phone !== 'import') {
      if (mIdx === -1) throw new Error('MEMBER_NOT_FOUND');
      const current = list[mIdx];
      const nextName = body?.name !== undefined ? String(body.name).trim() : current.name;
      const nextDept = body?.dept !== undefined ? String(body.dept).trim() : current.dept;
      const nextStatus =
        body?.status === 'disabled' || body?.status === 'active' ? body.status : current.status;

      if (!nextName) throw new Error('MEMBER_NAME_REQUIRED');

      list[mIdx] = {
        ...current,
        name: nextName,
        dept: nextDept,
        status: nextStatus,
      };
      appendAudit(
        'member.update',
        `role:${roleId}/${phone}`,
        `修改成员 ${phone}：姓名=${nextName}, 部门=${nextDept || '-'}, 状态=${nextStatus === 'active' ? '启用' : '停用'}`,
      );
      return { ok: true, item: list[mIdx] };
    }

    if (method === 'DELETE' && phone !== 'import') {
      if (mIdx === -1) throw new Error('MEMBER_NOT_FOUND');
      const removed = list[mIdx];
      list.splice(mIdx, 1);

      const now = nowText();
      const existPoolIdx = state.unactivatedPool.findIndex((u) => u.phone === removed.phone);
      const unassignedEntry: UnactivatedMemberItem = {
        phone: removed.phone,
        name: removed.name,
        activation: 'activated',
        usable: true,
        external_id: removed.external_id || `zdxlz_${removed.phone}`,
        activated_at: removed.activated_at || now,
        source_note: `由角色「${roleObj.role_name}」移出 · 密信会话保持绑定`,
        created_at: now,
      };
      if (existPoolIdx >= 0) {
        state.unactivatedPool[existPoolIdx] = unassignedEntry;
      } else {
        state.unactivatedPool.unshift(unassignedEntry);
      }

      appendAudit(
        'member.move_to_unassigned',
        `role:${roleId}/${phone}`,
        `将成员「${removed.name}」(${phone}) 从角色「${roleObj.role_name}」移出至「已激活待归纳角色人员」列表（保留密信会话绑定 ${unassignedEntry.external_id}）`,
      );
      return {
        ok: true,
        moved_to_unassigned: true,
        unassigned_item: unassignedEntry,
      };
    }
  }

  const importMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members\/import$/);
  if (method === 'POST' && importMatch) {
    const roleId = decodeURIComponent(importMatch[1]);
    if (!state.roles.some((r) => r.role_id === roleId)) throw new Error('ROLE_NOT_FOUND');
    const csvText = String(body?.csv_text || body?.csv || '').trim();
    if (!csvText) throw new Error('CSV_EMPTY');

    const lines = csvText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const list = state.members[roleId] || [];
    let added = 0;
    let updated = 0;
    const errors: Array<{ line: number; reason: string }> = [];

    lines.forEach((line, idx) => {
      const parts = line.split(',').map((s) => s.trim());
      const [phone = '', name = '', dept = ''] = parts;
      if (idx === 0 && (phone.includes('手机') || phone.toLowerCase() === 'phone')) {
        return;
      }
      if (!PHONE_RE.test(phone)) {
        errors.push({ line: idx + 1, reason: `手机号「${phone}」格式无效` });
        return;
      }
      if (!name) {
        errors.push({ line: idx + 1, reason: `手机号 ${phone} 缺少姓名` });
        return;
      }
      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      const pooledPerson = poolIdx >= 0 ? state.unactivatedPool[poolIdx] : undefined;
      if (poolIdx >= 0) {
        state.unactivatedPool.splice(poolIdx, 1);
      }
      const existIdx = list.findIndex((m) => m.phone === phone);
      if (existIdx >= 0) {
        list[existIdx] = { ...list[existIdx], name, dept, status: 'active' };
        updated++;
      } else {
        list.push({
          phone,
          name,
          dept,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: pooledPerson?.external_id || `zdxlz_${phone}`,
          activated_at: pooledPerson?.activated_at || nowText(),
          created_at: nowText(),
        });
        added++;
      }
    });

    state.members[roleId] = list;
    appendAudit(
      'member.import',
      `role:${roleId}`,
      `CSV 批量导入成员：新增 ${added} 人，更新 ${updated} 人，失败 ${errors.length} 行`,
    );
    return {
      ok: true,
      added,
      updated,
      skipped: errors.length,
      errors,
    };
  }

  // 3.5 Activated but Unassigned Role Personnel Pool (/api/admin/unactivated-members & /api/admin/unassigned-members)
  if (
    method === 'GET' &&
    (pathname === '/api/admin/unactivated-members' || pathname === '/api/admin/unassigned-members')
  ) {
    return {
      ok: true,
      items: [...state.unactivatedPool],
    };
  }

  if (
    method === 'POST' &&
    (pathname === '/api/admin/unactivated-members/move' ||
      pathname === '/api/admin/unassigned-members/move')
  ) {
    const targetRoleId = String(body?.target_role_id || '').trim();
    const roleObj = state.roles.find((r) => r.role_id === targetRoleId);
    if (!roleObj) throw new Error('ROLE_NOT_FOUND');

    const rawPhones: string[] = Array.isArray(body?.phones)
      ? body.phones.map((p: unknown) => String(p || '').trim()).filter(Boolean)
      : body?.phone
        ? [String(body.phone).trim()]
        : [];

    if (rawPhones.length === 0) {
      throw new Error('请选择需要归入角色分组的已激活人员');
    }

    const roleList = state.members[targetRoleId] || [];
    let movedCount = 0;
    const movedNames: string[] = [];

    for (const phone of rawPhones) {
      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      if (poolIdx === -1) continue;
      const person = state.unactivatedPool[poolIdx];
      state.unactivatedPool.splice(poolIdx, 1);

      const existIdx = roleList.findIndex((m) => m.phone === phone);
      if (existIdx >= 0) {
        roleList[existIdx] = {
          ...roleList[existIdx],
          name: person.name,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: roleList[existIdx].external_id || person.external_id || `zdxlz_${phone}`,
          activated_at: person.activated_at || nowText(),
        };
      } else {
        roleList.push({
          phone: person.phone,
          name: person.name,
          dept: '',
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: person.external_id || `zdxlz_${person.phone}`,
          activated_at: person.activated_at || nowText(),
          created_at: nowText(),
        });
      }
      movedCount++;
      movedNames.push(`${person.name}(${person.phone})`);
    }

    state.members[targetRoleId] = roleList;
    appendAudit(
      'member.assign_role',
      `role:${targetRoleId}`,
      `将 ${movedCount} 名已激活待归纳人员一键归入角色「${roleObj.role_name}」：${movedNames.join('、')}`,
    );

    return {
      ok: true,
      moved_count: movedCount,
      target_role_id: targetRoleId,
      target_role_name: roleObj.role_name,
      remaining: [...state.unactivatedPool],
    };
  }

  const unactivatedDelMatch = pathname.match(
    /^\/api\/admin\/(?:unactivated-members|unassigned-members)\/([^/]+)$/,
  );
  if (method === 'DELETE' && unactivatedDelMatch) {
    const phone = decodeURIComponent(unactivatedDelMatch[1]);
    const idx = state.unactivatedPool.findIndex((u) => u.phone === phone);
    if (idx === -1) throw new Error('MEMBER_NOT_FOUND');
    const removed = state.unactivatedPool[idx];
    state.unactivatedPool.splice(idx, 1);
    appendAudit(
      'unassigned_activated.unbind',
      `unassigned:${phone}`,
      `解除「${removed.name}」(${phone}) 的量子密信会话绑定（external_id: ${removed.external_id || `zdxlz_${phone}`}）并从待归纳列表移除；员工需在密信对话窗口重新输入发送激活指令方可再次绑定`,
    );
    return {
      ok: true,
      unbound_phone: phone,
      unbound_name: removed.name,
      unbound_external_id: removed.external_id || `zdxlz_${phone}`,
    };
  }

  // 4. Instances (/api/admin/instances)
  if (method === 'GET' && pathname === '/api/admin/instances/stats') {
    const filtered = filterInstances(query);
    return computeStats(filtered);
  }

  if (method === 'GET' && pathname === '/api/admin/instances') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const filtered = filterInstances(query);
    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  const instDetailMatch = pathname.match(/^\/api\/admin\/instances\/(\d+)$/);
  if (method === 'GET' && instDetailMatch) {
    const id = Number(instDetailMatch[1]);
    const inst = state.instances.find((it) => it.id === id);
    if (!inst) throw new Error('INSTANCE_NOT_FOUND');
    const tpl = state.templates.find((t) => t.id === inst.template_id);
    const roleObj = state.roles.find((r) => r.role_id === inst.role_id);
    const roleName = roleObj ? roleObj.role_name : inst.role_id;
    const submission =
      state.submissions[id] ||
      (tpl
        ? {
            id: id,
            instance_id: id,
            kind: inst.kind,
            form_values: {
              计划推送时间: tpl.remind_at,
              事项名称: tpl.name,
              接收人: `${inst.member_name_snap} (${inst.phone}) · ${roleName}`,
            },
            note: `【待推送提醒原文预览】\n${tpl.text_tpl}`,
            attachment_ref: null,
            attachment_name: null,
            submitted_at: inst.submitted_at || '待到点或手动触发推送',
          }
        : null);
    const reminders = state.reminders.filter((r) => r.instance_id === id);
    const result: TaskInstanceDetail = {
      ok: true,
      instance: inst,
      submission,
      reminders,
    };
    return result;
  }

  const instReviewMatch = pathname.match(/^\/api\/admin\/instances\/(\d+)\/review$/);
  if (method === 'POST' && instReviewMatch) {
    const id = Number(instReviewMatch[1]);
    const inst = state.instances.find((it) => it.id === id);
    if (!inst) throw new Error('INSTANCE_NOT_FOUND');
    if (inst.status !== 'submitted') {
      throw new Error('STATE_CONFLICT');
    }
    const action = String(body?.action || '').trim();
    const reason = String(body?.reason || '').trim();
    if (action !== 'approve' && action !== 'reject') {
      throw new Error('INVALID_ACTION');
    }
    if (action === 'reject' && reason.length < 2) {
      throw new Error('REASON_REQUIRED');
    }

    inst.status = action === 'approve' ? 'approved' : 'rejected';
    inst.reviewed_by = state.username;
    inst.reviewed_at = nowText();
    inst.reject_reason = action === 'reject' ? reason : null;

    appendAudit(
      action === 'approve' ? 'instance.approve' : 'instance.reject',
      `instance:${id}`,
      action === 'approve'
        ? `审核通过：${inst.member_name_snap} - ${inst.template_name} (${inst.period_key})`
        : `审核驳回：${inst.member_name_snap} - 原因：${reason}`,
    );
    return { ok: true, status: inst.status, instance: inst };
  }

  // 5. Logs (/api/admin/logs/reminders & /api/admin/logs/audit)
  if (method === 'GET' && pathname === '/api/admin/logs/reminders') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const resultFilter = (query.get('result') || '').trim();
    const instanceIdFilter = (query.get('instance_id') || '').trim();

    const filtered = state.reminders.filter((r) => {
      if (resultFilter && r.result !== resultFilter) return false;
      if (instanceIdFilter && String(r.instance_id) !== instanceIdFilter) return false;
      return true;
    });

    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  if (method === 'GET' && pathname === '/api/admin/logs/audit') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const actionFilter = (query.get('action') || '').trim();

    const filtered = state.audits.filter((a) => {
      if (actionFilter && !a.action.includes(actionFilter)) return false;
      return true;
    });

    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  throw new Error(`接口路径不存在: ${method} ${pathname}`);
}

export const api = {
  get: <T = any>(path: string): Promise<T> => realApi<T>('GET', path),
  post: <T = any>(path: string, body?: any): Promise<T> => realApi<T>('POST', path, body),
  put: <T = any>(path: string, body?: any): Promise<T> => realApi<T>('PUT', path, body),
  delete: <T = any>(path: string): Promise<T> => realApi<T>('DELETE', path),
};
