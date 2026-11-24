import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Plus,
  Edit3,
  Trash2,
  Clock,
  FileText,
  Image as ImageIcon,
  Paperclip,
  Bell,
  CheckCircle2,
  PauseCircle,
  Lock,
  X,
  Sparkles,
  Users,
  Upload,
  Layers,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  Smartphone,
  Hash,
  AlignLeft,
  ExternalLink,
  Check,
  Send,
  ArrowRight,
  BarChart3,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { ApiRequestError } from '../../lib/realApi';
import { AdminScopeHeader } from './AdminScopeHeader';
import { VariableNoticeFields, VariableNoticeIssues, VariableNoticeValidation, normalizeVariableBody } from './VariableNoticeFields';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import {
  FormFieldSpec,
  NoticeContentFormat,
  NoticeMessageItem,
  NoticeReceiptMode,
  TemplateItem,
  UploadH5Module,
  VariableNoticeConfig,
  VariableNoticeIssue,
  getTemplateRoleIds,
} from '../../lib/types';

const UPLOAD_MODULE_META: Record<
  UploadH5Module,
  { label: string; shortLabel: string; desc: string; icon: React.ReactNode; color: string }
> = {
  fields: {
    label: '字段（报数类型）',
    shortLabel: '字段报数',
    desc: '填写结构化数值或指标（如上门户数、意向单数）',
    icon: <Hash className="w-3.5 h-3.5" />,
    color: 'bg-blue-50 text-blue-700 border-blue-200',
  },
  text: {
    label: '文本',
    shortLabel: '文本说明',
    desc: '输入多行文字总结、进展说明或备注汇报',
    icon: <AlignLeft className="w-3.5 h-3.5" />,
    color: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  },
  image: {
    label: '图片',
    shortLabel: '图片上传',
    desc: '现场拍照或从手机相册选择实况照片/截图上传',
    icon: <ImageIcon className="w-3.5 h-3.5" />,
    color: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  },
  file: {
    label: '文件',
    shortLabel: '文件上传',
    desc: '上传 Excel 表格、Word、PDF 等业务附件',
    icon: <Paperclip className="w-3.5 h-3.5" />,
    color: 'bg-violet-50 text-violet-700 border-violet-200',
  },
};

/* 2026-10-29 执行日：1=周一…7=周日 */
const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

function describeExecWeekdays(days: number[]): string {
  const s = [...new Set(days)].sort((a, b) => a - b);
  if (s.length === 7) return '每天';
  if (s.length === 0) return '未选择（不会执行）';
  if (s.length === 5 && s.every((d, i) => d === i + 1)) return '仅工作日';
  if (s.length === 2 && s[0] === 6 && s[1] === 7) return '仅周末';
  return s.map((d) => WEEKDAY_LABELS[d - 1]).join('、');
}

function describeTemplateSchedule(tpl: TemplateItem): string {
  const days = tpl.exec_dom_days?.length ? tpl.exec_dom_days : tpl.period === 'month' ? [tpl.anchor_dom ?? 1] : [];
  if (days.length) return `每月 ${days.join('、')} 号`;
  if (tpl.period === 'week') return `每${WEEKDAY_LABELS[(tpl.anchor_weekday ?? 1) - 1]}`;
  const weekdays = tpl.exec_weekdays?.length ? tpl.exec_weekdays : [1, 2, 3, 4, 5, 6, 7];
  const label = describeExecWeekdays(weekdays);
  return label.startsWith('周') ? `每周 ${label}` : label;
}

function isBroadcastNotice(tpl: TemplateItem): boolean {
  return tpl.kind === 'notice' && !tpl.spec?.notice_receipt?.enabled;
}

function broadcastAudience(tpl: TemplateItem, total?: number): string {
  const audience = tpl.spec?.variable_notice ? '变量表格中的成员' : '当前角色中符合资格的成员';
  return total === undefined ? `将向${audience}发送通知。` : `将向${audience}共 ${total} 人发送通知。`;
}

type PushPreview = { total: number; completed: number; unfinished: number };

const NOTICE_KINDS: NoticeContentFormat[] = ['text', 'image', 'file', 'video'];
const NOTICE_LABELS: Record<NoticeContentFormat, string> = { text: '文本', image: '图片', file: '文件', video: '视频' };
const NOTICE_UPLOADS = {
  image: { accept: '.png,.jpg,.jpeg,.webp', max: 9 },
  file: { accept: '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip', max: 64 },
  video: { accept: '.mp4,.mov,.m4v', max: 100 },
};
type NoticeAttachmentKind = keyof typeof NOTICE_UPLOADS;
const DEFAULT_RECEIPT = '我已知晓';
const DEFAULT_NOTICE_TEXT = '请按时完成本时段重点工作安排与点检。';

function noticeAssetUrl(item: NoticeMessageItem): string | undefined {
  if (item.asset_id) return `/api/admin/notice-assets/${encodeURIComponent(item.asset_id)}`;
  if (item.image_path) return `/api/admin/template-assets/${encodeURIComponent(item.image_path.split('/').pop() || '')}`;
}

function noticeAssetSize(bytes?: number) {
  if (!bytes) return '';
  return bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatManualDeadline(value?: string | null): string {
  if (!value) return '不限';
  const [year, month, day] = value.slice(0, 10).split('-').map(Number);
  return `${year}年${month}月${day}日 ${value.slice(11, 16)}`;
}

function NoticePhonePreview({ body, media, label, mode, guide, title }: {
  body: string; media: NoticeMessageItem[]; label: string; mode: NoticeReceiptMode; guide?: string; title?: string;
}) {
  const linkMode = label && mode === 'link';
  /* 2026-11-19 需求四（所见即所得）：link 模式同样先发正文与全部附件，
     最后才追加一条确认链接。预览不再截断正文、不再隐藏媒体气泡。
     2026-11-20 需求 A/B：链接气泡改为与实发消息逐字一致的三行标准文本
     （头部 + 引导语 + 链接行），media 气泡只展示媒体本体（不发文件名）。 */
  const guideLine = (guide || '').trim() || `请打开链接阅读完整通知，点击「${label || '我已知晓'}」确认。`;
  const heading = `【德阳量密助手】您收到《${(title || '').trim() || '通知'}》通知。`;
  const message = body.trim();
  return <aside aria-label="量子密信通知预览" className="rounded-2xl border border-slate-200 bg-slate-50 overflow-hidden">
    <h4 className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-3 text-sm font-semibold">
      <Smartphone className="size-4 text-blue-700" />量子密信通知预览
    </h4>
    <div className="space-y-3 p-4">
      {message && <p className="rounded-xl border border-slate-200 bg-white p-3 whitespace-pre-wrap break-words">{message}</p>}
      {media.filter(item => item.format !== 'text').map(item => <div key={item.id} className="rounded-xl border border-slate-200 bg-white p-3 break-words">
        {item.format === 'image' && <img src={noticeAssetUrl(item)} alt={item.file_name || '通知图片'}
          className="max-h-44 w-full rounded-md object-contain" />}
        {item.format === 'video' && <video controls playsInline preload="metadata" src={noticeAssetUrl(item)}
          className="max-h-44 w-full rounded-md bg-black" />}
      </div>)}
      {linkMode && <p className="rounded-xl border border-slate-200 bg-white p-3 break-words">
        {heading}<br />
        {guideLine}<br />
        <span className="text-blue-700 break-all">http://…/h5/notice-receipt#t=…</span>
      </p>}
      {label && !linkMode && <p className="rounded-xl border border-slate-200 bg-white p-3 break-words">
        以上为通知内容，查收后请直接回复「{label}」完成确认。
      </p>}
      {!message && !media.length && !label && <p className="text-slate-500">添加通知内容后可预览。</p>}
    </div>
  </aside>;
}

function summarizeNoticeItemsToText(items: NoticeMessageItem[]): string {
  /* 2026-10-28：图片/文件提醒只负责"到点把图推过去"，**不再**往正文里塞说明文字
     （原来会把「当日数据看板（到点自动生成并推送）」这类描述拼进消息里）。 */
  return items
    .map((item) => (item.format === 'text' ? (item.text_content || '').trim() : ''))
    .filter(Boolean)
    .join('\n');
}

function resolveUploadModules(tpl: TemplateItem): UploadH5Module[] {
  if (Array.isArray(tpl.spec?.upload_modules) && tpl.spec.upload_modules.length > 0) {
    return tpl.spec.upload_modules;
  }
  if (tpl.kind === 'form') return ['fields', 'text'];
  if (tpl.kind === 'image') return ['image', 'text'];
  if (tpl.kind === 'file') return ['file', 'text'];
  return ['fields', 'image', 'text'];
}

/* ================================================================== */
/* 上传任务（1. 密信提醒消息 + 2. H5 上传页面）手机预览组件              */
/* ================================================================== */
interface UploadH5PreviewProps {
  isAdhoc: boolean;
  isManual: boolean;
  taskName: string;
  remindAt: string;
  deadline: string;
  /* 2026-11-12 下午（紧急修复）：截止类型——预览的「截止时间」必须按类型换算日期
     （本月月底→当月最后一天、本周日→本周日、当天→今天；00:00=当天24点=次日0点），
     与后端 scheduler.calc_instance_deadline / format_deadline_display 同口径，
     避免「预览显示今天 17:00、实发显示 11月30日 17:00」的两套话术。 */
  deadlineType: 'same_day' | 'weekend' | 'month_end' | null;
  remindText: string;
  modules: UploadH5Module[];
  fields: FormFieldSpec[];
  textLabel: string;
  textPlaceholder: string;
}

/* 与后端 format_deadline_rule 同口径的规则样式由 formatDeadlineRuleLabel 提供（见下）。 */

/* 2026-11-13 用户拍板：截止时间统一显示为**当前周期对应的具体日期**「11月30日 17:00」，
   废除此前的「本月月底 17:00 / 本周日 17:00 / 今天 17:00」抽象规则词。
   换算口径与后端 state.format_deadline_rule 一字不差：same_day=当天、weekend=本周日、
   month_end=当月最后一天；无截止=不限。列表徽章 / 编辑弹窗两处预览 / H5 任务页徽章统一。 */
export const formatDeadlineRuleLabel = (
  type: 'same_day' | 'weekend' | 'month_end' | null | undefined,
  hhmm: string,
  ref?: Date,
): string => {
  if (!hhmm) return '不限';
  const now = ref || new Date();
  let y = now.getFullYear();
  let m = now.getMonth();
  let d = now.getDate();
  if (type === 'month_end') {
    const lastDay = new Date(y, m + 1, 0); // 下月 0 号 = 本月最后一天
    y = lastDay.getFullYear();
    m = lastDay.getMonth();
    d = lastDay.getDate();
  } else if (type === 'weekend') {
    const iso = now.getDay() === 0 ? 7 : now.getDay(); // 周日=7（当天是周日即今天）
    const target = new Date(y, m, d + ((7 - iso) % 7));
    y = target.getFullYear();
    m = target.getMonth();
    d = target.getDate();
  }
  return `${m + 1}月${d}日 ${hhmm}`;
};

const UploadFlowPhonePreview: React.FC<UploadH5PreviewProps> = ({
  isAdhoc,
  isManual,
  taskName,
  remindAt,
  deadline,
  deadlineType,
  remindText,
  modules,
  fields,
  textLabel,
  textPlaceholder,
}) => {
  const [previewStep, setStep] = useState<'im' | 'h5'>('h5');
  const step = isAdhoc ? 'h5' : previewStep;
  /* 截止时间展示：统一规则样式（本月月底 17:00 / 本周日 17:00 / 今天 17:00 / 不限），
   与实发消息里后端解析出的具体时间戳互补——预览层按用户选择的规则展示。 */
  const deadlineText = isManual ? formatManualDeadline(deadline) : deadline ? formatDeadlineRuleLabel(deadlineType, deadline) : '不限';

  const displayTitle = taskName.trim() || '当日上门走访晒图与意向单报数';
  const displayRemindText =
    remindText.trim() ||
    '请各片长/分支局长于今日截止时间前，点击下方专属 H5 链接完成今日上门走访报数及图片上传。';
  /* 预览里的链接用占位形式，避免把真实域名/令牌暴露在截图里 */
  const linkPreviewUrl = 'https://<你的域名>/h5/task#t=****';

  return (
    <div className="bg-indigo-50/50 rounded-2xl p-4 text-slate-800 space-y-3.5 border border-indigo-200/80 shadow-xs">
      {/* 顶部步骤切换器（仅保留 1. 密信提醒 与 2. H5上传页） */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Smartphone className="w-4 h-4 text-indigo-600" />
          <span className="text-xs font-bold tracking-tight text-slate-900">H5 上传任务实时预览</span>
        </div>
        {!isAdhoc && <div className="inline-flex rounded-xl bg-white p-0.5 border border-indigo-200/80 text-[11px]">
          <button
            type="button"
            onClick={() => setStep('im')}
            className={`px-2.5 py-1 rounded-lg font-semibold cursor-pointer transition-all ${
              step === 'im' ? 'bg-indigo-600 text-white shadow-2xs' : 'text-slate-600 hover:text-indigo-700'
            }`}
          >
            1. 密信提醒
          </button>
          <button
            type="button"
            onClick={() => setStep('h5')}
            className={`px-2.5 py-1 rounded-lg font-semibold cursor-pointer transition-all ${
              step === 'h5' ? 'bg-indigo-600 text-white shadow-2xs' : 'text-slate-600 hover:text-indigo-700'
            }`}
          >
            2. H5上传页
          </button>
        </div>}
      </div>

      {/* 手机屏幕容器（2026-11-20 需求 B-3：H5 步骤与 task.html 真实页面同视觉基准——
          --blue #15558a / --canvas #f4f6f8 / --line #e2e8ee / 白卡 12px 圆角 / 顶栏 3px 主色条） */}
      <div className="bg-[#f4f6f8] rounded-2xl overflow-hidden text-slate-800 border-2 border-[#c7d9ea] shadow-inner min-h-[410px] flex flex-col">
        {step === 'im' ? (
          <>
            {/* 步骤1 顶部：办公软件标题行 */}
            <div className="bg-white px-4 py-2.5 border-b border-[#e2e8ee] flex items-center justify-between text-[11px]">
              <span className="font-mono font-bold text-slate-700">{isManual ? '立即推送' : (remindAt || '16:30')}</span>
              <span className="font-bold text-slate-900">办公软件 · 任务助手</span>
              <span className="text-[10px] text-[#15558a] font-semibold">H5 专属通道</span>
            </div>
            <div className="p-4 flex-1 flex flex-col justify-between space-y-4 bg-slate-100">
              <div className="space-y-3">
                <div className="text-center">
                  <span className="px-2 py-0.5 rounded bg-slate-200/80 text-slate-500 text-[10px] font-mono">
                    执行时间 {remindAt || '16:30'}
                  </span>
                </div>
                <div className="flex items-start gap-2.5">
                  <div className="bg-white rounded-2xl rounded-tl-xs p-3.5 shadow-xs border border-slate-200/80 max-w-[92%]">
                    <div className="text-xs text-slate-800 whitespace-pre-line leading-relaxed">
                      {`【任务提醒】${displayTitle}\n${displayRemindText}\n\n截止时间：${deadline ? deadlineText : '不限'}\n提交入口（点击链接打开上传任务）：\n${linkPreviewUrl}`}
                    </div>
                  </div>
                </div>
              </div>
              <div className="text-center pt-2">
                <button
                  type="button"
                  onClick={() => setStep('h5')}
                  className="px-3.5 py-1.5 rounded-xl bg-blue-600 text-white text-[11px] font-semibold cursor-pointer shadow-xs"
                >
                  查看 H5 上传页效果 →
                </button>
              </div>
            </div>
          </>
        ) : (
          <>
            {/* 步骤2 顶部：task.html 的 topbar（白底 + 3px 主色条 + 品牌/副标） */}
            <div className="bg-white px-4 py-2.5 shadow-xs flex items-center justify-between" style={{ borderTop: '3px solid #15558a', borderBottom: '1px solid #e2e8ee' }}>
              <span className="text-[13px] font-semibold text-[#182530] tracking-tight">德阳量密助手</span>
              <span className="text-xs text-[#60707d]">任务填报</span>
            </div>

            <div className="p-4 flex-1 overflow-y-auto space-y-3 bg-[#f4f6f8]">
              {/* H5 任务头卡（.task-head：白卡 + 19px 标题 + 截止徽章 + 提交人行 + 状态徽章） */}
              <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs">
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 mb-2.5">
                  <h3 className="text-[19px] leading-[1.35] font-semibold text-[#182530] tracking-tight flex-1">{displayTitle}</h3>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium text-[#15558a] bg-[#eef4fa] border border-[#c7d9ea] whitespace-nowrap">
                    截止 {deadline ? deadlineText : '不限'}
                  </span>
                </div>
                <p className="text-[14px] leading-[1.6] text-[#495966] whitespace-pre-line">{isAdhoc ? '' : displayRemindText}</p>
                <div className="flex flex-wrap items-center justify-between gap-2 mt-3.5 pt-3 border-t border-[#e2e8ee] text-[12.5px] text-[#60707d]">
                  <span>提交人：张三{!isAdhoc && !isManual && ` · 执行时间：${remindAt || '16:30'}`}</span>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium text-[#15558a] bg-[#eef4fa] border border-[#c7d9ea]">待上传</span>
                </div>
              </div>

              {modules.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-400 bg-white rounded-xl border border-dashed border-slate-200">
                  请在左侧勾选至少一种 H5 上传组件（字段 / 文本 / 图片 / 文件）
                </div>
              ) : (
                <div className="space-y-3">
                  {/* 模块 A：字段（报数类型）—— H5 block 视觉 */}
                  {modules.includes('fields') && (
                    <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs space-y-2.5">
                      <div className="flex items-center gap-1.5 text-sm font-semibold text-[#182530]">
                        <Hash className="w-[18px] h-[18px] text-[#15558a]" />
                        <span>数据报数填报</span>
                      </div>
                      <div className="space-y-2.5">
                        {(fields.length > 0
                          ? fields
                          : [
                              {
                                key: 'visit_count',
                                label: '今日累计上门户数',
                                type: 'number' as const,
                                required: true,
                              },
                              {
                                key: 'intent_orders',
                                label: '新增商机意向单数',
                                type: 'number' as const,
                                required: true,
                              },
                            ]
                        ).map((f, i) => (
                          <div key={i} className="space-y-1">
                            <label className="text-[13px] font-medium text-[#495966] flex items-center justify-between">
                              <span>
                                {f.label || `报数项 #${i + 1}`}
                                {f.required && <span className="text-rose-500 ml-0.5">*</span>}
                              </span>
                              <span className="text-[11px] text-[#60707d]">
                                {f.type === 'number' ? '数值报数' : f.type === 'boolean' ? '是／否' : f.type === 'select' ? '下拉选择' : '文本'}
                              </span>
                            </label>
                            {f.type === 'select' || f.type === 'boolean' ? (
                              <select className="w-full bg-[#f7f9fb] border border-[#e2e8ee] rounded-lg px-2.5 py-2 text-[13px] text-[#182530]">
                                {(f.type === 'boolean' ? ['是', '否'] : f.options || []).map(
                                  (opt, idx) => (
                                    <option key={idx}>{opt}</option>
                                  ),
                                )}
                              </select>
                            ) : (
                              <input
                                type={f.type === 'number' ? 'number' : 'text'}
                                readOnly
                                placeholder={
                                  f.type === 'number' ? '请输入具体数值（如：18）' : '请输入内容'
                                }
                                className="w-full bg-[#f7f9fb] border border-[#e2e8ee] rounded-lg px-2.5 py-2 text-[13px] text-[#182530] placeholder:text-[#8a95a1]"
                              />
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 模块 B：文本反馈 */}
                  {modules.includes('text') && (
                    <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs space-y-2">
                      <div className="flex items-center gap-1.5 text-sm font-semibold text-[#182530]">
                        <AlignLeft className="w-[18px] h-[18px] text-[#15558a]" />
                        <span>{textLabel.trim() || '文字情况反馈'}</span>
                      </div>
                      <textarea
                        rows={2}
                        readOnly
                        placeholder={
                          textPlaceholder.trim() || '请输入今日重点执行情况、未破零帮扶进度或补充说明…'
                        }
                        className="w-full bg-[#f7f9fb] border border-[#e2e8ee] rounded-lg p-2.5 text-[13px] text-[#182530] placeholder:text-[#8a95a1] resize-none"
                      />
                    </div>
                  )}

                  {/* 模块 C：图片上传 */}
                  {modules.includes('image') && (
                    <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1.5 text-sm font-semibold text-[#182530]">
                          <ImageIcon className="w-[18px] h-[18px] text-[#15558a]" />
                          <span>图片上传</span>
                        </span>
                        <span className="text-[11px] text-[#60707d]">JPG / PNG / WEBP</span>
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        <div className="h-[68px] w-full rounded-lg border-2 border-dashed border-[#c7d9ea] bg-[#eef4fa] flex flex-col items-center justify-center text-center gap-1 text-[#15558a]">
                          <ImageIcon className="w-4 h-4 shrink-0" />
                          <span className="text-[10px] font-medium leading-none whitespace-nowrap">+ 点击选择图片</span>
                        </div>
                        <div className="h-[68px] rounded-lg bg-[#f7f9fb] border border-[#e2e8ee] flex items-center justify-center text-[10px] text-[#8a95a1]">
                          示例晒图1.jpg
                        </div>
                      </div>
                    </div>
                  )}

                  {/* 模块 D：文件上传 */}
                  {modules.includes('file') && (
                    <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1.5 text-sm font-semibold text-[#182530]">
                          <Paperclip className="w-[18px] h-[18px] text-[#15558a]" />
                          <span>文件上传</span>
                        </span>
                        <span className="text-[11px] text-[#60707d]">Excel / PDF / Word</span>
                      </div>
                      <div className="p-2.5 rounded-lg border-2 border-dashed border-[#c7d9ea] bg-[#eef4fa] flex items-center justify-center gap-1.5 text-[#15558a] text-[13px] font-medium">
                        <Upload className="w-4 h-4" />
                        <span>点击选择文档并上传</span>
                      </div>
                    </div>
                  )}

                  {/* 备注说明（选填）——H5 noteField 块 */}
                  <div className="bg-white p-4 rounded-xl border border-[#e2e8ee] shadow-2xs space-y-2">
                    <div className="flex items-center gap-1.5 text-sm font-semibold text-[#182530]">
                      <AlignLeft className="w-[18px] h-[18px] text-[#15558a]" />
                      <span>备注说明（选填）</span>
                    </div>
                    <textarea
                      rows={2}
                      readOnly
                      placeholder="请输入补充说明"
                      className="w-full bg-[#f7f9fb] border border-[#e2e8ee] rounded-lg p-2.5 text-[13px] text-[#182530] placeholder:text-[#8a95a1] resize-none"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* H5 底部提交按钮（task-actions：贴底白条 + 深蓝主按钮） */}
            <div className="bg-white px-4 py-3 border-t border-[#e2e8ee]">
              <button
                type="button"
                className="w-full h-11 rounded-lg bg-[#15558a] text-white font-medium text-sm flex items-center justify-center gap-1.5 cursor-default"
              >
                <Send className="w-4 h-4" />
                <span>提交任务</span>
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export const TemplatesView: React.FC = () => {
  const {
    templates,
    roles,
    refreshTemplates,
    isSuperAdmin,
    ownerFilter,
    setOwnerFilter,
    adminAccounts,
    setStatsFocus,
    platform,
  } = useAppStore();
  const toast = useToast();

  // 顶部筛选条件：按角色选择 + 按任务类型选择 + 关键词
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [keyword, setKeyword] = useState('');
  const [expandedRows, setExpandedRows] = useState<Record<number, boolean>>({});

  const [modalOpen, setModalOpen] = useState(false);
  const [editingTpl, setEditingTpl] = useState<TemplateItem | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [deleteConfirmTpl, setDeleteConfirmTpl] = useState<TemplateItem | null>(null);
  const [pushingId, setPushingId] = useState<number | null>(null);
  const [pushConfirmTpl, setPushConfirmTpl] = useState<TemplateItem | null>(null);
  /* 2026-11-19 需求一：本周期已推送过时的二次确认（新建批次并重推） */
  const [repushConfirm, setRepushConfirm] = useState<{
    tpl: TemplateItem; targetCount: number; preview: PushPreview | null;
  } | null>(null);
  /* 2026-11-21 需求二：推送弹窗「过滤本周期已完成 / 已回执成员」开关（默认开启）与实时人数 */
  const [skipCompleted, setSkipCompleted] = useState(true);
  const [pushPreview, setPushPreview] = useState<PushPreview | null>(null);
  const repushPreview = pushPreview ?? repushConfirm?.preview ?? null;
  const [pushIssues, setPushIssues] = useState<VariableNoticeIssue[]>([]);
  useEffect(() => {
    const tpl = pushConfirmTpl || repushConfirm?.tpl;
    setPushIssues([]);
    setPushPreview(null);
    if (!tpl) return;
    let live = true;
    api.get<PushPreview>(
      `/api/admin/templates/${tpl.id}/push-preview`,
    ).then(res => { if (live) setPushPreview(res); }).catch(err => {
      if (live) {
        setPushPreview(null);
        if (err instanceof ApiRequestError) setPushIssues(err.issues || []);
      }
    });
    return () => { live = false; };
  }, [pushConfirmTpl, repushConfirm]);

  // 弹窗表单状态（任务类型简化为两大类：'notice' 纯通知提醒 | 'upload' 上传任务）
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'notice' | 'upload'>('notice');
  const [isManual, setIsManual] = useState(false);
  const [pushOnCreate, setPushOnCreate] = useState(false);
  const createRequestId = useRef('');
  const [manualDeadline, setManualDeadline] = useState('');
  const hasPreview = kind === 'upload' || platform === 'zdxlz';
  /* 2026-10-29 执行日：1=周一…7=周日；全选七天等价于「每天」（保存时传 null）。
     2026-11-02 用户口径：去掉「执行周期」下拉，只按这里选的星期几触发。 */
  const [execWeekdays, setExecWeekdays] = useState<number[]>([1, 2, 3, 4, 5, 6, 7]);
  /* 2026-11-10 月度执行日：每月几号（1~31 多选）；null=未启用（每周模式）。
     execMode 仅是弹窗内的 UI 态（'weekly' | 'monthly'），实际生效看提交的 exec_dom_days。 */
  const [execMode, setExecMode] = useState<'weekly' | 'monthly'>('weekly');
  const [execDomDays, setExecDomDays] = useState<number[]>([]);
  const toggleExecDomDay = (d: number) => {
    setExecDomDays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b)));
  };
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [remindAt, setRemindAt] = useState('08:15');
  /* 2026-11-12 任务E1：截止配置三件套（与后端同名字段对齐）。
     hasDeadline=false → 无截止（deadlineType/deadlineTime 不提交任何值，实例截止存哨兵）。 */
  const [hasDeadline, setHasDeadline] = useState<boolean>(true);
  const [deadlineType, setDeadlineType] = useState<'same_day' | 'weekend' | 'month_end'>('same_day');
  const [deadlineTime, setDeadlineTime] = useState('18:00');
  const [requireReview, setRequireReview] = useState<boolean>(true);
  const [enabled, setEnabled] = useState(true);

  // 纯通知提醒专用状态
  const [noticeItems, setNoticeItems] = useState<NoticeMessageItem[]>([]);
  const [noticeKinds, setNoticeKinds] = useState<NoticeContentFormat[]>(['text']);
  const [noticeText, setNoticeText] = useState(DEFAULT_NOTICE_TEXT);
  const [hasVariableTable, setHasVariableTable] = useState(false);
  const [variableResult, setVariableResult] = useState<(VariableNoticeValidation & { body: string })>();
  const [variableIssues, setVariableIssues] = useState<VariableNoticeIssue[]>([]);
  const [variableBusy, setVariableBusy] = useState(false);
  const [sampleIndex, setSampleIndex] = useState(0);
  const variableRequest = useRef(0);
  const variableEnabled = kind === 'notice' && isManual && hasVariableTable;
  const variableValidation = variableEnabled && variableResult?.body === normalizeVariableBody(noticeText)
    && variableResult.config.platform === platform ? variableResult : undefined;
  const [receiptEnabled, setReceiptEnabled] = useState(false);
  const [receiptMode, setReceiptMode] = useState<NoticeReceiptMode>('link');
  const [receiptPhrase, setReceiptPhrase] = useState(DEFAULT_RECEIPT);
  const [receiptButtonLabel, setReceiptButtonLabel] = useState(DEFAULT_RECEIPT);
  /* 2026-11-20 需求 A：link 模式选填「链接引导语」——确认链接消息第二行的自定义提示语 */
  const [receiptGuideText, setReceiptGuideText] = useState('');
  const [uploadingAssetId, setUploadingAssetId] = useState<string | null>(null);

  // 上传任务专用状态（提醒文本 + 选择 H5 上传项 [字段/文本/图片/文件] + 是否需要审核）
  const [uploadRemindText, setUploadRemindText] = useState(
    '请按时点击下方 H5 链接，完成本时段业务数据报数及现场图片/文件上传。',
  );
  const [uploadModules, setUploadModules] = useState<UploadH5Module[]>(['fields', 'image', 'text']);
  const [multipleImages, setMultipleImages] = useState(false);
  const [multipleFiles, setMultipleFiles] = useState(false);
  const [fields, setFields] = useState<FormFieldSpec[]>([
    { key: 'visit_count', label: '今日累计上门户数', type: 'number', required: true },
    { key: 'intent_count', label: '商机意向单数量', type: 'number', required: true },
  ]);
  const [textLabel, setTextLabel] = useState('文字情况说明');
  const [textPlaceholder, setTextPlaceholder] = useState('请简要填写执行情况或差缺原因说明…');
  const [saving, setSaving] = useState(false);
  /** 2026-11-12 晚 · 不定期任务（菜单自选）：无推送时刻、无截止，调度物理隔离——
   *  成员私聊回复【菜单】+ 数字自主获取一次性填报链接（Pull 模式，随到随报、提交即焚、支持多次）。 */
  const [isAdhoc, setIsAdhoc] = useState(false);

  const roleMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of roles) {
      map[r.role_id] = r.role_name;
    }
    return map;
  }, [roles]);

  const filteredTemplates = useMemo(() => {
    return templates.filter((t) => {
      const tplRoleIds = getTemplateRoleIds(t);
      if (roleFilter && !tplRoleIds.includes(roleFilter)) return false;
      if (kindFilter) {
        if (kindFilter === 'notice' && t.kind !== 'notice') return false;
        if (kindFilter === 'upload' && t.kind === 'notice') return false;
      }
      if (keyword.trim()) {
        const kw = keyword.trim().toLowerCase();
        const roleNamesText = tplRoleIds
          .map((rid) => roleMap[rid] || rid)
          .join(' ')
          .toLowerCase();
        if (
          !t.name.toLowerCase().includes(kw) &&
          !roleNamesText.includes(kw) &&
          !t.text_tpl.toLowerCase().includes(kw)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [templates, roleFilter, kindFilter, keyword, roleMap]);

  const toggleExpandRow = (id: number) => {
    setExpandedRows((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleRoleSelection = (targetRoleId: string) => {
    setRoleIds((prev) =>
      prev.includes(targetRoleId)
        ? prev.filter((id) => id !== targetRoleId)
        : [...prev, targetRoleId],
    );
  };

  const selectAllRoles = () => {
    setRoleIds(roles.map((r) => r.role_id));
  };

  const selectSingleDefaultRole = () => {
    const fallback = roleFilter || roles[0]?.role_id || 'role_branch_chief';
    setRoleIds(fallback ? [fallback] : []);
  };

  const toggleUploadModule = (mod: UploadH5Module) => {
    setUploadModules((prev) =>
      prev.includes(mod) ? prev.filter((m) => m !== mod) : [...prev, mod],
    );
  };

  const openCreateModal = (defaultKind: 'notice' | 'upload' = 'notice') => {
    if (isSuperAdmin) {
      toast.warning('超级管理员仅负责全平台查看与监管，不可创建任务，请登录分级管理员账号创建');
      return;
    }
    resetVariableDraft(false);
    setEditingTpl(null);
    setName('');
    setKind(defaultKind);
    setIsAdhoc(false);
    setIsManual(false);
    setPushOnCreate(false);
    createRequestId.current = Array.from(crypto.getRandomValues(new Uint8Array(16)),
      byte => byte.toString(16).padStart(2, '0')).join('');
    setManualDeadline('');
    setExecWeekdays([1, 2, 3, 4, 5, 6, 7]);
    setExecMode('weekly');
    setExecDomDays([]);
    const defaultRoleId = roleFilter || roles[0]?.role_id || 'role_branch_chief';
    setRoleIds(defaultRoleId ? [defaultRoleId] : []);
    setRemindAt(defaultKind === 'notice' ? '08:15' : '16:30');
    setHasDeadline(true);
    setDeadlineType('same_day');
    setDeadlineTime('18:00');
    setRequireReview(true);
    setNoticeItems([]);
    setNoticeKinds(['text']);
    setNoticeText(DEFAULT_NOTICE_TEXT);
    setReceiptEnabled(false);
    setReceiptMode('link');
    setReceiptPhrase(DEFAULT_RECEIPT);
    setReceiptButtonLabel(DEFAULT_RECEIPT);
    setUploadRemindText(
      '请按时点击下方 H5 链接，完成今日上门走访户数报数及图片上传。',
    );
    setUploadModules(['fields', 'image', 'text']);
    setMultipleImages(false);
    setMultipleFiles(false);
    setFields([
      { key: 'visit_count', label: '今日累计上门户数', type: 'number', required: true },
      { key: 'intent_count', label: '新增意向单数量', type: 'number', required: true },
    ]);
    setTextLabel('文字情况说明');
    setTextPlaceholder('请填写补充说明…');
    setEnabled(true);
    setModalOpen(true);
  };

  const openEditModal = (tpl: TemplateItem) => {
    const variable = Boolean(tpl.spec?.variable_notice);
    resetVariableDraft(variable);
    setEditingTpl(tpl);
    setName(tpl.name);
    setKind(tpl.kind === 'notice' ? 'notice' : 'upload');
    setIsManual(Boolean(tpl.is_manual));
    setManualDeadline(tpl.manual_deadline_at?.slice(0, 16).replace(' ', 'T') || '');
    /* 2026-11-12 晚 · 不定期任务回显：菜单模式（无推送时刻/无截止，界面禁用两项配置） */
    setIsAdhoc(Boolean(tpl.is_adhoc) && tpl.kind !== 'notice');
    /* 2026-10-29 执行日：null/空 = 每天 → 七天全上 */
    setExecWeekdays(
      Array.isArray(tpl.exec_weekdays) && tpl.exec_weekdays.length
        ? tpl.exec_weekdays.map(Number)
        : [1, 2, 3, 4, 5, 6, 7],
    );
    /* 2026-11-10 月度执行日回显：数据库有 exec_dom_days → 月度模式 */
    if (Array.isArray(tpl.exec_dom_days) && tpl.exec_dom_days.length) {
      setExecMode('monthly');
      setExecDomDays(tpl.exec_dom_days.map(Number).filter((n) => n >= 1 && n <= 31));
    } else {
      setExecMode('weekly');
      setExecDomDays([]);
    }
    const tplRoles = getTemplateRoleIds(tpl);
    setRoleIds(
      tplRoles.length > 0 ? tplRoles : [roles[0]?.role_id || 'role_branch_chief'],
    );
    setRemindAt(tpl.remind_at || '');
    /* 2026-11-12 任务E1：截止配置三件套回显（老模板无 deadline_type 时回落 same_day） */
    setHasDeadline(tpl.has_deadline !== false);
    setDeadlineType(
      tpl.deadline_type === 'weekend' || tpl.deadline_type === 'month_end' ? tpl.deadline_type : 'same_day',
    );
    setDeadlineTime(tpl.deadline_time || tpl.deadline || '18:00');
    setRequireReview(tpl.kind !== 'notice' && (tpl.need_review ?? tpl.spec?.require_review ?? false));
    setEnabled(tpl.enabled);

    if (tpl.kind === 'notice') {
      const items = (tpl.spec?.notice_items || []).map(item => ({
        ...item,
        format: item.format === 'file' && item.image_path && !item.asset_id ? 'image' as const : item.format,
      }));
      if (tpl.attach_image_path && !items.some(item => item.format === 'image')) {
        items.push({ id: 'legacy_image', format: 'image', image_path: tpl.attach_image_path,
          file_name: tpl.attach_image_name || tpl.attach_image_path.split('/').pop() });
      }
      const body = summarizeNoticeItemsToText(items) || tpl.text_tpl || '';
      setNoticeText(body.replace(/\\r\\n|\\n/g, '\n'));
      if (variable) void validateVariableDraft(body, String(tpl.id));
      setNoticeItems(items.filter(item => item.format !== 'text'));
      setNoticeKinds(NOTICE_KINDS.filter(key => key === 'text' ? Boolean(body) : items.some(item => item.format === key)));
      setReceiptEnabled(tpl.spec?.notice_receipt?.enabled === true);
      setReceiptMode(tpl.spec?.notice_receipt?.mode || (tpl.spec?.notice_receipt?.enabled ? 'text' : 'link'));
      setReceiptPhrase(tpl.spec?.notice_receipt?.phrase || DEFAULT_RECEIPT);
      setReceiptButtonLabel(tpl.spec?.notice_receipt?.button_label || DEFAULT_RECEIPT);
      setReceiptGuideText(tpl.spec?.notice_receipt?.receipt_guide_text || '');
    } else {
      setUploadRemindText(tpl.text_tpl || '');
      setUploadModules(resolveUploadModules(tpl));
      setMultipleImages(tpl.spec?.allow_multiple_images ?? tpl.name.trim().toLowerCase() === 'ta使用统计');
      setMultipleFiles(tpl.spec?.allow_multiple_files === true);
      if (Array.isArray(tpl.spec?.fields) && tpl.spec.fields.length > 0) {
        setFields(tpl.spec.fields.map((f) => ({ ...f })));
      } else {
        setFields([
          { key: 'visit_count', label: '今日累计上门户数', type: 'number', required: true },
        ]);
      }
      setTextLabel(tpl.spec?.text_label || '文字情况说明');
      setTextPlaceholder(tpl.spec?.text_placeholder || '请填写补充说明…');
    }

    setModalOpen(true);
  };

  /* 2026-10-29 执行日：点一下切换某天 */
  const toggleExecWeekday = (d: number) => {
    setExecWeekdays((prev) =>
      prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b),
    );
  };

  const handleNoticeUpload = async (assetKind: NoticeAttachmentKind, files: File[]) => {
    if (!files.length || uploadingAssetId !== null) return;
    const { max } = NOTICE_UPLOADS[assetKind];
    if (files.some(file => !file.size || file.size > max * 1024 ** 2)) {
      toast.warning(`每个${NOTICE_LABELS[assetKind]}必须非空且不超过 ${max} MB`);
      return;
    }
    setUploadingAssetId(assetKind);
    const uploaded: NoticeMessageItem[] = [];
    try {
      for (const file of files) {
        const body = new FormData();
        body.append('file', file);
        const res = await api.post<{ asset_id: string; file_name: string; file_size: number; file_mime: string }>(
          `/api/admin/notice-assets/${assetKind}`, body,
        );
        uploaded.push({ ...res, id: res.asset_id, format: assetKind });
      }
      toast.success(`已上传 ${uploaded.length} 个附件`);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '附件上传失败');
    } finally {
      if (uploaded.length) setNoticeItems(previous => [...previous, ...uploaded]);
      setUploadingAssetId(null);
    }
  };

  const handleToggleEnabled = async (tpl: TemplateItem) => {
    setBusyId(tpl.id);
    try {
      const nextEnabled = !tpl.enabled;
      const tplRoleIds = getTemplateRoleIds(tpl);
      await api.put(`/api/admin/templates/${tpl.id}`, tpl.spec?.variable_notice ? { enabled: nextEnabled } : {
        name: tpl.name,
        role_id: tplRoleIds[0] || tpl.role_id,
        role_ids: tplRoleIds,
        remind_at: tpl.remind_at,
        deadline: tpl.kind === 'notice' ? '' : tpl.deadline,
        /* 2026-11-12 任务E1：快捷启停带上截止三件套（后端已按 existing 回显补默认，双保险） */
        has_deadline: tpl.kind !== 'notice' && tpl.has_deadline !== false,
        deadline_type: tpl.deadline_type ?? null,
        deadline_time: tpl.kind === 'notice' ? null : tpl.deadline_time || tpl.deadline || null,
        text_tpl: tpl.text_tpl,
        spec: tpl.spec,
        enabled: nextEnabled,
        /* 2026-10-29 P1-5：快捷启停也要把审核开关与附图路径带上，
           避免这条路径上把 need_review / 附图配置写丢。 */
        need_review: tpl.need_review,
        attach_image_path: tpl.attach_image_path ?? null,
        /* 2026-10-29 H5.5：启停也带上图片名称，防写丢 */
        attach_image_name: tpl.attach_image_name ?? null,
        /* 2026-10-29 执行日：启停路径同样带上，避免把执行日冲回全周 */
        exec_weekdays: tpl.exec_weekdays ?? null,
        /* 2026-11-10 月度执行日：启停同样带上，避免把月度配置冲掉 */
        exec_dom_days: tpl.exec_dom_days ?? null,
      });
      await refreshTemplates();
      toast.success(
        nextEnabled ? `已启用模板「${tpl.name}」` : `已停用模板「${tpl.name}」`,
        nextEnabled ? '模板已启用' : '模板已停用',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '切换状态未成功');
    } finally {
      setBusyId(null);
    }
  };

  function resetVariableDraft(enabled: boolean) {
    variableRequest.current += 1;
    setHasVariableTable(enabled);
    setVariableBusy(false);
    setVariableIssues([]);
    setVariableResult(undefined);
    setSampleIndex(0);
  }

  async function validateVariableDraft(rawBody: string, source: File | string) {
    const request = ++variableRequest.current;
    const body = normalizeVariableBody(rawBody);
    setVariableBusy(true);
    setVariableIssues([]);
    setVariableResult(undefined);
    try {
      let payload: FormData | { body: string; config: VariableNoticeConfig };
      if (typeof source === 'string') {
        const { template } = await api.get<{ template: { spec_json: { variable_notice: VariableNoticeConfig } } }>(
          `/api/admin/templates/${source}`,
        );
        if (request !== variableRequest.current) return;
        payload = { body, config: template.spec_json.variable_notice };
      } else {
        payload = new FormData();
        payload.append('file', source);
        payload.append('body', body);
        payload.append('platform', platform);
      }
      const result = await api.post<VariableNoticeValidation>('/api/admin/variable-notices/validate', payload);
      if (request === variableRequest.current) {
        setVariableResult({ ...result, body });
        setSampleIndex(0);
      }
    } catch (err) {
      if (request !== variableRequest.current) return;
      const issues = err instanceof ApiRequestError ? err.issues : undefined;
      if (issues?.length) setVariableIssues(issues);
      else toast.error(err instanceof Error ? plainError(err.message) : '变量表格校验失败');
    } finally {
      if (request === variableRequest.current) setVariableBusy(false);
    }
  }

  const handleSaveTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      toast.warning('请填写任务模板名称');
      return;
    }
    if (!isAdhoc && !isManual && !remindAt.trim()) {
      toast.warning('请填写执行时间');
      return;
    }
    /* 2026-10-29 执行日：必须至少选一天，否则永远不会执行（2026-11-02 起周期固定按周）*/
    /* 2026-11-10 月度模式：切换为「每月执行」时必须至少选一个日期 */
    if (!isAdhoc && !isManual && execMode === 'weekly' && execWeekdays.length === 0) {
      toast.warning('请至少选择一天「执行日」', '缺少执行日');
      return;
    }
    if (!isAdhoc && !isManual && execMode === 'monthly' && execDomDays.length === 0) {
      toast.warning('每月执行至少选择一个日期（或点「每月月底」快捷）', '缺少执行日');
      return;
    }

    const activeNoticeItems = noticeItems.filter(item => noticeKinds.includes(item.format));
    if (variableEnabled && !variableValidation) {
      toast.warning('请先上传变量表格并通过校验');
      return;
    }
    const selectedRoles = variableEnabled ? [] : roleIds;
    let finalTextTpl = '';
    let spec: Record<string, any> = {};

    if (kind === 'notice') {
      if (!noticeKinds.length) {
        toast.warning('请选择至少一种通知内容');
        return;
      }
      if (noticeKinds.includes('text') && !noticeText.trim()) {
        toast.warning('请填写通知正文');
        return;
      }
      for (const assetKind of noticeKinds.filter(key => key !== 'text')) {
        if (!activeNoticeItems.some(item => item.format === assetKind && (item.asset_id || item.image_path))) {
          toast.warning(`请至少上传一个${NOTICE_LABELS[assetKind]}附件`);
          return;
        }
      }
      if (receiptEnabled && !(receiptMode === 'link' ? receiptButtonLabel : receiptPhrase).trim()) {
        toast.warning(receiptMode === 'link' ? '请填写确认按钮文案' : '请填写要求回复的话术');
        return;
      }
      finalTextTpl = noticeKinds.includes('text') ? noticeText.trim() : '';
      spec = {
        require_review: false,
        ...(variableValidation ? { variable_notice: variableValidation.config } : {}),
        notice_items: [
          ...(finalTextTpl ? [{ id: 'notice_text', format: 'text', text_content: finalTextTpl }] : []),
          ...activeNoticeItems,
        ],
        notice_receipt: { enabled: receiptEnabled, mode: receiptMode,
          ...(receiptMode === 'link'
            ? { button_label: receiptButtonLabel.trim(), ...(receiptGuideText.trim() ? { receipt_guide_text: receiptGuideText.trim() } : {}) }
            : { phrase: receiptPhrase.trim() }) },
      };
    } else {
      // 统一上传任务校验（2026-11-12 任务E1：截止配置改为 开关+类型+时刻）
      if (!isAdhoc && !isManual && hasDeadline && !deadlineTime.trim()) {
        toast.warning('请填写截止时刻');
        return;
      }
      if (!isAdhoc && !uploadRemindText.trim()) {
        toast.warning('请填写发送给用户的提醒文本');
        return;
      }
      if (uploadModules.length === 0) {
        toast.warning('请至少勾选一种 H5 上传内容类型（字段 / 文本 / 图片 / 文件）');
        return;
      }
      if (uploadModules.includes('fields')) {
        if (fields.length === 0) {
          toast.warning('请至少添加 1 个报数字段');
          return;
        }
        for (const f of fields) {
          if (!f.label.trim()) {
            toast.warning('请填写每个报数字段的名称');
            return;
          }
        }
      }
      finalTextTpl = isAdhoc ? '' : uploadRemindText.trim();
      spec = {
        require_review: requireReview,
        upload_modules: uploadModules,
        allow_multiple_images: uploadModules.includes('image') && multipleImages,
        allow_multiple_files: uploadModules.includes('file') && multipleFiles,
        fields: uploadModules.includes('fields')
          ? fields.map((f, idx) => ({
              ...f,
              key: f.key?.trim() || `field_${idx + 1}`,
              label: f.label.trim(),
            }))
          : [],
        text_label: textLabel.trim() || '文字情况说明',
        text_placeholder: textPlaceholder.trim(),
      };
    }

    const legacyImage = activeNoticeItems.find(item => item.format === 'image' && item.image_path);
    const noticeImagePath = legacyImage?.image_path || '';
    const noticeImageName = legacyImage?.file_caption?.trim() || '';

    setSaving(true);
    try {
      /* 2026-11-12 任务E1：截止三件套随保存提交——
         无截止 → has_deadline=false（deadline/deadline_type/deadline_time 全空）；
         有截止 → deadline_type ∈ {same_day, weekend, month_end} + deadline_time（HH:MM）。
         旧列 deadline 同步存 deadline_time，保证未升级的读取链路一致。 */
      const finalHasDeadline = kind !== 'notice' && hasDeadline && !isAdhoc && !isManual;
      const finalDeadline = finalHasDeadline ? deadlineTime.trim() : '';
      const finalDeadlineType = finalHasDeadline ? deadlineType : null;
      const finalDeadlineTime = finalHasDeadline ? deadlineTime.trim() : null;
      const manualConfig = { is_manual: isManual, manual_deadline_at: isManual && kind === 'upload' ? manualDeadline || null : null };
      if (editingTpl) {
        await api.put(`/api/admin/templates/${editingTpl.id}`, {
          name: name.trim(),
          role_id: selectedRoles[0],
          role_ids: selectedRoles,
          remind_at: isAdhoc || isManual ? '' : remindAt.trim(),
          is_adhoc: kind !== 'notice' && isAdhoc,
          ...manualConfig,
          deadline: finalDeadline,
          has_deadline: finalHasDeadline,
          deadline_type: finalDeadlineType,
          deadline_time: finalDeadlineTime,
          text_tpl: finalTextTpl,
          spec,
          enabled,
          /* 2026-11-02：不再有周期概念，选哪几天就每周那几天触发；七天全选等价「每天」存 null
             2026-11-10：月度模式时 exec_dom_days 带选中几号（每周字段随 UI 状态一并提交，
             后端以 exec_dom_days 非空优先判定）；切回每周时显式 [] 清空月度 */
          exec_weekdays: isAdhoc || isManual || execWeekdays.length === 7 ? null : execWeekdays,
          exec_dom_days: !isAdhoc && !isManual && execMode === 'monthly' ? execDomDays : [],
          /* 2026-10-28：审核开关以前只塞进 spec.require_review，后端读的是顶层 need_review，
             导致「需要审核」永远不生效、提交后直接变已完成。这里显式带上。 */
          need_review: kind === 'notice' ? false : requireReview,
          /* 通知类模板的每个提醒时刻都存在 notice_times，界面上只有一个「执行时间」输入框，
             必须显式映射过去，否则改了时间不会真的改发送时刻（后端对该类模板忽略 remind_at）。 */
          ...(kind === 'notice'
            ? { notice_times: isManual ? [] : [remindAt.trim()], attach_image: Boolean(noticeImagePath), attach_image_path: noticeImagePath || null, attach_image_name: noticeImageName || null }
            : {}),
        });
        await refreshTemplates();
        setModalOpen(false);
        toast.success(
          variableValidation
            ? `已保存「${name.trim()}」及 ${variableValidation.config.rows.length} 人的变量表格。`
            : `任务模板「${name.trim()}」已更新保存（共关联 ${roleIds.length} 个业务角色）。`,
          '编辑已保存',
        );
      } else {
        const res = await api.post<{ replayed?: boolean; push?: { queued: boolean; message?: string; instance_count?: number } }>('/api/admin/templates', {
          name: name.trim(),
          kind: kind === 'notice' ? 'notice' : 'upload',
          period: 'day',
          role_id: selectedRoles[0],
          role_ids: selectedRoles,
          remind_at: isAdhoc || isManual ? '' : remindAt.trim(),
          is_adhoc: kind !== 'notice' && isAdhoc,
          ...manualConfig,
          deadline: finalDeadline,
          has_deadline: finalHasDeadline,
          deadline_type: finalDeadlineType,
          deadline_time: finalDeadlineTime,
          text_tpl: finalTextTpl,
          spec,
          enabled: isManual || enabled,
          ...(isManual ? { push_on_create: pushOnCreate, request_id: createRequestId.current } : {}),
          /* 2026-11-02：不再有周期概念，选哪几天就每周那几天触发；七天全选等价「每天」存 null */
          exec_weekdays: isAdhoc || isManual || execWeekdays.length === 7 ? null : execWeekdays,
          exec_dom_days: !isAdhoc && !isManual && execMode === 'monthly' ? execDomDays : [],
          need_review: kind === 'notice' ? false : requireReview,
          ...(kind === 'notice'
            ? { attach_image: Boolean(noticeImagePath), attach_image_path: noticeImagePath || null, attach_image_name: noticeImageName || null }
            : {}),
        });
        setModalOpen(false);
        if (res.replayed && isManual && pushOnCreate) {
          toast.success('此创建请求已处理，请在提醒日志核对推送状态。', '模板已保存');
        } else if (res.push?.queued && res.push.instance_count) {
          toast.success(`推送已受理，涉及 ${res.push.instance_count} 条任务实例。请在提醒日志查看结果。`, '已创建并提交推送');
        } else if (res.push?.queued) {
          toast.warning('暂无符合资格的成员。', '模板已保存');
        } else if (res.push) {
          toast.warning(res.push.message || '推送未启动，可在任务列表重新推送。', '模板已保存');
        } else {
          toast.success(`已创建模板「${name.trim()}」。`, '新建模板成功');
        }
        await refreshTemplates().catch(() => toast.warning('模板列表刷新失败，可刷新页面查看。'));
      }
    } catch (err) {
      if (err instanceof ApiRequestError && err.issues?.length) {
        setVariableResult(undefined);
        setVariableIssues(err.issues);
      }
      toast.error(err instanceof Error ? plainError(err.message) : '保存模板未成功');
    } finally {
      setSaving(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteConfirmTpl) return;
    const target = deleteConfirmTpl;
    setBusyId(target.id);
    try {
      await api.delete(`/api/admin/templates/${target.id}`);
      await refreshTemplates();
      setDeleteConfirmTpl(null);
      toast.success(`模板「${target.name}」已删除。`, '删除成功');
    } catch (err) {
      setDeleteConfirmTpl(null);
      toast.error(err instanceof Error ? plainError(err.message) : '删除模板失败');
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmPush = async () => {
    if (!pushConfirmTpl) return;
    const target = pushConfirmTpl;
    const filterCompleted = skipCompleted && !isBroadcastNotice(target);
    setPushingId(target.id);
    try {
      const res = await api.post<{
        queued?: boolean;
        needs_confirm?: boolean;
        target_count?: number;
        completed_count?: number;
        unfinished_count?: number;
        new_batch?: boolean;
        synced_completed?: number;
        instance_count?: number;
        pushed_at?: string;
        deadline_at?: string;
        stats?: { sent?: number; pushed_instances?: number };
      }>(`/api/admin/templates/${target.id}/push-now?skip_completed=${Number(filterCompleted)}`);
      if (res?.needs_confirm) {
        /* 2026-11-19 需求一：本周期已推送过 → 关掉首次确认框，改问「是否新建一批」。
           预检不写冷却 marker，用户取消也不影响后续操作。 */
        const { completed_count: completed, unfinished_count: unfinished } = res;
        const preview = completed !== undefined && unfinished !== undefined
          ? { total: completed + unfinished, completed, unfinished } : null;
        setPushConfirmTpl(null);
        setPushPreview(null);
        setRepushConfirm({ tpl: target, targetCount: res.target_count ?? 0, preview });
        return;
      }
      if (res?.queued) {
        toast.success(
          `当期已生成 ${res.instance_count ?? 0} 条任务实例，提醒消息已在后台逐条投递` +
            `（2600 人约需 2 分钟）。发送结果可在「提醒日志 / 任务实例」页核对。`,
          '推送已启动',
        );
      } else {
        const stats = res?.stats || {};
        toast.success(
          `已向 ${stats.pushed_instances ?? 0} 名待提交成员推送提醒（发送成功 ${stats.sent ?? 0} 条）。`,
          '推送完成',
        );
      }
      setPushConfirmTpl(null);
    } catch (err) {
      // 429 冷却（3 秒内已推过）等错误由服务端文案直接透出，不关弹窗让用户看清原因
      if (err instanceof ApiRequestError && err.issues?.length) setPushIssues(err.issues);
      toast.error(err instanceof Error ? plainError(err.message) : '推送未成功');
    } finally {
      setPushingId(null);
    }
  };

  /* 2026-11-19 需求一：第二次确认 —— 新建批次并向当前角色重推一遍 */
  const handleConfirmRepush = async () => {
    if (!repushConfirm) return;
    const target = repushConfirm.tpl;
    const broadcast = isBroadcastNotice(target);
    const filterCompleted = skipCompleted && !broadcast;
    setPushingId(target.id);
    try {
      const res = await api.post<{
        queued?: boolean;
        new_batch?: boolean;
        synced_completed?: number;
        instance_count?: number;
        period_key?: string;
        stats?: { sent?: number; pushed_instances?: number };
      }>(`/api/admin/templates/${target.id}/push-now?confirmed=1&skip_completed=${Number(filterCompleted)}`);
      if (res?.queued) {
        toast.success(
          broadcast ? `向 ${res.instance_count ?? 0} 人重推通知，消息正在后台投递。` :
            `已按归属周期再推 ${res.instance_count ?? 0} 条实例` +
            (filterCompleted ? '（本周期已完成/已回执成员已过滤）' : `（已同步本周期完成状态 ${res.synced_completed ?? 0} 条）`) +
            '；消息正在后台逐条投递，可在「提醒日志 / 任务实例」页核对。',
          '重推已启动',
        );
      } else {
        const stats = res?.stats || {};
        toast.success(
          `已重推 ${stats.pushed_instances ?? 0} 名成员（发送成功 ${stats.sent ?? 0} 条）。`,
          '重推完成',
        );
      }
      setRepushConfirm(null);
    } catch (err) {
      if (err instanceof ApiRequestError && err.issues?.length) setPushIssues(err.issues);
      toast.error(err instanceof Error ? plainError(err.message) : '重推未成功');
    } finally {
      setPushingId(null);
    }
  };

  const addFieldRow = () => {
    setFields((prev) => [
      ...prev,
      {
        key: `field_${prev.length + 1}`,
        label: '',
        type: 'number',
        required: true,
      },
    ]);
  };

  const updateFieldRow = (idx: number, patch: Partial<FormFieldSpec>) => {
    setFields((prev) => prev.map((f, i) => (i === idx ? { ...f, ...patch } : f)));
  };

  const removeFieldRow = (idx: number) => {
    setFields((prev) => prev.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-4">
      {/* 顶部标题与管理员视角（统一标准组件） */}
      <AdminScopeHeader
        title="任务模板管理"
        badge={`共 ${templates.length} 项任务`}
        description="支持「通知提醒」与「上传任务（发送提醒文本 + 专属 H5 上传链接）」两大类任务配置。"
        actions={
          !isSuperAdmin ? (
            <>
              <button
                type="button"
                onClick={() => openCreateModal('upload')}
                className="px-3.5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition-all"
              >
                <Plus className="w-4 h-4" />
                <span>新建上传任务</span>
              </button>
              <button
                type="button"
                onClick={() => openCreateModal('notice')}
                className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition-all"
              >
                <Plus className="w-4 h-4" />
                <span>新建通知提醒</span>
              </button>
            </>
          ) : null
        }
      />

      {/* 筛选栏 + 干净对齐的任务列表 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
        <div className="p-4 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5">

            {/* 按照角色选择 */}
            <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
              <Users className="w-3.5 h-3.5 text-slate-400" />
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="bg-transparent text-xs font-medium text-slate-700 outline-none cursor-pointer"
              >
                <option value="">全部业务角色</option>
                {roles.map((r) => (
                  <option key={r.role_id} value={r.role_id}>
                    {r.role_name}
                  </option>
                ))}
              </select>
            </div>

            {/* 按照任务类型选择 */}
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5 text-xs font-medium text-slate-700 outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
            >
              <option value="">全部任务类型</option>
              <option value="notice">通知提醒</option>
              <option value="upload">上传任务（H5链接反馈）</option>
            </select>

            {/* 关键词搜索 */}
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索任务名称或内容…"
              className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-52"
            />
          </div>

          <div className="text-xs text-slate-400">
            共 <span className="font-bold text-slate-700">{filteredTemplates.length}</span> 项任务
          </div>
        </div>

        {/* 任务模板列表 */}
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50/80 border-b border-slate-200/80 text-[11px] font-bold text-slate-500">
                <th className="py-3.5 px-4 w-48 whitespace-nowrap">任务名称</th>
                <th className="py-3.5 px-3 w-36 whitespace-nowrap">所属角色</th>
                <th className="py-3.5 px-3 w-32 whitespace-nowrap">任务类型</th>
                <th className="py-3.5 px-3 w-28 whitespace-nowrap">执行时间</th>
                <th className="py-3.5 px-3">任务内容配置</th>
                <th className="py-3.5 px-3 w-28 whitespace-nowrap">状态</th>
                <th className="py-3.5 px-4 w-36 text-right whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-xs">
              {filteredTemplates.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-400">
                    当前筛选条件下暂无任务模板。
                  </td>
                </tr>
              ) : (
                filteredTemplates.map((tpl) => {
                  const tplRoleIds = getTemplateRoleIds(tpl);
                  const isNotice = tpl.kind === 'notice';
                  const items = Array.isArray(tpl.spec?.notice_items) ? tpl.spec.notice_items : [];
                  const isExpanded = Boolean(expandedRows[tpl.id]);
                  const upModules = resolveUploadModules(tpl);

                    return (
                    <tr key={tpl.id} className="hover:bg-slate-50/60 transition-colors align-top">
                      {/* 1. 任务名称（2026-10-28 修复：移除 template_id 显示，消除文本遮挡） */}
                      <td className="py-4 px-4 whitespace-nowrap">
                        <div className="font-bold text-slate-900 text-xs">{tpl.name}</div>
                        {isSuperAdmin && tpl.owner_username && (
                          <div className="text-[10px] text-slate-400 mt-0.5">
                            创建人：{tpl.owner_display_name || tpl.owner_username}
                          </div>
                        )}
                      </td>

                      {/* 2. 所属角色（支持多角色展示） */}
                      <td className="py-4 px-3">
                        <div className="flex flex-wrap items-center gap-1.5 max-w-[240px]">
                          {tplRoleIds.length === 0 ? (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-500 font-semibold text-xs">
                              {tpl.spec?.variable_notice ? `变量表格 · ${tpl.spec.variable_notice.count} 人` : '未分配角色'}
                            </span>
                          ) : (
                            tplRoleIds.map((rid) => (
                              <span
                                key={rid}
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-700 font-semibold text-xs whitespace-nowrap"
                              >
                                <Users className="w-3.5 h-3.5 text-blue-600 shrink-0" />
                                <span>{roleMap[rid] || rid}</span>
                              </span>
                            ))
                          )}
                        </div>
                      </td>

                      {/* 3. 任务类型 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        {isNotice ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-amber-50 text-amber-700 border-amber-200 text-xs font-semibold">
                            <Bell className="w-3.5 h-3.5 shrink-0" />
                            <span>通知提醒</span>
                          </span>
                        ) : tpl.is_adhoc ? (
                          /* 2026-11-12 晚 · 不定期任务（菜单自选）：Pull 模式徽章 */
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-blue-50 text-blue-700 border-blue-200 text-xs font-semibold" title="成员在个人工作台自主上报">
                            <Smartphone className="w-3.5 h-3.5 shrink-0" />
                            <span>自主上报</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-blue-50 text-blue-700 border-blue-200 text-xs font-semibold">
                            <Smartphone className="w-3.5 h-3.5 shrink-0" />
                            <span>上传任务</span>
                          </span>
                        )}
                      </td>

                      {/* 4. 执行时间 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        {tpl.is_manual ? (
                          <span className="font-semibold text-blue-700">立即推送</span>
                        ) : tpl.is_adhoc && !isNotice ? (
                          /* 不定期任务无推送时刻（调度物理隔离） */
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-violet-50/80 text-violet-700 border border-violet-200/70 font-mono text-xs font-bold">
                            <Clock className="w-3.5 h-3.5 shrink-0" />
                            <span>自主发起</span>
                          </span>
                        ) : (
                          <div className="space-y-1.5">
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-blue-50/80 text-blue-700 border border-blue-200/70 font-mono text-xs font-bold">
                              <Clock className="w-3.5 h-3.5 shrink-0" />
                              <span>{(isNotice && tpl.notice_times?.join(' / ')) || tpl.remind_at || '—'}</span>
                            </span>
                            <div className="max-w-44 whitespace-normal text-xs font-medium text-slate-600">{describeTemplateSchedule(tpl)}</div>
                          </div>
                        )}
                      </td>

                      {/* 5. 任务内容配置 */}
                      <td className="py-4 px-3 max-w-xl">
                        {isNotice ? (
                          items.length > 0 ? (
                            <div className="space-y-2">
                              {items.map((item, index) => (
                                <div key={item.id || index} className="flex gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                                  <span className="shrink-0 font-semibold text-slate-600">{NOTICE_LABELS[item.format]}</span>
                                  {item.format === 'text' ? <span className={isExpanded ? 'whitespace-pre-line' : 'line-clamp-2'}>
                                    {item.text_content}
                                  </span> : <a href={noticeAssetUrl(item)} target="_blank" rel="noreferrer"
                                    className="min-w-0 truncate text-blue-700 hover:underline">{item.file_name || item.file_caption}</a>}
                                </div>
                              ))}
                              {tpl.spec?.notice_receipt?.enabled && <p className="text-xs text-slate-500">
                                {tpl.spec.notice_receipt.mode === 'link'
                                  ? `H5 链接确认 · ${tpl.spec.notice_receipt.button_label || DEFAULT_RECEIPT}`
                                  : `文本回复确认 · ${tpl.spec.notice_receipt.phrase || DEFAULT_RECEIPT}`}
                              </p>}

                              <button
                                type="button"
                                onClick={() => toggleExpandRow(tpl.id)}
                                className="text-[11px] text-blue-600 hover:text-blue-700 font-medium inline-flex items-center gap-1 cursor-pointer"
                              >
                                {isExpanded ? (
                                  <>
                                    <ChevronUp className="w-3.5 h-3.5" />
                                    <span>收起详情</span>
                                  </>
                                ) : (
                                  <>
                                    <ChevronDown className="w-3.5 h-3.5" />
                                    <span>展开详情</span>
                                  </>
                                )}
                              </button>
                            </div>
                          ) : (
                            <div className="text-slate-700 line-clamp-2 text-xs bg-slate-50 border border-slate-200/70 rounded-xl px-3.5 py-2.5">
                              {tpl.text_tpl}
                            </div>
                          )
                        ) : (
                          /* 上传任务展示：提醒文本 + H5 链接上传模块标签 */
                          <div className="bg-slate-50/90 border border-slate-200/80 rounded-xl px-3.5 py-2.5 space-y-2">
                            <div className="text-slate-700 text-xs leading-relaxed line-clamp-2">
                              <span className="font-semibold text-slate-900">提醒文本：</span>
                              {tpl.text_tpl}
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                              <span className="text-[11px] text-slate-500 font-medium">
                                H5 上传模块：
                              </span>
                              {upModules.map((m) => (
                                <span
                                  key={m}
                                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${
                                    UPLOAD_MODULE_META[m]?.color ||
                                    'bg-blue-50 text-blue-700 border-blue-200'
                                  }`}
                                >
                                  {UPLOAD_MODULE_META[m]?.icon}
                                  <span>{UPLOAD_MODULE_META[m]?.label || m}</span>
                                </span>
                              ))}
                              {/* 2026-11-12 下午（用户拍板）：截止展示统一**规则样式**——
                                  本月月底 17:00 / 本周日 17:00 / 今天 17:00 / 不限（与预览、H5 徽章一字不差） */}
                              {(() => {
                                const dlLabel = tpl.is_manual ? formatManualDeadline(tpl.manual_deadline_at) : formatDeadlineRuleLabel(
                                  tpl.has_deadline === false ? null : (tpl.deadline_type ?? 'same_day'),
                                  tpl.deadline_time || tpl.deadline || '',
                                );
                                return (
                                  <span className="text-[11px] font-mono text-slate-500 ml-1">
                                    截止 {dlLabel}
                                  </span>
                                );
                              })()}
                            </div>
                          </div>
                        )}
                      </td>

                      {/* 6. 状态（2026-11-05：超管对业务数据只读 —— 原「快速启停」是假入口，改纯徽章） */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        {isSuperAdmin ? (
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border whitespace-nowrap ${
                              tpl.enabled
                                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                : 'bg-slate-100 text-slate-500 border-slate-200'
                            }`}
                          >
                            {tpl.enabled ? (
                              <>
                                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                                <span>已启用</span>
                              </>
                            ) : (
                              <>
                                <PauseCircle className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                                <span>已停用</span>
                              </>
                            )}
                          </span>
                        ) : (
                        <button
                          onClick={() => handleToggleEnabled(tpl)}
                          disabled={busyId === tpl.id}
                          title="点击快速切换启用/停用状态"
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border cursor-pointer transition-all whitespace-nowrap ${
                            tpl.enabled
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                              : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200'
                          }`}
                        >
                          {tpl.enabled ? (
                            <>
                              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                              <span>已启用</span>
                            </>
                          ) : (
                            <>
                              <PauseCircle className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                              <span>已停用</span>
                            </>
                          )}
                        </button>
                        )}
                      </td>

                      {/* 7. 操作 */}
                      <td className="py-4 px-4 text-right whitespace-nowrap">
                        {isSuperAdmin ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-500 font-medium text-xs">
                            只读查看
                          </span>
                        ) : (
                          <div className="inline-flex items-center justify-end gap-2">
                            {(tpl.kind !== 'notice' || tpl.spec?.notice_receipt?.enabled) && (
                              <button
                                onClick={() => setStatsFocus(String(tpl.id))}
                                title="查看该任务的数据统计（进度 / 部门对比 / 明细 / Excel 导出）"
                                className="px-3 py-1.5 rounded-lg bg-violet-50 hover:bg-violet-100 text-violet-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                              >
                                <BarChart3 className="w-3.5 h-3.5 shrink-0" />
                                <span>统计</span>
                              </button>
                            )}
                            {(tpl.is_manual || (tpl.kind !== 'notice' && !tpl.is_adhoc)) && (
                              <button
                                onClick={() => setPushConfirmTpl(tpl)}
                                disabled={!tpl.enabled || busyId === tpl.id || pushingId === tpl.id}
                                title={
                                  tpl.enabled
                                    ? (tpl.is_manual ? '立即向当前符合资格的成员发送一次' : '手动推送当期提醒，同模板 2 分钟内不可重复推送')
                                    : '模板已停用，请先启用再推送'
                                }
                                className="px-3 py-1.5 rounded-lg bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                <Send className="w-3.5 h-3.5 shrink-0" />
                                <span>{pushingId === tpl.id ? '推送中…' : '推送'}</span>
                              </button>
                            )}
                            <button
                              onClick={() => openEditModal(tpl)}
                              className="px-3 py-1.5 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                            >
                              <Edit3 className="w-3.5 h-3.5 shrink-0" />
                              <span>编辑</span>
                            </button>
                            <button
                              onClick={() => setDeleteConfirmTpl(tpl)}
                              disabled={busyId === tpl.id}
                              className="px-3 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                            >
                              <Trash2 className="w-3.5 h-3.5 shrink-0" />
                              <span>删除</span>
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 新建 / 编辑模板弹窗（当选择「上传任务」时自动展开右侧手机端 H5 实时预览） */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4 overflow-y-auto">
          <div
            className={`bg-white rounded-2xl w-full p-6 shadow-2xl border border-slate-200 my-8 space-y-4 max-h-[92vh] overflow-y-auto transition-all ${
              hasPreview ? 'max-w-5xl' : 'max-w-2xl'
            }`}
          >
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-blue-600 text-white flex items-center justify-center">
                  <Sparkles className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    {editingTpl ? `编辑任务模板 · ${editingTpl.name}` : '新建任务模板'}
                  </h3>
                  <p className="text-[11px] text-slate-400">
                    {isManual ? '可在任务模板列表随时点击「推送」。'
                      : kind === 'notice'
                      ? '到达执行时间后，向角色成员发送通知。'
                      : isAdhoc ? '成员可在个人工作台随时发起上报。'
                        : '到达执行时间后，向接收成员发送提醒和专属 H5 提交链接。'}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setModalOpen(false)}
                className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveTemplate} className="space-y-4 text-xs">
              <div
                className={`grid grid-cols-1 ${
                  hasPreview ? 'lg:grid-cols-12 gap-6' : 'gap-4'
                } items-start`}
              >
                {/* 左侧表单配置区 */}
                <div className={hasPreview ? 'lg:col-span-7 space-y-4' : 'space-y-4'}>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                    <div className="sm:col-span-2">
                      <label className="font-semibold text-slate-700 block mb-1">
                        任务模板名称 <span className="text-rose-500">*</span>
                      </label>
                      <input
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder={
                          kind === 'notice'
                            ? '例如：晨会安排 / 上午业务点检、午间复盘'
                            : '例如：外出队伍晒图与意向单报数上传'
                        }
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 font-medium"
                      />
                    </div>

                    {/* 所属角色（支持多选） */}
                    {!variableEnabled && <div className="sm:col-span-2">
                      <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5">
                        <label className="font-semibold text-slate-700 flex items-center gap-1.5">
                          <span>所属业务角色（支持多选）</span>
                          <span className="text-rose-500">*</span>
                          <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-bold">
                            {roleIds.length ? `已选 ${roleIds.length} / ${roles.length} 个角色` : '未绑定角色，保存后暂不派发'}
                          </span>
                        </label>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={selectAllRoles}
                            className="px-2.5 py-1 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 font-semibold text-[11px] cursor-pointer transition-colors"
                          >
                            全选全部角色
                          </button>
                          <button
                            type="button"
                            onClick={selectSingleDefaultRole}
                            className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium text-[11px] cursor-pointer transition-colors"
                          >
                            重置为单角色
                          </button>
                        </div>
                      </div>
                      <div
                        data-testid="template-role-multiselect"
                        className="grid grid-cols-2 sm:grid-cols-3 gap-2 p-2.5 rounded-2xl bg-slate-50 border border-slate-200/90"
                      >
                        {roles.map((r) => {
                          const selected = roleIds.includes(r.role_id);
                          return (
                            <button
                              key={r.role_id}
                              type="button"
                              data-role-id={r.role_id}
                              onClick={() => toggleRoleSelection(r.role_id)}
                              className={`px-3 py-2 rounded-xl border text-left transition-all cursor-pointer flex items-center justify-between gap-2 ${
                                selected
                                  ? 'bg-blue-50/95 border-blue-500 text-blue-900 shadow-2xs'
                                  : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-100/80'
                              }`}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                <span
                                  className={`w-4 h-4 rounded flex items-center justify-center shrink-0 transition-colors ${
                                    selected
                                      ? 'bg-blue-600 text-white'
                                      : 'border border-slate-300 bg-white'
                                  }`}
                                >
                                  {selected && <Check className="w-3 h-3" />}
                                </span>
                                <span className="font-semibold text-xs truncate">
                                  {r.role_name}
                                </span>
                              </div>
                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                                  selected
                                    ? 'bg-blue-100/80 text-blue-700 font-bold'
                                    : 'bg-slate-100 text-slate-400'
                                }`}
                              >
                                {r.active_count ?? r.member_count ?? 0}人
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>}

                    {/* 任务类型切换 */}
                    <div>
                      <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                        <span>任务类型</span>
                        {editingTpl && (
                          <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.2 rounded border border-amber-200">
                            <Lock className="w-2.5 h-2.5" /> 不可修改
                          </span>
                        )}
                      </label>
                      <select
                        value={kind}
                        disabled={Boolean(editingTpl)}
                          onChange={(e) => {
                          setKind(e.target.value as 'notice' | 'upload');
                          if (e.target.value !== 'notice') resetVariableDraft(false);
                          if (e.target.value === 'notice') setIsAdhoc(false);
                        }}
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 disabled:opacity-65 disabled:cursor-not-allowed font-semibold"
                      >
                        <option value="notice">通知提醒</option>
                        <option value="upload">上传任务</option>
                      </select>
                    </div>

                    <label className="font-semibold text-slate-700 sm:col-span-2">
                      推送方式
                      <select value={isManual ? 'manual' : 'scheduled'} disabled={Boolean(editingTpl)}
                        onChange={(event) => {
                          setIsManual(event.target.value === 'manual');
                          if (event.target.value === 'manual') setIsAdhoc(false);
                          else {
                            setPushOnCreate(false);
                            resetVariableDraft(false);
                          }
                        }}
                        className="mt-1 block w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-slate-800 disabled:opacity-70">
                        <option value="scheduled">定时推送</option>
                        <option value="manual">一次性立即推送</option>
                      </select>
                    </label>
                    {kind === 'notice' && isManual && (
                      <label className="flex cursor-pointer items-center gap-2 font-semibold text-slate-700 sm:col-span-2">
                        <input type="checkbox" className="accent-blue-700" checked={hasVariableTable}
                          onChange={event => {
                            resetVariableDraft(event.target.checked);
                            if (event.target.checked) setNoticeKinds(current => current.includes('text') ? current : [...current, 'text']);
                          }} />
                        启用变量表格（千人千面）
                      </label>
                    )}

                    {kind === 'upload' && !isManual && (
                      <div className="sm:col-span-2 -mt-1">
                        <label
                          className={`inline-flex items-start gap-2 rounded-xl border px-3 py-2 cursor-pointer transition-colors ${
                            isAdhoc
                              ? 'bg-blue-50 border-blue-300'
                              : 'bg-slate-50 border-slate-200 hover:border-slate-300'
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={isAdhoc}
                            onChange={(e) => setIsAdhoc(e.target.checked)}
                            className="w-4 h-4 mt-0.5 rounded text-violet-600 focus:ring-violet-500"
                          />
                          <span className="text-xs leading-5">
                            <span className="font-bold text-slate-800">不定期任务（自主上报）</span>
                            <span className="text-slate-500 block">
                              成员从个人工作台的「自主上报」进入，随时填报并可重复提交。
                            </span>
                          </span>
                        </label>
                      </div>
                    )}

                    {/* 2026-11-02 用户口径：去掉「执行周期」概念 —— 管理员只选星期几，固定在每周选中的那几天触发 */}
                    {/* 2026-11-10 双模式：每周（周几）/ 每月（几号）；月底=31（调度自动适配小月/闰年） */}
                    {!isAdhoc && !isManual && <>
                    <div>
                      <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                        <span>执行日</span>
                        <span className="text-[10px] text-slate-400 font-normal">
                          {execMode === 'weekly'
                              ? '只在这些星期生成任务；没选的日子不提醒、不产生逾期'
                              : '只在每月选中的日期生成任务；选中 31 号时小月/闰月自动按当月最后一天执行'}
                        </span>
                      </label>
                      {/* 模式切换 */}
                      <div className="inline-flex rounded-lg border border-slate-200 overflow-hidden mb-2">
                        <button
                          type="button"
                          onClick={() => setExecMode('weekly')}
                          className={`px-3.5 py-1.5 text-xs font-semibold cursor-pointer transition-colors ${
                            execMode === 'weekly'
                              ? 'bg-blue-600 text-white'
                              : 'bg-white text-slate-600 hover:bg-slate-50'
                          }`}
                        >
                          每周执行
                        </button>
                        <button
                          type="button"
                          onClick={() => setExecMode('monthly')}
                          className={`px-3.5 py-1.5 text-xs font-semibold cursor-pointer transition-colors ${
                            execMode === 'monthly'
                              ? 'bg-blue-600 text-white'
                              : 'bg-white text-slate-600 hover:bg-slate-50'
                          }`}
                        >
                          每月执行
                        </button>
                      </div>

                      {execMode === 'weekly' ? (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {WEEKDAY_LABELS.map((label, i) => {
                          const d = i + 1;
                          const on = execWeekdays.includes(d);
                          return (
                            <button
                              key={d}
                              type="button"
                              onClick={() => toggleExecWeekday(d)}
                              className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors cursor-pointer ${
                                on
                                  ? 'bg-blue-600 text-white border-blue-600'
                                  : 'bg-slate-50 text-slate-600 border-slate-200 hover:border-blue-300'
                              }`}
                            >
                              {label}
                            </button>
                          );
                        })}
                        <button
                          type="button"
                          onClick={() => setExecWeekdays([1, 2, 3, 4, 5])}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-slate-500 bg-white border border-slate-200 hover:border-blue-300 cursor-pointer"
                        >
                          工作日
                        </button>
                        <button
                          type="button"
                          onClick={() => setExecWeekdays([1, 2, 3, 4, 5, 6, 7])}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-slate-500 bg-white border border-slate-200 hover:border-blue-300 cursor-pointer"
                        >
                          每天
                        </button>
                        <span className="text-[11px] text-slate-500 ml-1">
                          {describeExecWeekdays(execWeekdays)}
                        </span>
                      </div>
                      ) : (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => {
                          const on = execDomDays.includes(d);
                          return (
                            <button
                              key={d}
                              type="button"
                              onClick={() => toggleExecDomDay(d)}
                              className={`w-8 py-1.5 rounded-lg text-xs font-semibold border transition-colors cursor-pointer tabular-nums ${
                                on
                                  ? 'bg-blue-600 text-white border-blue-600'
                                  : 'bg-slate-50 text-slate-600 border-slate-200 hover:border-blue-300'
                              }`}
                            >
                              {d}
                            </button>
                          );
                        })}
                        <button
                          type="button"
                          onClick={() => setExecDomDays([31])}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-slate-500 bg-white border border-slate-200 hover:border-blue-300 cursor-pointer"
                          title="每月最后一天（2月28/29日、小月30日、大月31日自动适配）"
                        >
                          每月月底
                        </button>
                        <span className="text-[11px] text-slate-500 ml-1 w-full">
                          {execDomDays.length === 0
                            ? '尚未选择'
                            : `每月 ${execDomDays.map((d) => (d === 31 ? '31（含月底）' : String(d))).join('、')} 号执行`}
                        </span>
                      </div>
                      )}
                    </div>

                    {/* 执行时间 & 截止时间（2026-10-28 重排版：时间字段并列一行 + 原生时间选择器） */}
                    <div className={kind === 'notice' ? 'sm:col-span-2' : ''}>
                      <label className="font-semibold text-slate-700 block mb-1">
                        执行时间 <span className="text-rose-500">*</span>
                      </label>
                      <input
                        type="time"
                        value={remindAt}
                        onChange={(e) => setRemindAt(e.target.value)}
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 font-mono text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                      />
                    </div>

                    {/* 截止配置（2026-11-12 任务E1 重构：开关 + 类型单选 + 时刻） */}
                    {kind === 'upload' && (
                      <div className="sm:col-span-2">
                        <label className="font-semibold text-slate-700 block mb-1.5">
                          截止配置 <span className="text-[11px] font-normal text-slate-400">（不设截止时任务长期有效）</span>
                        </label>
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="inline-flex items-center gap-1.5 cursor-pointer select-none bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
                            <input
                              type="checkbox"
                              checked={hasDeadline}
                              onChange={(e) => setHasDeadline(e.target.checked)}
                              className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500"
                            />
                            <span className="font-semibold text-slate-800 text-xs">设置截止时间</span>
                          </label>
                          {hasDeadline && (
                            <>
                              <div className="inline-flex rounded-xl bg-white p-0.5 border border-slate-200 text-[11px]">
                                <button
                                  type="button"
                                  onClick={() => setDeadlineType('same_day')}
                                  className={`px-2.5 py-1.5 rounded-lg font-semibold cursor-pointer transition-all ${
                                    deadlineType === 'same_day'
                                      ? 'bg-blue-600 text-white shadow-2xs'
                                      : 'text-slate-600 hover:text-slate-900'
                                  }`}
                                >
                                  当天截止
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setDeadlineType('weekend')}
                                  className={`px-2.5 py-1.5 rounded-lg font-semibold cursor-pointer transition-all ${
                                    deadlineType === 'weekend'
                                      ? 'bg-blue-600 text-white shadow-2xs'
                                      : 'text-slate-600 hover:text-slate-900'
                                  }`}
                                >
                                  本周日截止
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setDeadlineType('month_end')}
                                  className={`px-2.5 py-1.5 rounded-lg font-semibold cursor-pointer transition-all ${
                                    deadlineType === 'month_end'
                                      ? 'bg-blue-600 text-white shadow-2xs'
                                      : 'text-slate-600 hover:text-slate-900'
                                  }`}
                                >
                                  本月月底截止
                                </button>
                              </div>
                              <input
                                type="time"
                                aria-label="截止时刻"
                                value={deadlineTime}
                                onChange={(e) => setDeadlineTime(e.target.value)}
                                className="w-28 bg-slate-50 border border-slate-200 rounded-xl p-2 font-mono text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                              />
                            </>
                          )}
                        </div>
                        <div className="text-[11px] text-slate-500 mt-1.5">
                          {hasDeadline
                            ? '到截止时刻后 H5 提交入口关闭，逾期后统一提示「当前任务已过期，无法提交」。'
                            : '无截止时间：任务长期有效，提醒与 H5 页面显示「截止 不限」。'}
                        </div>
                      </div>
                    )}

                    </>}
                    {isManual && kind === 'upload' && (
                      <label className="font-semibold text-slate-700 sm:col-span-2">
                        截止日期和时刻（留空不限）
                        <input type="datetime-local" value={manualDeadline} onChange={(event) => setManualDeadline(event.target.value)}
                          className="mt-1 block w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 font-mono text-slate-800" />
                      </label>
                    )}
                    <div className="sm:col-span-2 pt-1">
                      <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={isManual && !editingTpl ? pushOnCreate : enabled}
                          onChange={(e) => isManual && !editingTpl
                            ? setPushOnCreate(e.target.checked) : setEnabled(e.target.checked)}
                          className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500"
                        />
                        <span className="font-semibold text-slate-800">{isAdhoc ? '在个人工作台开放自主上报'
                          : isManual ? (editingTpl ? '启用模板' : '创建后立即推送') : '立即启用自动调度'}</span>
                      </label>
                      {isManual && !editingTpl && <p className="mt-1 text-slate-500">关闭时只保存，可稍后在任务模板列表推送。</p>}
                    </div>
                  </div>

                  {/* ==================== 分支 A：纯通知提醒配置 ==================== */}
                  {kind === 'notice' && (
                    <div className="space-y-4 pt-1">
                      <fieldset className="space-y-2">
                        <legend className="font-bold text-slate-800">推送内容 <span className="text-rose-500">*</span></legend>
                        <div className="flex flex-wrap gap-2">
                          {NOTICE_KINDS.map(key => <label key={key}
                            className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                            <input type="checkbox" className="accent-blue-700" checked={noticeKinds.includes(key)}
                              disabled={variableEnabled && key === 'text'}
                              onChange={event => setNoticeKinds(current => event.target.checked
                                ? [...current, key] : current.filter(value => value !== key))} />
                            {NOTICE_LABELS[key]}
                          </label>)}
                        </div>
                      </fieldset>
                      {noticeKinds.includes('text') && <label className="block space-y-2 font-semibold">
                        <span>通知正文</span>
                        <textarea rows={4} value={noticeText} onChange={event => setNoticeText(event.target.value)}
                          placeholder={variableEnabled ? '{姓名}您好，您本月得分 {得分} 分' : undefined}
                          className="w-full rounded-xl border border-slate-200 bg-white p-3 font-normal outline-none focus:border-blue-500" />
                      </label>}
                      {variableEnabled && <VariableNoticeFields body={noticeText} issues={variableIssues}
                        validation={variableValidation} busy={variableBusy} onUpload={file => validateVariableDraft(noticeText, file)} />}
                      {variableEnabled && variableResult && !variableValidation && (
                        <p className="text-xs text-amber-800">正文已修改，请重新导入表格校验。</p>
                      )}
                      {(Object.keys(NOTICE_UPLOADS) as NoticeAttachmentKind[]).filter(key => noticeKinds.includes(key)).map(key =>
                        <fieldset key={key} className="space-y-2 rounded-xl border border-slate-200 p-3">
                          <legend className="px-1 font-semibold">{NOTICE_LABELS[key]}附件</legend>
                          <input type="file" accept={NOTICE_UPLOADS[key].accept} multiple disabled={uploadingAssetId !== null}
                            aria-label={`选择${NOTICE_LABELS[key]}（可多选）`}
                            className="block w-full cursor-pointer rounded-md text-xs file:mr-3 file:cursor-pointer file:border-0 file:bg-blue-50 file:px-3 file:py-2 file:text-blue-700"
                            onChange={event => {
                              void handleNoticeUpload(key, Array.from(event.target.files || []));
                              event.currentTarget.value = '';
                            }} />
                          <p className="text-xs text-slate-500">{uploadingAssetId === key ? '上传中…' : `可多选并追加，每个最多 ${NOTICE_UPLOADS[key].max} MB`}</p>
                          {noticeItems.filter(item => item.format === key).map(item =>
                            <div key={item.id} className="flex items-center gap-2 rounded-lg bg-slate-50 px-2 py-1 text-xs">
                              <a href={noticeAssetUrl(item)} target="_blank" rel="noreferrer"
                                className="min-w-0 flex-1 truncate text-blue-700 hover:underline" title={item.file_name}>{item.file_name}</a>
                              <span className="shrink-0 tabular-nums text-slate-500">{noticeAssetSize(item.file_size)}</span>
                              <button type="button" onClick={() => setNoticeItems(previous => previous.filter(value => value.id !== item.id))}
                                aria-label={`移除 ${item.file_name || NOTICE_LABELS[key]}`}
                                className="cursor-pointer px-2 py-1.5 text-rose-700 hover:bg-rose-50">移除</button>
                            </div>)}
                        </fieldset>)}
                      <div className="space-y-3 rounded-xl border border-slate-200 p-3">
                        <label className="flex cursor-pointer items-center gap-2 font-semibold">
                          <input type="checkbox" className="accent-blue-700" checked={receiptEnabled}
                            onChange={event => setReceiptEnabled(event.target.checked)} />
                          需要成员回执确认
                        </label>
                        {receiptEnabled && <>
                          <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
                            <legend className="mb-1 font-medium">回执方式</legend>
                            <label className="flex items-center gap-2">
                              <input type="radio" name="notice-receipt-mode" className="accent-blue-700" checked={receiptMode === 'link'}
                                onChange={() => setReceiptMode('link')} /> H5 链接确认
                            </label>
                            <label className="flex items-center gap-2">
                              <input type="radio" name="notice-receipt-mode" className="accent-blue-700" checked={receiptMode === 'text'}
                                onChange={() => setReceiptMode('text')} /> 文本回复确认
                            </label>
                          </fieldset>
                          {receiptMode === 'link'
                            ? <>
                              <label className="block space-y-2">
                                <span>确认按钮文案</span>
                                <input type="text" maxLength={80} value={receiptButtonLabel} onChange={event => setReceiptButtonLabel(event.target.value)}
                                  className="w-full rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-blue-500" />
                              </label>
                              <label className="block space-y-2">
                                <span>链接引导语（选填）</span>
                                <input type="text" maxLength={120} value={receiptGuideText} onChange={event => setReceiptGuideText(event.target.value)}
                                  placeholder="留空默认：请打开链接阅读完整通知，点击「我已知晓」确认。"
                                  className="w-full rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-blue-500" />
                              </label>
                            </>
                            : <label className="block space-y-2">
                              <span>要求回复的话术</span>
                              <input type="text" maxLength={80} value={receiptPhrase} onChange={event => setReceiptPhrase(event.target.value)}
                                className="w-full rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-blue-500" />
                            </label>}
                        </>}
                      </div>
                    </div>
                  )}

                  {/* ==================== 分支 B：统一上传任务配置 ==================== */}
                  {kind === 'upload' && (
                    <div className="space-y-4">
                      {/* 步骤1：发送给用户的提醒文本（系统自动附上 H5 链接） */}
                      {!isAdhoc && <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/90 space-y-2">
                        <label className="font-bold text-slate-800 flex items-center justify-between">
                          <span>发送给用户的提醒文本（自动附上专属 H5 链接） *</span>
                          <span className="text-[10px] font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded border border-blue-200">
                            自动追加 H5 链接
                          </span>
                        </label>
                        <textarea
                          rows={3}
                          value={uploadRemindText}
                          onChange={(e) => setUploadRemindText(e.target.value)}
                          placeholder="请输入下发给用户的提醒文案，系统将在消息底部自动附上专属 H5 上传链接…"
                          className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:border-blue-500 leading-relaxed"
                        />
                      </div>}

                      {/* 步骤2：选择 H5 链接中包含的上传组件（字段报数 / 文本 / 图片 / 文件） */}
                      <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/90 space-y-3">
                        <div className="font-bold text-slate-800">
                          选择需要员工提交的内容类型（可多选） *
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          {(['fields', 'text', 'image', 'file'] as UploadH5Module[]).map((mod) => {
                            const active = uploadModules.includes(mod);
                            const meta = UPLOAD_MODULE_META[mod];
                            return (
                              <button
                                key={mod}
                                type="button"
                                onClick={() => toggleUploadModule(mod)}
                                className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between gap-1.5 ${
                                  active
                                    ? 'bg-blue-50/90 border-blue-500 text-blue-900 shadow-2xs'
                                    : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-100'
                                }`}
                              >
                                <div className="flex items-center justify-between w-full">
                                  <span className="font-bold text-xs flex items-center gap-1">
                                    {meta.icon}
                                    <span>{meta.shortLabel}</span>
                                  </span>
                                  <span
                                    className={`w-4 h-4 rounded flex items-center justify-center ${
                                      active
                                        ? 'bg-blue-600 text-white'
                                        : 'border border-slate-300 bg-white'
                                    }`}
                                  >
                                    {active && <Check className="w-3 h-3" />}
                                  </span>
                                </div>
                                <span className="text-[10px] text-slate-400 line-clamp-1">
                                  {meta.label}
                                </span>
                              </button>
                            );
                          })}
                        </div>

                        <div className="flex flex-wrap gap-x-5 gap-y-2">
                          {uploadModules.includes('image') && <label className="inline-flex items-center gap-2 text-xs text-slate-700">
                            <input type="checkbox" checked={multipleImages} onChange={(event) => setMultipleImages(event.target.checked)} />
                            允许上传多张图片（最多 10 张）
                          </label>}
                          {uploadModules.includes('file') && <label className="inline-flex items-center gap-2 text-xs text-slate-700">
                            <input type="checkbox" checked={multipleFiles} onChange={(event) => setMultipleFiles(event.target.checked)} />
                            允许上传多个文件（最多 10 个）
                          </label>}
                        </div>

                        {/* 若勾选了「字段（报数类型）」，展示报数字段配置 */}
                        {uploadModules.includes('fields') && (
                          <div className="p-3 rounded-xl bg-white border border-slate-200 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="font-bold text-slate-800 text-[11px]">
                                配置报数字段列表
                              </span>
                              <button
                                type="button"
                                onClick={addFieldRow}
                                className="px-2 py-1 rounded-lg bg-blue-50 text-blue-700 font-semibold text-[11px] cursor-pointer"
                              >
                                + 添加报数字段
                              </button>
                            </div>
                            <div className="space-y-1.5">
                              {fields.map((f, idx) => (
                                <div key={idx} className="flex items-center gap-2">
                                  <input
                                    type="text"
                                    value={f.label}
                                    onChange={(e) => updateFieldRow(idx, { label: e.target.value })}
                                    placeholder="字段名称，如：今日累计上门户数"
                                    className="flex-1 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-[11px]"
                                  />
                                  <select
                                    value={f.type}
                                    onChange={(e) =>
                                      updateFieldRow(idx, {
                                        type: e.target.value as FormFieldSpec['type'],
                                      })
                                    }
                                    className="bg-slate-50 border border-slate-200 rounded-lg px-2 py-1.5 text-[11px]"
                                  >
                                    <option value="number">数字报数</option>
                                    <option value="text">单行文本</option>
                                    <option value="boolean">布尔（是／否）</option>
                                    {f.type === 'select' && <option value="select">下拉选择</option>}
                                  </select>
                                  {fields.length > 1 && (
                                    <button
                                      type="button"
                                      onClick={() => removeFieldRow(idx)}
                                      className="p-1 text-rose-500 hover:bg-rose-50 rounded-lg cursor-pointer"
                                    >
                                      <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* 步骤3：仅保留「是否需要审核」选项 */}
                      <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/90 flex items-center justify-between">
                        <div className="font-bold text-slate-800 flex items-center gap-1.5">
                          <ShieldCheck className="w-4 h-4 text-blue-600" />
                          <span>上传后是否需要审核</span>
                        </div>
                        <div className="inline-flex rounded-xl bg-white p-0.5 border border-slate-200">
                          <button
                            type="button"
                            onClick={() => setRequireReview(true)}
                            className={`px-3 py-1 rounded-lg text-[11px] font-semibold cursor-pointer transition-all ${
                              requireReview
                                ? 'bg-blue-600 text-white shadow-2xs'
                                : 'text-slate-600 hover:text-slate-900'
                            }`}
                          >
                            需要审核
                          </button>
                          <button
                            type="button"
                            onClick={() => setRequireReview(false)}
                            className={`px-3 py-1 rounded-lg text-[11px] font-semibold cursor-pointer transition-all ${
                              !requireReview
                                ? 'bg-emerald-600 text-white shadow-2xs'
                                : 'text-slate-600 hover:text-slate-900'
                            }`}
                          >
                            无需审核
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* 右侧：当选择「上传任务」时实时展示手机端预览（1. 密信提醒 + 2. H5上传页） */}
                {kind === 'upload' && (
                  <div className="lg:col-span-5">
                    <UploadFlowPhonePreview
                      isAdhoc={isAdhoc}
                      isManual={isManual}
                      taskName={name}
                      remindAt={remindAt}
                      deadline={isManual ? manualDeadline : hasDeadline ? deadlineTime : ''}
                      deadlineType={hasDeadline ? deadlineType : null}
                      remindText={uploadRemindText}
                      modules={uploadModules}
                      fields={fields}
                      textLabel={textLabel}
                      textPlaceholder={textPlaceholder}
                    />
                  </div>
                )}
                {kind === 'notice' && (platform === 'zdxlz' || variableEnabled) && (
                  <div className="space-y-2 lg:col-span-5">
                    {variableValidation && <label className="flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-700">
                      专属消息预览
                      <select value={sampleIndex} onChange={event => setSampleIndex(Number(event.target.value))}
                        className="rounded-lg border border-slate-200 bg-white px-2 py-1.5">
                        {variableValidation.samples.map((sample, index) => <option key={sample.phone} value={index}>
                          {sample.name}（尾号 {sample.phone.slice(-4)}）
                        </option>)}
                      </select>
                    </label>}
                    <NoticePhonePreview
                      body={variableEnabled ? variableValidation?.samples[sampleIndex]?.body || '' : noticeKinds.includes('text') ? noticeText : ''}
                      media={noticeItems.filter(item => noticeKinds.includes(item.format))}
                      mode={receiptMode}
                      label={receiptEnabled ? (receiptMode === 'link' ? receiptButtonLabel : receiptPhrase).trim() : ''}
                      guide={receiptEnabled && receiptMode === 'link' ? receiptGuideText : ''}
                      title={name} />
                  </div>
                )}
              </div>

              <div className="pt-2 border-t border-slate-100 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={saving || uploadingAssetId !== null || (variableEnabled && !variableValidation)}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
                >
                  {saving ? '正在保存…' : editingTpl ? '保存修改' : isManual && pushOnCreate ? '创建并推送' : '确认创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {deleteConfirmTpl && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">确认删除任务模板</h3>
              <button
                onClick={() => setDeleteConfirmTpl(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed">
              确定要删除模板「<span className="font-bold text-slate-900">{deleteConfirmTpl.name}</span>
              」吗？若该模板已生成过历史任务实例，系统将自动拦截删除以保护数据（此时建议将模板设为「停用」）。
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setDeleteConfirmTpl(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={busyId === deleteConfirmTpl.id}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold text-xs cursor-pointer"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}

      {pushConfirmTpl && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">确认立即推送提醒</h3>
              <button
                onClick={() => setPushConfirmTpl(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            {isBroadcastNotice(pushConfirmTpl) ? <p className="text-xs text-slate-600 leading-relaxed" role="status">
              {broadcastAudience(pushConfirmTpl, pushPreview?.total)}
            </p> : pushConfirmTpl.is_manual ? <p className="text-xs text-slate-600 leading-relaxed">
              将立即向模板「{pushConfirmTpl.name}」中符合资格且尚未推送的成员发送{pushConfirmTpl.kind === 'notice' ? '通知' : '填报提醒'}。
            </p> : <p className="text-xs text-slate-600 leading-relaxed">
              将立即向模板「
              <span className="font-bold text-slate-900">{pushConfirmTpl.name}</span>
              」当前<b>待提交</b>的成员各推送一条提醒（内容与定时提醒一致）。
              <br />
              此操作<b>不影响</b>当天已定的定时提醒，两条各发各的；同模板 3 秒内不可重复推送。
            </p>}
            {!isBroadcastNotice(pushConfirmTpl) && (
              <PushSkipCompleted checked={skipCompleted} onChange={setSkipCompleted} preview={pushPreview} />
            )}
            <VariableNoticeIssues issues={pushIssues} />
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setPushConfirmTpl(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={handleConfirmPush}
                disabled={pushingId === pushConfirmTpl.id || Boolean(pushIssues.length)}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-semibold text-xs cursor-pointer disabled:opacity-60"
              >
                {pushingId === pushConfirmTpl.id ? '推送中…' : '确认推送'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 本周期已推送过时，按当前过滤选项确认重推范围。 */}
      {repushConfirm && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">本周期已推送过，仍需再推一次？</h3>
              <button
                onClick={() => setRepushConfirm(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            {isBroadcastNotice(repushConfirm.tpl) ? <p className="text-xs text-slate-600 leading-relaxed" role="status">
              {broadcastAudience(repushConfirm.tpl, repushPreview?.total ?? repushConfirm.targetCount)}
            </p> : <>
              <RepushDescription tpl={repushConfirm.tpl} checked={skipCompleted} preview={repushPreview} />
              <PushSkipCompleted checked={skipCompleted} onChange={setSkipCompleted} preview={repushPreview} />
            </>}
            <VariableNoticeIssues issues={pushIssues} />
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setRepushConfirm(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={handleConfirmRepush}
                disabled={pushingId === repushConfirm.tpl.id || Boolean(pushIssues.length)}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-xl font-semibold text-xs cursor-pointer disabled:opacity-60"
              >
                {pushingId === repushConfirm.tpl.id ? '重推中…' : '确认重推'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

function RepushDescription({ tpl, checked, preview }: { tpl: TemplateItem; checked: boolean; preview: PushPreview | null }) {
  const audience = tpl.spec?.variable_notice ? '变量表格中' : '当前角色';
  const notice = tpl.kind === 'notice';
  const recipients = checked ? preview?.unfinished : preview?.total;
  return (
    <p className="text-xs text-slate-600 leading-relaxed">
      {checked ? `本次将仅向${audience}未完成的` : `本次将向${audience}全部`}
      {recipients === undefined ? '成员' : <> <strong>{recipients} 人</strong> </>}
      {notice ? '再次发送本周期通知。' : '再次推送本周期任务。'}
      <br />
      （{checked ? <>
        已完成/已回执{preview ? `的 ${preview.completed} 人` : '的成员'}不打扰；
        {!notice && '已过截止成员若收到，链接仅供只读回顾；'}
      </> : notice ? '已回执成员照常收到通知，回执记录保留；' :
        '已完成/已回执与已过截止的成员照常收到消息，但链接仅供只读回顾，不会回到待办；未完成成员正常提交；'}
      待办与统计按本周期自动去重。）
    </p>
  );
}

/* 2026-11-21 需求二：推送确认弹窗的「过滤本周期已完成/已回执成员」开关。
   勾选 = 只向本周期未完成人员推送（已完成/已回执不打扰）；实时展示人数。 */
function PushSkipCompleted({ checked, onChange, preview }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  preview: PushPreview | null;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5 space-y-1">
      <label className="flex items-start gap-2 text-xs font-medium text-slate-700 cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={event => onChange(event.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600"
        />
        <span>过滤本周期已完成 / 已回执成员（仅向未完成人员推送）</span>
      </label>
      <p className="pl-6 text-[11px] leading-relaxed text-slate-500" role="status">
        {preview
          ? (checked
            ? `将向未完成 ${preview.unfinished} 人推送；本周期已完成/已回执 ${preview.completed} 人不打扰（共 ${preview.total} 人）。`
            : `将向全部 ${preview.total} 人照推（其中已完成/已回执 ${preview.completed} 人，链接只读回顾）。`)
          : '正在统计本周期完成情况…'}
      </p>
    </div>
  );
}
