import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, Clock, Download, Eye, ImagePlus, Paperclip, Send, ShieldCheck, Users, X, XCircle } from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { ImageViewer } from '../../lib/ImageViewer';
import { useAppStore } from '../../lib/store';
import { humanizeReminderLogError, useToast } from '../../lib/toast';
import {
  NoticeView,
  RefinedInstanceStatus,
  SubmissionAttachment,
  TaskInstanceDetail,
  TaskInstanceItem,
} from '../../lib/types';

/* ══ 2026-11-23 执行状态细分（通知 6 项 / 上传 9 项）════════════════════
   列表徽章、详情徽章、下拉筛选三处共用这一份键值与文案；
   后端 admin.py 的 _REFINED_STATUS_SQL / refined_status 与此逐键对齐。 */
export interface StatusOption {
  value: RefinedInstanceStatus;
  label: string;
}

export const NOTICE_STATUS_OPTIONS: StatusOption[] = [
  { value: 'push_pending', label: '待推送' },
  { value: 'sent', label: '已送达' },
  { value: 'await_reply', label: '待回复' },
  { value: 'replied', label: '已回复' },
  { value: 'notice_expired', label: '未送达' },
  { value: 'cancelled', label: '已取消' },
];

export const UPLOAD_STATUS_OPTIONS: StatusOption[] = [
  { value: 'push_pending', label: '待推送' },
  { value: 'upload_pending', label: '待上传' },
  { value: 'reviewing', label: '已提交（待审核）' },
  { value: 'done', label: '已完成' },
  { value: 'done_admin', label: '已完成（管理员代交）' },
  { value: 'rejected', label: '已驳回（待重交）' },
  { value: 'overdue', label: '已逾期未交' },
  { value: 'expired', label: '已失效' },
  { value: 'cancelled', label: '已取消' },
];

/* 全部细分键 → 中文文案（KPI 卡片组合筛选的「筛选中」标签复用） */
export const REFINED_STATUS_LABELS: Record<string, string> = Object.fromEntries(
  [...NOTICE_STATUS_OPTIONS, ...UPLOAD_STATUS_OPTIONS].map((o) => [o.value, o.label]),
);

export function refineInstanceStatus(
  inst: Pick<TaskInstanceItem, 'kind' | 'status' | 'need_review' | 'submitted_at'> &
    Partial<Pick<TaskInstanceItem, 'refined_status' | 'published_at' | 'is_adhoc' | 'receipt_mode' | 'receipt_at' | 'latest_submission'>>,
): RefinedInstanceStatus {
  // 后端统一下发的细分键优先（列表/详情逐行由 _REFINED_STATUS_SQL 同源计算）
  if (inst.refined_status && REFINED_STATUS_META[inst.refined_status]) return inst.refined_status;
  const status = inst.status || 'pending';
  const isNotice = inst.kind === 'notice';
  const source = inst.latest_submission?.source ?? null;
  if (status === 'cancelled') return 'cancelled';
  if (isNotice) {
    if (status === 'pending') return 'push_pending';
    if (status === 'expired') return 'notice_expired';
    if (status === 'sent' || status === 'approved') {
      if (inst.receipt_at) return 'replied';
      if (status === 'sent' && inst.receipt_mode) return 'await_reply';
      return 'sent';
    }
    return 'sent'; // 历史脏行兜底：送达态之外不再细分
  }
  if (status === 'pending') {
    if (inst.is_adhoc) return 'upload_pending';
    /* published_at === null = 后端确认「提醒未送达」→ 待推送；
       undefined = 调用方没带该字段（统计面板行）→ 沿用旧口径「待上传」 */
    return inst.published_at ? 'upload_pending' : inst.published_at === null ? 'push_pending' : 'upload_pending';
  }
  if (status === 'submitted') return inst.need_review ? 'reviewing' : source === 'admin' ? 'done_admin' : 'done';
  if (status === 'approved') return source === 'admin' ? 'done_admin' : 'done';
  if (status === 'rejected') return 'rejected';
  if (status === 'overdue') return 'overdue';
  if (status === 'expired') return 'expired';
  return 'upload_pending';
}

const REFINED_STATUS_META: Record<RefinedInstanceStatus, { label: string; className: string; icon: React.ReactNode }> = {
  push_pending: {
    label: '待推送',
    className: 'bg-slate-100 text-slate-600 border-slate-200',
    icon: <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
  },
  upload_pending: {
    label: '待上传',
    className: 'bg-slate-100 text-slate-700 border-slate-300',
    icon: <Clock className="w-3.5 h-3.5 text-slate-500 shrink-0" />,
  },
  sent: {
    label: '已送达',
    className: 'bg-blue-50 text-blue-700 border-blue-200',
    icon: <CheckCircle2 className="w-3.5 h-3.5 text-blue-600 shrink-0" />,
  },
  await_reply: {
    label: '待回复',
    className: 'bg-amber-50 text-amber-700 border-amber-200',
    icon: <Clock className="w-3.5 h-3.5 text-amber-600 shrink-0" />,
  },
  replied: {
    label: '已回复',
    className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
  },
  notice_expired: {
    label: '未送达',
    className: 'bg-slate-100 text-slate-500 border-slate-200',
    icon: <AlertTriangle className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
  },
  reviewing: {
    label: '已提交（待审核）',
    className: 'bg-blue-50 text-blue-700 border-blue-200',
    icon: <Clock className="w-3.5 h-3.5 text-blue-600 shrink-0" />,
  },
  done: {
    label: '已完成',
    className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
  },
  done_admin: {
    label: '已完成（管理员代交）',
    className: 'bg-teal-50 text-teal-700 border-teal-200',
    icon: <Send className="w-3.5 h-3.5 text-teal-600 shrink-0" />,
  },
  rejected: {
    label: '已驳回（待重交）',
    className: 'bg-rose-50 text-rose-700 border-rose-200',
    icon: <XCircle className="w-3.5 h-3.5 text-rose-600 shrink-0" />,
  },
  overdue: {
    label: '已逾期未交',
    className: 'bg-amber-50 text-amber-800 border-amber-200',
    icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />,
  },
  expired: {
    label: '已失效',
    className: 'bg-slate-100 text-slate-500 border-slate-200',
    icon: <AlertTriangle className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
  },
  cancelled: {
    /* 2026-11-12 任务E5：成员移出分组后实例作废（cancelled），灰色「已取消」徽章；
       2026-11-23：通知类同样走细分表，不再误显示「待提醒」 */
    label: '已取消',
    className: 'bg-slate-100 text-slate-500 border-slate-200',
    icon: <XCircle className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
  },
};

export function getInstanceStatusMeta(inst: Parameters<typeof refineInstanceStatus>[0]): {
  label: string;
  className: string;
  icon: React.ReactNode;
} {
  return REFINED_STATUS_META[refineInstanceStatus(inst)];
}

const reminderKindLabels: Record<string, string> = {
  remind: '定时下发',
  manual_push: '手动推送',
  reject: '驳回通知',
  overdue: '逾期提醒',
  notice: '通知正文',
  notice_receipt: '通知回执提示',
  portal: '工作台任务下发',
  submit_ack: '提交成功回执',
  notice_img: '通知图片',
  notice_img_link: '通知图片链接',
  /* 2026-11-20：补齐通知附件的发送记录译名（原来这几种直接显示英文 kind） */
  notice_file: '通知文件',
  notice_file_link: '通知文件链接',
  notice_video: '通知视频',
  notice_vid_link: '通知视频链接',
  看板: '通知图片',
};

const reminderResultBadges: Record<string, { label: string; color: string }> = {
  sent: { label: '已送达', color: 'bg-emerald-50 text-emerald-700' },
  skipped: { label: '已跳过', color: 'bg-slate-100 text-slate-700' },
  no_target: { label: '未激活', color: 'bg-amber-50 text-amber-700' },
  need_activate: { label: '需重激活', color: 'bg-amber-50 text-amber-700' },
  missed: { label: '已过期跳过', color: 'bg-slate-100 text-slate-700' },
  failed: { label: '发送失败', color: 'bg-rose-50 text-rose-700' },
  /* 2026-11-23：补齐占位/熔断/限流三种结果徽标，界面不露英文 key */
  pending: { label: '发送中', color: 'bg-blue-50 text-blue-700' },
  circuit_open: { label: '通道熔断', color: 'bg-rose-50 text-rose-700' },
  rate_limited: { label: '触发限流', color: 'bg-amber-50 text-amber-700' },
};

/* 2026-10-29：执行时间统一在这里算——列表和详情共用一份，避免两处再走偏。
   规则：取模板计划时刻（通知类可能是多个）；已到达的加粗，未到达的置灰。 */
function parseNoticeTimes(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String).filter(Boolean);
  if (typeof v === 'string' && v.trim()) {
    try {
      const arr = JSON.parse(v);
      return Array.isArray(arr) ? arr.map(String).filter(Boolean) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function instancePlanSlots(inst: TaskInstanceItem): string[] {
  if (inst.kind === 'notice') {
    const list = parseNoticeTimes(inst.template_notice_times);
    if (list.length > 0) return list;
  }
  return inst.template_remind_at ? [inst.template_remind_at] : [];
}

function slotReached(inst: TaskInstanceItem, hhmm: string): boolean {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(inst.period_key || '');
  const t = /^(\d{1,2}):(\d{2})$/.exec(hhmm || '');
  if (!d || !t) return true; // 解析不出来就按「已到达」显示，不要误导
  const at = new Date(+d[1], +d[2] - 1, +d[3], +t[1], +t[2], 0, 0).getTime();
  return at <= Date.now();
}

export const InstancePlanTime: React.FC<{ inst: TaskInstanceItem; className?: string }> = ({ inst, className = '' }) => {
  if (inst.kind !== 'notice' && inst.submitted_at) return <span title="完成时间">{inst.submitted_at}</span>;
  if (inst.is_adhoc) return <span className="text-slate-400">—</span>;
  /* 2026-11-20：「立即推送」的实例没有计划时刻，执行时间改用 published_at
     （真实手动触发 / 送达时间）。批次键带 `#YYYYMMDDHHMMSS` 后缀的也是手动重推。 */
  const manual = !!inst.is_manual || /#\d{14}(?:-\d+)?$/.test(inst.period_key || '');
  if (manual) {
    return inst.published_at
      ? <span title="立即推送 · 实际送达时间">{inst.published_at}</span>
      : <span className="text-slate-400" title="尚未推送">待推送</span>;
  }
  if (inst.kind !== 'notice' && inst.published_at) return <span title="执行时间">{inst.published_at}</span>;
  const slots = instancePlanSlots(inst);
  if (slots.length === 0) {
    return inst.published_at
      ? <span title="执行时间">{inst.published_at}</span>
      : <span className="text-slate-400">—</span>;
  }
  const pk = inst.period_key || '';
  const datePart = /^\d{4}-\d{2}-\d{2}$/.test(pk) ? pk.slice(5) : pk;
  return (
    <span className={`inline-flex flex-wrap items-baseline gap-x-1.5 ${className}`}>
      {slots.map((s, i) => {
        const reached = slotReached(inst, s);
        return (
          <span
            key={`${s}-${i}`}
            className={reached ? 'font-bold text-slate-900' : 'font-normal text-slate-400'}
            title={reached ? '已到达执行时刻' : '尚未到达执行时刻'}
          >
            {datePart} {s}
            {i < slots.length - 1 ? ' /' : ''}
          </span>
        );
      })}
    </span>
  );
};

function ManualAttachmentPicker({ image, limit, files, onChange }: {
  image: boolean; limit: number; files: File[]; onChange: (files: File[]) => void;
}) {
  const [warning, setWarning] = useState('');
  const label = image ? '图片' : '文件';
  const Icon = image ? ImagePlus : Paperclip;
  return (
    <div className="space-y-1.5">
      <div className="font-medium text-slate-700">{label}凭证 <span className="font-normal text-slate-500">{files.length} / {limit}</span></div>
      <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-dashed border-blue-300 bg-white px-3 font-semibold text-blue-700 hover:bg-blue-50 focus-within:ring-2 focus-within:ring-blue-500">
        <Icon className="h-4 w-4" />{limit === 1 && files.length ? `更换${label}` : `选择${label}`}
        <input aria-label={`选择${label}`} className="sr-only" type="file" multiple={limit > 1}
          accept={image ? 'image/jpeg,image/png,image/webp,image/bmp,image/gif' : '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip'}
          onChange={(event) => {
            const incoming = Array.from(event.currentTarget.files || []);
            event.currentTarget.value = '';
            if (!incoming.length) return;
            const next = limit === 1 ? incoming : [...files, ...incoming];
            if (next.length > limit) {
              setWarning(`最多选择 ${limit} ${image ? '张图片' : '个文件'}`);
              return;
            }
            setWarning('');
            onChange(next);
          }} />
      </label>
      {warning && <p role="alert" className="text-rose-700">{warning}</p>}
      {files.map((file, index) => (
        <div key={`${file.name}/${index}`} className="flex items-center gap-2 rounded-lg bg-white px-2.5 py-1.5">
          <span className="min-w-0 flex-1 truncate" title={file.name}>{file.name}</span>
          <button type="button" aria-label={`移除 ${file.name}`} onClick={() => {
            setWarning('');
            onChange(files.filter((_, position) => position !== index));
          }} className="rounded p-1 text-slate-500 hover:bg-slate-100"><X className="h-3.5 w-3.5" /></button>
        </div>
      ))}
    </div>
  );
}

function ManualSubmissionForm({
  detail, busy, onCancel, onConfirm, editing = false,
}: {
  detail: TaskInstanceDetail;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (body: FormData) => void;
  editing?: boolean;
}) {
  const modules = detail.instance.modules ?? [];
  const [images, setImages] = useState<File[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const submission = editing ? detail.submission : undefined;
  const [retained, setRetained] = useState<SubmissionAttachment[]>(() => submission?.attachments || []);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => { formRef.current?.scrollIntoView({ block: 'nearest' }); }, []);
  const resultLabel = detail.instance.need_review ? '已提交' : '已完成';
  const fields = modules.includes('fields') ? detail.template?.schema_json ?? [] : [];
  const spec = detail.template?.spec_json;
  const inputClass = 'w-full rounded-lg border border-slate-200 px-2.5 py-2 text-xs outline-none focus:border-blue-500';

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const input = new FormData(event.currentTarget);
    const values = Object.fromEntries(fields.flatMap((field) => {
      const value = String(input.get(`field:${field.key}`) ?? '').trim();
      return value ? [[field.key, value]] : [];
    }));
    const body = new FormData();
    body.set('payload', JSON.stringify({
      fields: modules.includes('fields') ? values : undefined,
      note: modules.includes('text') ? String(input.get('note') ?? '').trim() : undefined,
      ...(editing ? { revision: submission?.revision, retained_refs: retained.map(item => item.ref) } : {}),
    }));
    for (const file of [...images, ...files]) body.append('attachments', file);
    onConfirm(body);
  };
  const inputValue = (key: string) => {
    const value = submission?.form_values?.[key];
    return value === true ? '是' : value === false ? '否' : String(value ?? '');
  };
  const attachmentPicker = (image: boolean, added: File[], setAdded: (files: File[]) => void) => {
    const existing = retained.filter(item => item.is_image === image);
    const limit = (image ? detail.instance.attachment_limits?.image : detail.instance.attachment_limits?.file) ?? 1;
    return <div className="space-y-2">
      {existing.map(item => <div key={item.ref} className="flex items-center gap-2 rounded-lg bg-white px-2.5 py-2">
        <a href={item.url} target="_blank" rel="noopener noreferrer" className="min-w-0 flex-1 truncate text-blue-700">{item.file_name}</a>
        <button type="button" onClick={() => setRetained(current => current.filter(row => row.ref !== item.ref))}
          aria-label={`移除 ${item.file_name}`} className="rounded p-1 text-slate-500 hover:bg-slate-100"><X className="h-3.5 w-3.5" /></button>
      </div>)}
      {(limit === 1 || existing.length < limit) && <ManualAttachmentPicker image={image} limit={Math.max(1, limit - existing.length)}
        files={added} onChange={next => {
          if (next.length && limit === 1) setRetained(current => current.filter(item => item.is_image !== image));
          setAdded(next);
        }} />}
    </div>;
  };

  return (
    <form ref={formRef} onSubmit={handleSubmit} aria-labelledby="manual-submit-title" className="rounded-xl border border-blue-200 bg-blue-50/40 p-4 space-y-3">
      <div>
        <h4 id="manual-submit-title" className="text-sm font-semibold text-slate-900">{editing ? '编辑提交内容' : `标记${resultLabel}`}</h4>
        <p className="mt-1 text-slate-600">{editing ? '保存后，员工提交页和统计将同步显示更正后的内容。' : '所有内容均可选，可直接确认。标记后，员工原提交链接将作废。'}</p>
      </div>
      <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <label key={field.key} className="grid gap-1 font-medium text-slate-700">
            <span>{field.label}{field.unit ? `（${field.unit}）` : ''}</span>
            {field.type === 'select' || field.type === 'boolean' ? (
              <select name={`field:${field.key}`} defaultValue={inputValue(field.key)} className={inputClass}>
                <option value="">请选择</option>
                {(field.type === 'boolean' ? ['是', '否'] : field.options)?.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            ) : (
              <input
                name={`field:${field.key}`}
                type={field.type === 'number' || field.type === 'date' ? field.type : 'text'}
                step={field.type === 'number' ? 'any' : undefined}
                defaultValue={inputValue(field.key)}
                maxLength={field.type === 'text' ? field.max_len : undefined}
                className={inputClass}
              />
            )}
          </label>
        ))}
        {modules.includes('text') && (
          <label className="grid gap-1 font-medium text-slate-700 sm:col-span-2">
            <span>{spec?.text_label || '文字说明'}</span>
            <textarea name="note" rows={2} maxLength={500} defaultValue={submission?.note} placeholder={spec?.text_placeholder} className={inputClass} />
          </label>
        )}
        {modules.includes('image') && (
          attachmentPicker(true, images, setImages)
        )}
        {modules.includes('file') && (
          attachmentPicker(false, files, setFiles)
        )}
      </fieldset>
      <div className="flex justify-end gap-2">
        <button type="button" disabled={busy} onClick={onCancel} className="rounded-lg px-3 py-2 font-semibold text-slate-600 hover:bg-white disabled:opacity-50">
          {editing ? '取消编辑' : '取消代交'}
        </button>
        <button type="submit" disabled={busy} className="rounded-lg bg-blue-600 px-3 py-2 font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
          {busy ? '正在保存…' : editing ? '保存修改' : `确认标记为${resultLabel}`}
        </button>
      </div>
    </form>
  );
}

/* 2026-11-23：通知类「实际下发内容与附件」卡片 —— 渲染该次下发的正文快照与
   图片（ImageViewer 缩略图）/ 视频（在线播放）/ 文件（下载），与 H5 阅读页同源。 */
function NoticeContentCard({ view, onPreview }: {
  view: NoticeView;
  onPreview: (url: string, alt?: string) => void;
}) {
  const usable = view.media.filter((m) => !!m.url);
  const images = usable.filter((m) => m.kind === 'image');
  const videos = usable.filter((m) => m.kind === 'video');
  const files = usable.filter((m) => m.kind !== 'image' && m.kind !== 'video');
  const missing = view.media.filter((m) => !m.url);
  return (
    <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3">
      {view.body && (
        <div>
          <div className="text-[11px] font-semibold text-slate-500 mb-1">通知正文</div>
          <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-800 leading-relaxed whitespace-pre-wrap break-words">
            {view.body}
          </div>
        </div>
      )}

      {images.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-slate-500 mb-1">图片（{images.length}）</div>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            {images.map((a) => (
              <button
                type="button"
                key={a.url}
                onClick={() => onPreview(a.url, a.name)}
                className="group rounded-xl border border-slate-200 overflow-hidden hover:border-blue-400 transition-colors bg-white cursor-pointer"
                title="点击在线查看大图"
              >
                <img src={a.url} alt={a.name} className="w-full h-24 object-cover group-hover:opacity-90" />
                <div className="px-2 py-1.5 border-t border-slate-100">
                  <div className="text-[10px] text-slate-500 truncate">{a.name}</div>
                  <div className="text-[10px] text-blue-600 flex items-center gap-1 mt-0.5">
                    <Eye className="w-3 h-3" />
                    <span>在线查看</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {videos.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-slate-500 mb-1">视频（{videos.length}）</div>
          <div className="space-y-2">
            {videos.map((a) => (
              <div key={a.url} className="rounded-xl border border-slate-200 overflow-hidden bg-white">
                <video controls preload="metadata" src={a.url} className="w-full max-h-72 bg-slate-900" />
                <div className="px-2.5 py-1.5 border-t border-slate-100 flex items-center justify-between gap-3">
                  <span className="text-[10px] text-slate-500 truncate">{a.name}</span>
                  <a href={`${a.url}?dl=1`} download={a.name}
                    className="px-2 py-0.5 rounded-lg bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-semibold hover:bg-blue-100 inline-flex items-center gap-1 shrink-0">
                    <Download className="w-3 h-3" />
                    <span>下载</span>
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {files.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-slate-500 mb-1">文件（{files.length}）</div>
          <div className="space-y-2">
            {files.map((a) => (
              <div key={a.url || a.name} className="p-2.5 rounded-xl border border-slate-200 bg-white flex items-center justify-between gap-3">
                <div className="min-w-0 flex items-center gap-2">
                  <Paperclip className="w-4 h-4 text-slate-400 shrink-0" />
                  <div className="min-w-0">
                    <div className="text-[12px] font-semibold text-slate-800 truncate">{a.name}</div>
                    <div className="text-[10px] text-slate-400">{a.size > 0 ? `${(a.size / 1024).toFixed(0)} KB` : ''}</div>
                  </div>
                </div>
                <a href={`${a.url}?dl=1`} download={a.name}
                  className="px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-semibold hover:bg-blue-100 inline-flex items-center gap-1 shrink-0">
                  <Download className="w-3 h-3" />
                  <span>下载</span>
                </a>
              </div>
            ))}
          </div>
        </div>
      )}

      {missing.length > 0 && (
        <div className="text-[11px] text-slate-400">
          {missing.map((a) => a.name).join('、')}：附件文件已清理或不可用，无法在线预览。
        </div>
      )}

      {view.published_at && (
        <div className="text-[11px] text-slate-400">下发时间：{view.published_at}</div>
      )}
    </div>
  );
}

export function useInstanceRoleLabel() {
  const { roles } = useAppStore();
  const roleMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of roles) {
      map[r.role_id] = r.role_name;
    }
    return map;
  }, [roles]);

  /* 2026-10-30 用户反馈：详情里角色一直显示成固定的「业务角色」。
     根因是详情接口没带 role_id（已补），同时这里的兜底也不该编一个假名字 ——
     多角色逐个查名，查不到就显示「—」，让人一眼看出是角色被删了而不是"叫业务角色"。
     2026-11-19 需求二：实例上固化的 role_name_snapshot **优先**（历史实例不随模板
     改绑角色漂移）；没有快照（老数据 / portal_task）才回退到按当前 role_ids 查名。 */
  return useCallback(
    (inst: { role_id?: string; role_ids?: string[]; role_name_snapshot?: string | null }) => {
      const snapshot = (inst.role_name_snapshot || '').trim();
      if (snapshot) return snapshot;
      const ids = (
        Array.isArray(inst.role_ids) && inst.role_ids.length
          ? inst.role_ids
          : [inst.role_id || '']
      ).filter(Boolean) as string[];
      const names = ids.map((id) => roleMap[id]).filter(Boolean);
      return names.length ? names.join('、') : '—';
    },
    [roleMap],
  );
}

export function InstanceDetailDialog({
  instanceId, submissionId, onClose, onChanged,
}: {
  instanceId: number;
  submissionId?: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { me, isSuperAdmin } = useAppStore();
  const toast = useToast();
  const roleLabel = useInstanceRoleLabel();
  const [detailData, setDetailData] = useState<TaskInstanceDetail | null>(null);
  const [error, setError] = useState('');
  const [manualSubmissionOpen, setManualSubmissionOpen] = useState(false);
  const [editSubmissionOpen, setEditSubmissionOpen] = useState(false);
  const [manualSubmissionBusy, setManualSubmissionBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [selectedSubmissionId, setSelectedSubmissionId] = useState(submissionId);
  /* 2026-11-20 需求 C：图片点开走原生全屏看图层 */
  const [viewerSource, setViewerSource] = useState<{ url: string; alt?: string } | null>(null);
  const path = `/api/admin/instances/${instanceId}`;
  const detailPath = selectedSubmissionId === undefined ? path : `${path}?submission_id=${selectedSubmissionId}`;

  useEffect(() => {
    let active = true;
    api.get<TaskInstanceDetail>(detailPath)
      .then((result) => { if (active) setDetailData(result); })
      .catch((err) => { if (active) setError(plainError(err.message)); });
    return () => { active = false; };
  }, [detailPath]);

  useEffect(() => {
    if (manualSubmissionOpen || editSubmissionOpen || manualSubmissionBusy || rejectOpen) return;
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (document.hidden || refreshing) return;
      refreshing = true;
      try {
        const result = await api.get<TaskInstanceDetail>(detailPath);
        if (active) setDetailData(result);
      } catch {
        // Keep the visible record during a transient refresh failure.
      } finally { refreshing = false; }
    };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, [detailPath, manualSubmissionOpen, editSubmissionOpen, manualSubmissionBusy, rejectOpen]);

  const canMarkSubmitted = !isSuperAdmin && me.role_level === 'sub_admin'
    && me.id != null && detailData?.instance.owner_id === me.id
    && detailData.instance.kind !== 'notice'
    && ['pending', 'overdue', 'rejected'].includes(detailData.instance.status);
  const latestRecord = detailData?.submissions?.at(-1);
  const submissionTimeline = detailData?.submission_timeline ?? [];
  const canEditSubmission = !isSuperAdmin && me.role_level === 'sub_admin'
    && me.id != null && detailData?.instance.owner_id === me.id
    && detailData.instance.kind !== 'notice' && ['submitted', 'approved'].includes(detailData.instance.status)
    && detailData.submission?.id === latestRecord?.id && Boolean(detailData.submission?.revision);
  const save = async (action: 'manual' | 'edit' | 'approve' | 'reject', body?: FormData) => {
    if (!detailData || manualSubmissionBusy) return;
    const reason = rejectReason.trim();
    if (action === 'reject' && reason.length < 2) {
      toast.warning('请填写至少 2 个字的驳回原因。');
      return;
    }
    setManualSubmissionBusy(true);
    try {
      if (action === 'manual') {
        const result = await api.post<{ status: 'submitted' | 'approved'; submitted_at: string }>(`${path}/mark-submitted`, body);
        setManualSubmissionOpen(false);
        setDetailData({
          ...detailData,
          instance: { ...detailData.instance, status: result.status, submitted_at: result.submitted_at, reject_reason: null },
        });
      } else if (action === 'edit') {
        await api.put(`${path}/submission`, body);
        setEditSubmissionOpen(false);
      } else {
        await api.post(`${path}/review`, { action, reason });
      }
      const messages = {
        manual: detailData.instance.need_review ? '任务已提交，等待审核。' : '任务已标记为已完成。',
        edit: '提交内容已更新。',
        approve: '审核已通过。', reject: '任务已驳回，系统将通知员工重交。',
      };
      toast.success(messages[action]);
      onChanged();
      if (action === 'reject') {
        onClose();
        return;
      }
      if (action === 'manual' && selectedSubmissionId !== undefined) {
        setSelectedSubmissionId(undefined);
        return;
      }
      try {
        setDetailData(await api.get<TaskInstanceDetail>(detailPath));
      } catch {
        toast.warning('操作已保存，请重新打开详情查看最新记录。');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '操作失败');
    } finally {
      setManualSubmissionBusy(false);
    }
  };

  if (!detailData) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-xs">
        <div role="dialog" aria-modal="true" aria-label="任务实例详情" className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-slate-900">任务实例详情</h3>
            <button type="button" aria-label="关闭详情" onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"><X className="h-5 w-5" /></button>
          </div>
          <p role={error ? 'alert' : 'status'} className={`mt-4 text-sm ${error ? 'text-rose-700' : 'text-slate-500'}`}>{error || '正在加载任务详情…'}</p>
        </div>
      </div>
    );
  }
  if (rejectOpen) {
    const rejectTarget = detailData.instance;
    return (

        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div role="dialog" aria-modal="true" aria-label="驳回上传任务" className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">驳回上传任务</h3>
                <p className="text-[11px] text-slate-400">
                  实例 #{rejectTarget.id} · {rejectTarget.member_name_snap} ({rejectTarget.phone})
                </p>
              </div>
              <button
                disabled={manualSubmissionBusy} onClick={() => setRejectOpen(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={(event) => { event.preventDefault(); void save('reject'); }} className="space-y-3.5 text-xs">
              <div className="p-3 rounded-xl bg-rose-50/70 border border-rose-200/80 text-rose-800 leading-relaxed">
                驳回后，该任务状态将变更为「已驳回」，员工打开原 H5 链接可看到您填写的驳回原因并重新上传提交。
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1.5">
                  驳回原因说明 <span className="text-rose-500">*（必填，至少 2 个字）</span>
                </label>
                <textarea
                  rows={4}
                  autoFocus
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="请具体说明不通过的原因及重交要求…"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-slate-800 outline-none focus:bg-white focus:border-rose-500 leading-relaxed"
                />
              </div>

              <div className="pt-1 flex justify-end gap-2">
                <button
                  type="button"
                  disabled={manualSubmissionBusy} onClick={() => setRejectOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={manualSubmissionBusy || !rejectReason.trim()}
                  className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-50"
                >
                  {manualSubmissionBusy ? '正在提交…' : '确认驳回'}
                </button>
              </div>
            </form>
          </div>
        </div>
          );
  }
  return (
    <>
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4 overflow-y-auto">
          <div role="dialog" aria-modal="true" aria-label="任务实例详情" className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-slate-200 my-8 space-y-5 max-h-[90vh] overflow-y-auto text-xs">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-slate-400">#{detailData.instance.id}</span>
                  <h3 className="font-bold text-slate-900 text-base">
                    {detailData.instance.template_name}
                  </h3>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${
                      getInstanceStatusMeta(detailData.instance).className
                    }`}
                  >
                    {getInstanceStatusMeta(detailData.instance).label}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  {detailData.instance.is_adhoc ? '自主上报 · ' : ''}接收人：
                  {detailData.instance.member_name_snap} ({detailData.instance.phone}) ·{' '}
                  {roleLabel(detailData.instance)}
                </p>
              </div>
              <button
                onClick={() => onClose()}
                disabled={manualSubmissionBusy}
                aria-label="关闭详情"
                className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 disabled:opacity-50"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 1. 基础信息卡片（接收人带上角色）
                2026-11-23 顶部时间字段统一：
                · 推送类（定时/立即上传、定时/立即通知、回执通知）→「送达时间」（未推送显示待推送）
                  +「提交时间 / 回执确认时间」（未完成/未回复显示 —）+ 有截止则展示「截止时间」；
                · 自主上报（无推送环节）→ 不显示送达时间，仅展示「提交时间」。 */}
            {(() => {
              const inst = detailData.instance;
              const isNotice = inst.kind === 'notice';
              const isAdhoc = !!inst.is_adhoc;
              const deadlineRaw = String(inst.deadline_at || '');
              const hasDeadline = !isNotice && !isAdhoc && !!deadlineRaw && !deadlineRaw.startsWith('9999-12-31');
              const confirmAt = isNotice ? (inst.receipt_at || null) : (inst.submitted_at || null);
              const gridCols = isAdhoc
                ? 'sm:grid-cols-3'
                : isNotice ? 'sm:grid-cols-2 lg:grid-cols-4' : 'sm:grid-cols-3 lg:grid-cols-5';
              return (
                <div className={`grid grid-cols-2 ${gridCols} gap-3 bg-slate-50 p-3.5 rounded-xl border border-slate-200/70`}>
              <div>
                <div className="text-[11px] text-slate-400">接收人（含角色）</div>
                <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                  <span className="font-bold text-slate-800">
                    {detailData.instance.member_name_snap}
                  </span>
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/70 text-[11px] font-semibold">
                    <Users className="w-3 h-3 shrink-0" />
                    <span>{roleLabel(detailData.instance)}</span>
                  </span>
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-400">手机号码</div>
                <div className="font-mono font-semibold text-slate-800 mt-0.5">
                  {detailData.instance.phone}
                </div>
              </div>
              {!isAdhoc && <div>
                <div className="text-[11px] text-slate-400">送达时间</div>
                <div className="font-mono text-slate-700 mt-0.5">
                  {detailData.instance.published_at
                    ? <span title="提醒实际送达时间">{detailData.instance.published_at}</span>
                    : <span className="text-slate-400" title="尚未推送">待推送</span>}
                </div>
              </div>}
              <div>
                <div className="text-[11px] text-slate-400">{isNotice ? '回执确认时间' : '提交时间'}</div>
                <div className="font-mono text-slate-700 mt-0.5">
                  {confirmAt
                    ? <span title={isNotice ? '成员完成回执确认的时间' : '成员提交任务的时间'}>{confirmAt}</span>
                    : <span className="text-slate-400">{isNotice ? '未回复' : '未完成'}</span>}
                </div>
              </div>
              {hasDeadline && (
                <div>
                  <div className="text-[11px] text-slate-400">截止时间</div>
                  <div className="font-mono text-slate-700 mt-0.5">
                    {/* 2026-11-13：具体时间戳统一缩写为「11月30日 17:00」样式（三端口径） */}
                    {(() => {
                      const m2 = deadlineRaw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
                      if (!m2) return deadlineRaw || '—';
                      return `${Number(m2[2])}月${Number(m2[3])}日 ${m2[4]}:${m2[5]}`;
                    })()}
                  </div>
                </div>
              )}
                </div>
              );
            })()}

            {detailData.instance.reject_reason && (
              <div className="p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-800">
                <div className="font-bold mb-1">最近一次驳回原因：</div>
                <div>{detailData.instance.reject_reason}</div>
              </div>
            )}

            {/* 2026-11-12 任务E3：逾期补交申请记录区块已随「申请补交」链路下线删除 */}

            {/* 2. 提交内容与凭证 / 通知下发内容与附件（2026-11-23 通知类真实渲染该次下发快照） */}
            <div className="space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                  <Paperclip className="w-3.5 h-3.5 text-blue-600" />
                  <span>{detailData.instance.kind === 'notice' ? '通知下发内容与附件' : '提交内容与凭证'}</span>
                </h4>
                {canMarkSubmitted && !manualSubmissionOpen && (
                  <button type="button" onClick={() => setManualSubmissionOpen(true)}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-blue-50 px-3 py-2 font-semibold text-blue-700 hover:bg-blue-100">
                    <Check className="h-3.5 w-3.5" /> 标记{detailData.instance.need_review ? '已提交' : '已完成'}
                  </button>
                )}
                {canEditSubmission && !editSubmissionOpen && <button type="button" onClick={() => setEditSubmissionOpen(true)}
                  className="rounded-xl bg-blue-50 px-3 py-2 font-semibold text-blue-700 hover:bg-blue-100">编辑提交内容</button>}
              </div>

              {detailData.instance.kind === 'notice' ? (
                detailData.notice_view && (detailData.notice_view.body || detailData.notice_view.media.length > 0) ? (
                  <NoticeContentCard view={detailData.notice_view} onPreview={(url, alt) => setViewerSource({ url, alt })} />
                ) : (
                  <div className="p-6 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                    该通知尚未配置下发正文或附件。
                  </div>
                )
              ) : !detailData.submission ? (
                <div className="p-6 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                  该任务实例尚未产生提交记录。
                </div>
              ) : (
                <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3">
                  {detailData.submission.source === 'admin' && (
                    <span className="inline-flex rounded-md bg-blue-50 px-2 py-1 font-semibold text-blue-700">管理员代交</span>
                  )}
                  {(() => {
                    const sub = detailData.submission!;
                    const fieldRows =
                      sub.fields && sub.fields.length > 0
                        ? sub.fields
                        : Object.entries(sub.form_values || {}).map(([k, v]) => ({
                            key: k,
                            label: k,
                            value: String(v ?? ''),
                          }));
                    const attachments = sub.attachments || [];
                    /* 2026-10-30：只渲染模板**实际启用**的模块 —— 管理员只勾了字段+图片，
                       就不该出现「文件（0） 本次未上传文件」这种空区块。
                       例外：真有数据就照常显示（历史模板/脏数据不隐藏证据）。 */
                    const mods: string[] = Array.isArray(detailData.instance.modules)
                      ? detailData.instance.modules
                      : [];
                    const hasModule = (m: string, hasData = false) =>
                      hasData || mods.length === 0 || mods.indexOf(m) >= 0;
                    return (
                      <>
                        {fieldRows.length > 0 && hasModule('fields', true) && (
                          <div className="space-y-1.5">
                            <div className="text-[11px] font-semibold text-slate-500">报数字段</div>
                            <div className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
                              {fieldRows.map((f) => (
                                <div
                                  key={f.key}
                                  className="px-3.5 py-2.5 bg-slate-50/50 flex items-center justify-between gap-4"
                                >
                                  <span className="text-slate-500">{f.label}</span>
                                  <span className="font-semibold text-slate-900 text-right font-mono">
                                    {f.value}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {sub.note && (
                          <div>
                            <div className="text-[11px] font-semibold text-slate-500 mb-1">文字情况说明</div>
                            <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-800 leading-relaxed whitespace-pre-wrap break-words">
                              {sub.note}
                            </div>
                          </div>
                        )}

                        {/* 2026-10-28：图片与文件分两行单独展示；图片可点开在线查看，文件只给「下载」 */}
                        {(() => {
                          const imgs = attachments.filter((a) => a.is_image);
                          const files = attachments.filter((a) => !a.is_image);
                          return (
                            <>
                              {hasModule('image', imgs.length > 0) && (
                              <div>
                                <div className="text-[11px] font-semibold text-slate-500 mb-1">
                                  图片（{imgs.length}）
                                </div>
                                {imgs.length === 0 ? (
                                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-400 text-center">
                                    本次未上传图片。
                                  </div>
                                ) : (
                                  <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                                    {imgs.map((a) => (
                                      <button
                                        type="button"
                                        key={a.ref}
                                        onClick={() => setViewerSource({ url: a.url, alt: a.file_name })}
                                        className="group rounded-xl border border-slate-200 overflow-hidden hover:border-blue-400 transition-colors bg-white cursor-pointer"
                                        title="点击在线查看大图"
                                      >
                                        <img
                                          src={a.url}
                                          alt={a.file_name}
                                          className="w-full h-24 object-cover group-hover:opacity-90"
                                        />
                                        <div className="px-2 py-1.5 border-t border-slate-100">
                                          <div className="text-[10px] text-slate-500 truncate">{a.file_name}</div>
                                          <div className="text-[10px] text-blue-600 flex items-center gap-1 mt-0.5">
                                            <Eye className="w-3 h-3" />
                                            <span>在线查看</span>
                                          </div>
                                        </div>
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                              )}

                              {hasModule('file', files.length > 0) && (
                              <div>
                                <div className="text-[11px] font-semibold text-slate-500 mb-1">
                                  文件（{files.length}）
                                </div>
                                {files.length === 0 ? (
                                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-400 text-center">
                                    本次未上传文件。
                                  </div>
                                ) : (
                                  <div className="space-y-2">
                                    {files.map((a) => (
                                      <div
                                        key={a.ref}
                                        className="p-2.5 rounded-xl border border-slate-200 bg-white flex items-center justify-between gap-3"
                                      >
                                        <div className="min-w-0 flex items-center gap-2">
                                          <Paperclip className="w-4 h-4 text-slate-400 shrink-0" />
                                          <div className="min-w-0">
                                            <div className="text-[12px] font-semibold text-slate-800 truncate">
                                              {a.file_name}
                                            </div>
                                            <div className="text-[10px] text-slate-400">
                                              {a.size > 0 ? `${(a.size / 1024).toFixed(0)} KB` : ''}
                                            </div>
                                          </div>
                                        </div>
                                        <div className="flex items-center gap-1.5 shrink-0">
                                          {/* 2026-10-28：docx/xlsx 等浏览器无法内联渲染，点「在线查看」也只会变下载，
                                              按用户口径统一去掉该按钮，只留下载。图片仍可点缩略图在线查看。 */}
                                          <a
                                            href={`${a.url}?dl=1`}
                                            download={a.file_name}
                                            className="px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-semibold hover:bg-blue-100 inline-flex items-center gap-1"
                                          >
                                            <Download className="w-3 h-3" />
                                            <span>下载</span>
                                          </a>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                              )}
                            </>
                          );
                        })()}

                        {sub.submitted_at && (
                          <div className="text-[11px] text-slate-400">提交时间：{sub.submitted_at}</div>
                        )}
                      </>
                    );
                  })()}
                </div>
              )}
            </div>

            {/* 2026-11-23：通知开启回执 → 附「成员回执记录」（状态 / 确认时间 / 内容与方式） */}
            {detailData.instance.kind === 'notice' && detailData.instance.receipt_mode && (
              <section className="space-y-2.5">
                <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-blue-600" />
                  <span>成员回执记录</span>
                </h4>
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold">
                        <th className="py-2 px-3">回执状态</th>
                        <th className="py-2 px-3">回执确认时间</th>
                        <th className="py-2 px-3">回执内容 / 方式</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      <tr>
                        <td className="py-2.5 px-3">
                          {detailData.instance.receipt_at ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-lg border text-[11px] font-semibold bg-emerald-50 text-emerald-700 border-emerald-200">
                              <CheckCircle2 className="w-3 h-3" />已回复
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-lg border text-[11px] font-semibold bg-amber-50 text-amber-700 border-amber-200">
                              <Clock className="w-3 h-3" />待回复
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 font-mono text-slate-600">
                          {detailData.instance.receipt_at || '—'}
                        </td>
                        <td className="py-2.5 px-3 text-slate-600">
                          {detailData.instance.receipt_mode === 'link'
                            ? `在 H5 通知确认页点击「${detailData.instance.receipt_phrase || '我已知晓'}」完成确认`
                            : `在聊天框回复短语「${detailData.instance.receipt_phrase || '我已知晓'}」完成确认`}
                          {detailData.instance.receipt_text && (
                            <span className="block text-slate-400 mt-0.5">回复内容：{detailData.instance.receipt_text}</span>
                          )}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {canMarkSubmitted && manualSubmissionOpen && (
              <ManualSubmissionForm detail={detailData} busy={manualSubmissionBusy}
                onCancel={() => setManualSubmissionOpen(false)} onConfirm={(body) => void save('manual', body)} />
            )}
            {canEditSubmission && editSubmissionOpen && (
              <ManualSubmissionForm detail={detailData} editing busy={manualSubmissionBusy}
                onCancel={() => setEditSubmissionOpen(false)} onConfirm={(body) => void save('edit', body)} />
            )}

            {detailData.instance.kind !== 'notice' && (
              <section className="space-y-2.5">
                <h4 className="font-bold text-slate-900">任务提交时间（{submissionTimeline.length}）</h4>
                {submissionTimeline.length ? <div className="overflow-x-auto rounded-xl border border-slate-200">
                  <table className="w-full whitespace-nowrap text-left text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                      <tr><th className="px-3 py-2">操作时间</th><th className="px-3 py-2">操作类型</th><th className="px-3 py-2">记录</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {submissionTimeline.map((record) => {
                        const current = record.submission_id === detailData.submission?.id;
                        const labels = { user_submit: '员工自交', admin_submit: '管理员代交', user_edit: '员工修改', admin_edit: '管理员修改' };
                        return (
                          <tr key={record.id}>
                            <td className="px-3 py-2 tabular-nums text-slate-600">{record.at}</td>
                            <td className="px-3 py-2">{labels[record.kind]}</td>
                            <td className="px-3 py-2">{record.status === null ? <span className="text-slate-500">内容已更新</span>
                              : current ? <span className="text-slate-500">正在查看</span>
                              : <button type="button" disabled={manualSubmissionBusy || manualSubmissionOpen || editSubmissionOpen}
                                onClick={() => setSelectedSubmissionId(record.submission_id)} className="font-semibold text-blue-700 hover:underline disabled:opacity-50">查看</button>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div> : <p className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-slate-500">暂无提交记录。</p>}
              </section>
            )}

            {/* 3. 消息发送记录（2026-11-23：通知与上传统一展示，口径同源汉化） */}
            <div className="space-y-2.5">
              <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                <Send className="w-3.5 h-3.5 text-blue-600" />
                <span>办公软件消息发送记录 ({detailData.reminders.length})</span>
              </h4>

              {detailData.reminders.length === 0 ? (
                <div className="p-4 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                  暂无提醒发送记录。
                </div>
              ) : (
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold">
                        <th className="py-2 px-3">触发类型 / 重试</th>
                        <th className="py-2 px-3">发送时刻</th>
                        <th className="py-2 px-3">发送结果</th>
                        <th className="py-2 px-3">状态说明</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {detailData.reminders.map((r) => {
                        const kind = r.kind || r.slot;
                        const badge = reminderResultBadges[r.result] || reminderResultBadges.skipped;
                        return (
                          <tr key={r.id}>
                            <td className="py-2.5 px-3">
                              <span className="font-semibold text-slate-800">{reminderKindLabels[kind] || kind || '—'}</span>
                              {r.retry_no > 0 && (
                                <span className="ml-1.5 px-1.5 py-0.2 rounded bg-amber-50 text-amber-700 border border-amber-200">
                                  重试 #{r.retry_no}
                                </span>
                              )}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-slate-600" title={`计划时刻：${r.planned_at || '—'}`}>
                              {r.attempted_at || r.sent_at || '—'}
                            </td>
                            <td className="py-2.5 px-3">
                              <span className={`px-2 py-0.5 rounded font-semibold ${badge.color}`}>{badge.label}</span>
                            </td>
                            <td className="py-2.5 px-3 text-slate-600">
                              {humanizeReminderLogError(r.result, r.error, kind)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* 弹窗底部：仅分级管理员可审核通过/驳回；超管只读查看 */}
            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isSuperAdmin ? (
                  detailData.instance.status === 'submitted' && detailData.instance.need_review && (
                    <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-violet-50 border border-violet-200 text-violet-800 text-xs font-semibold">
                      <ShieldCheck className="w-3.5 h-3.5 text-violet-600 shrink-0" />
                      <span>超管只读查看（由创建该任务的分级管理员审核）</span>
                    </span>
                  )
                ) : (
                  <>
                    {detailData.instance.kind !== 'notice' &&
                      detailData.instance.need_review === true &&
                      detailData.instance.status === 'submitted' && (
                        <>
                          <button
                            disabled={manualSubmissionBusy} onClick={() => void save('approve')}
                            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                          >
                            <Check className="w-3.5 h-3.5" />
                            <span>审核通过</span>
                          </button>
                          <button
                            disabled={manualSubmissionBusy} onClick={() => { setRejectReason(''); setRejectOpen(true); }}
                            className="px-4 py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                          >
                            <X className="w-3.5 h-3.5" />
                            <span>驳回重交</span>
                          </button>
                        </>
                      )}
                  </>
                )}
              </div>
              <button
                onClick={() => onClose()}
                disabled={manualSubmissionBusy}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl cursor-pointer transition-colors disabled:opacity-50"
              >
                关闭详情
              </button>
            </div>
          </div>
        </div>
        <ImageViewer source={viewerSource} onClose={() => setViewerSource(null)} />
    </>
  );
}
