export type TemplateKind = 'notice' | 'upload' | 'form' | 'image' | 'file';
export type TemplatePeriod = 'day' | 'week' | 'month';

export interface FormFieldSpec {
  key: string;
  label: string;
  type: 'text' | 'number' | 'select';
  required: boolean;
  unit?: string;
  options?: string[];
}

export type NoticeContentFormat = 'text' | 'file';

export interface NoticeMessageItem {
  id: string;
  format: NoticeContentFormat; // 'text' 文本 | 'file' 文件（含图片）
  text_content?: string;
  file_name?: string;
  file_url?: string;
  file_mime?: string;
  file_size?: number;
  file_caption?: string;
  /** 已上传到服务端的图片相对路径（notice_attach/xxx.png），到点直接推送这张图 */
  image_path?: string;
}

// 统一上传任务的 H5 组件模块类型：字段（报数类型）、文本、图片、文件
export type UploadH5Module = 'fields' | 'text' | 'image' | 'file';

export interface TemplateSpec {
  // 纯通知提醒配置
  notice_items?: NoticeMessageItem[];

  // 统一上传任务配置（下发提醒文本 + 专属 H5 链接 + 上传完成反馈）
  upload_modules?: UploadH5Module[]; // 选择 H5 链接包含的上传项：字段(报数)、文本、图片、文件
  fields?: FormFieldSpec[]; // 报数字段配置
  text_label?: string; // 文本反馈标题
  text_placeholder?: string; // 文本反馈占位提示
  image_max_count?: number; // 图片上传最大张数
  file_allow_ext?: string[]; // 允许的文件后缀
  max_count?: number;
  max_size_mb?: number;
  allow_ext?: string[];
  feedback_message?: string; // 上传完成后的反馈结束语
  require_review?: boolean; // 仅上传任务可选 true(需审核) | false(无需审核)
}

export interface TemplateItem {
  id: number;
  name: string;
  kind: TemplateKind;
  period: TemplatePeriod;
  role_id: string;
  /** 支持关联多个业务角色（向后兼容单角色 role_id） */
  role_ids?: string[];
  remind_at: string;
  deadline: string;
  text_tpl: string;
  spec: TemplateSpec;
  enabled: boolean;
  /* 2026-10-29 P1-5：模板列表回显需要这两个字段，否则快捷启停/编辑会把配置写丢 */
  need_review?: boolean;
  attach_image_path?: string | null;
  /* 2026-10-29 H5 图文页：管理员填写的图片名称（描述），直链文案与 H5 页面标题用 */
  attach_image_name?: string | null;
  created_at: string;
  updated_at?: string;
}

export function getTemplateRoleIds(
  tpl: { role_id?: string; role_ids?: string[] } | null | undefined,
): string[] {
  if (!tpl) return [];
  if (Array.isArray(tpl.role_ids) && tpl.role_ids.length > 0) {
    return Array.from(new Set(tpl.role_ids.map((r) => String(r).trim()).filter(Boolean)));
  }
  if (typeof tpl.role_id === 'string' && tpl.role_id.trim()) {
    return Array.from(
      new Set(
        tpl.role_id
          .split(',')
          .map((r) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  return [];
}

export interface RoleItem {
  role_id: string;
  role_name: string;
  description: string;
  member_count: number;
  active_count: number;
  activated_count: number;
  created_at?: string;
}

export interface RoleMemberItem {
  phone: string;
  name: string;
  dept: string;
  status: 'active' | 'disabled';
  activation: 'activated' | 'pending';
  usable?: boolean;
  external_id?: string | null;
  activated_at?: string | null;
  created_at?: string;
}

export interface UnactivatedMemberItem {
  phone: string;
  name: string;
  activation: 'activated';
  usable?: boolean;
  external_id?: string;
  activated_at?: string;
  source_note?: string;
  created_at: string;
}


export type InstanceStatus =
  | 'pending'
  | 'sent'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'overdue'
  | 'expired';

export interface TaskInstanceItem {
  id: number;
  template_id: number;
  template_name: string;
  kind: TemplateKind;
  period: TemplatePeriod;
  period_key: string;
  role_id: string;
  phone: string;
  member_name_snap: string;
  dept_snap?: string;
  require_review?: boolean;
  status: InstanceStatus;
  submitted_at: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  deadline_at: string | null;
  /* 2026-10-29：模板的计划执行时间（通知类可能有多个时点），列表「执行时间」列要用 */
  template_remind_at?: string | null;
  template_notice_times?: string[] | string | null;
  /* 2026-10-29 逾期补交审核制：申请时间 / 审核状态 / 审核人 / 审核时间 */
  resubmit_req_at?: string | null;
  resubmit_status?: 'pending' | 'approved' | 'rejected' | null;
  resubmit_decided_by?: string | null;
  resubmit_decided_at?: string | null;
  created_at: string;
}

export interface SubmissionAttachment {
  ref: string;
  file_name: string;
  mime: string;
  size: number;
  is_image: boolean;
  url: string;
}

export interface SubmissionData {
  id?: number;
  instance_id?: number;
  kind?: TemplateKind;
  form_values?: Record<string, string | number>;
  /** 带中文标签的字段明细（后端按模板 schema 映射好） */
  fields?: Array<{ key: string; label: string; value: string }>;
  note?: string;
  attachments?: SubmissionAttachment[];
  attachment_ref?: string | null;
  attachment_name?: string | null;
  attachment_size?: number | null;
  submitted_at?: string;
  status?: string;
}

export interface ReminderLogItem {
  id: number;
  instance_id: number;
  slot: string;
  retry_no: number;
  planned_at: string;
  attempted_at: string | null;
  sent_at: string | null;
  result: 'sent' | 'need_activate' | 'failed' | 'no_target' | 'skipped';
  error: string | null;
  template_name?: string;
  phone?: string;
  member_name?: string;
}

export interface TaskInstanceDetail {
  ok: boolean;
  instance: TaskInstanceItem;
  submission: SubmissionData | null;
  reminders: ReminderLogItem[];
  /** 模板快照（详情页用来把字段 key 还原成中文标签） */
  template?: {
    name?: string;
    kind?: string;
    schema_json?: Array<{ key: string; label: string }>;
    [k: string]: unknown;
  } | null;
}

export interface InstanceStats {
  ok: boolean;
  total: number;
  by_status: Record<InstanceStatus, number>;
  completion_rate: number;
  on_time_rate: number;
  overdue_rate: number;
}

export interface AdminAuditItem {
  id: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
}

export interface AdminHealth {
  ok: boolean;
  service: string;
  channel: 'ready' | 'unconfigured';
  event_crypto: 'ready' | 'plaintext';
  public_base_url: string;
  is_loopback_public_url: boolean;
}

export function isUploadTaskKind(kind: TemplateKind): boolean {
  return kind !== 'notice';
}
