import React, { useMemo, useState } from 'react';
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
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { AdminScopeHeader } from './AdminScopeHeader';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import {
  FormFieldSpec,
  NoticeContentFormat,
  NoticeMessageItem,
  TemplateItem,
  UploadH5Module,
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

function createDefaultNoticeItems(): NoticeMessageItem[] {
  return [
    {
      id: `ni_${Date.now()}_1`,
      format: 'text',
      text_content: '一、文字提醒：\n请按时完成本时段重点工作安排与点检。',
    },
    {
      id: `ni_${Date.now()}_2`,
      format: 'file',
      /* 2026-10-29：不再预填假文件名/假描述（「支局1+1155…截图.png」「二、数据提醒：…」）。
         上传后自动带出真实原始文件名；描述是**必填**（保存时校验），所以这里留空等管理员填。 */
      file_name: '',
      file_caption: '',
    },
  ];
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
  const [step, setStep] = useState<'im' | 'h5'>('h5');
  /* 截止时间展示：统一规则样式（本月月底 17:00 / 本周日 17:00 / 今天 17:00 / 不限），
   与实发消息里后端解析出的具体时间戳互补——预览层按用户选择的规则展示。 */
  const deadlineText = deadline ? formatDeadlineRuleLabel(deadlineType, deadline) : '不限';

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
        <div className="inline-flex rounded-xl bg-white p-0.5 border border-indigo-200/80 text-[11px]">
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
        </div>
      </div>

      {/* 手机屏幕容器 */}
      <div className="bg-slate-100 rounded-2xl overflow-hidden text-slate-800 border-2 border-indigo-200/90 shadow-inner min-h-[410px] flex flex-col">
        {/* 手机顶部状态栏 */}
        <div className="bg-white px-4 py-2.5 border-b border-slate-200 flex items-center justify-between text-[11px]">
          <span className="font-mono font-bold text-slate-700">{remindAt || '16:30'}</span>
          <span className="font-bold text-slate-900 truncate max-w-[180px]">
            {step === 'im' ? '办公软件 · 任务助手' : displayTitle}
          </span>
          <span className="text-[10px] text-blue-600 font-semibold">H5 专属通道</span>
        </div>

        {/* 步骤1：办公软件聊天界面收到提醒文本 + H5链接 */}
        {step === 'im' && (
          <div className="p-4 flex-1 flex flex-col justify-between space-y-4 bg-slate-100">
            <div className="space-y-3">
              <div className="text-center">
                <span className="px-2 py-0.5 rounded bg-slate-200/80 text-slate-500 text-[10px] font-mono">
                  执行时间 {remindAt || '16:30'}
                </span>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="bg-white rounded-2xl rounded-tl-xs p-3.5 shadow-xs border border-slate-200/80 max-w-[92%]">
                  {/* 2026-10-28：预览按实际推送的纯文本渲染（密信不支持富文本卡片/超链接），
                      与实际发送内容一字不差，避免「预览一套、实发一套」。 */}
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
        )}

        {/* 步骤2：H5 上传页面（根据勾选的 字段/文本/图片/文件 动态合成） */}
        {step === 'h5' && (
          <div className="p-3.5 flex-1 overflow-y-auto space-y-3 bg-slate-50">
            {/* H5 顶部任务说明卡片 */}
            <div className="bg-white p-3 rounded-xl border border-slate-200/90 space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-bold text-slate-900 text-xs">{displayTitle}</span>
                <span className="text-[10px] font-mono text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                  截止 {deadline ? deadlineText : '不限'}
                </span>
              </div>
              {/* 2026-10-28：按业务方要求，这里不放提醒正文，改为「提交人 · 周期 · 状态」 */}
              <div className="text-[11px] text-slate-500">
                提交人：张三 · 周期：{new Date().toISOString().slice(0, 10)} · 状态：<span className="text-slate-700 font-semibold">待上传</span>
              </div>
            </div>

            {modules.length === 0 ? (
              <div className="p-6 text-center text-xs text-slate-400 bg-white rounded-xl border border-dashed border-slate-200">
                请在左侧勾选至少一种 H5 上传组件（字段 / 文本 / 图片 / 文件）
              </div>
            ) : (
              <div className="space-y-2.5">
                {/* 模块 A：字段（报数类型） */}
                {modules.includes('fields') && (
                  <div className="bg-white p-3 rounded-xl border border-slate-200/90 space-y-2">
                    <div className="flex items-center gap-1.5 text-xs font-bold text-slate-800">
                      <Hash className="w-3.5 h-3.5 text-blue-600" />
                      <span>数据报数填报</span>
                    </div>
                    <div className="space-y-2">
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
                          <label className="text-[11px] font-medium text-slate-700 flex items-center justify-between">
                            <span>
                              {f.label || `报数项 #${i + 1}`}
                              {f.required && <span className="text-rose-500 ml-0.5">*</span>}
                            </span>
                            <span className="text-[10px] text-slate-400">
                              {f.type === 'number' ? '数值报数' : f.type === 'select' ? '下拉选择' : '文本'}
                            </span>
                          </label>
                          {f.type === 'select' ? (
                            <select className="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-[11px] text-slate-700">
                              {(f.options && f.options.length > 0 ? f.options : ['已完成', '进行中']).map(
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
                              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-[11px] text-slate-700"
                            />
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 模块 B：文本反馈 */}
                {modules.includes('text') && (
                  <div className="bg-white p-3 rounded-xl border border-slate-200/90 space-y-1.5">
                    <div className="flex items-center gap-1.5 text-xs font-bold text-slate-800">
                      <AlignLeft className="w-3.5 h-3.5 text-emerald-600" />
                      <span>{textLabel.trim() || '文字情况反馈'}</span>
                    </div>
                    <textarea
                      rows={2}
                      readOnly
                      placeholder={
                        textPlaceholder.trim() || '请输入今日重点执行情况、未破零帮扶进度或补充说明…'
                      }
                      className="w-full bg-slate-50 border border-slate-200 rounded-lg p-2 text-[11px] text-slate-600 resize-none"
                    />
                  </div>
                )}

                {/* 模块 C：图片上传 */}
                {modules.includes('image') && (
                  <div className="bg-white p-3 rounded-xl border border-slate-200/90 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 text-xs font-bold text-slate-800">
                        <ImageIcon className="w-3.5 h-3.5 text-indigo-600" />
                        <span>图片上传</span>
                      </span>
                      <span className="text-[10px] text-slate-400">JPG / PNG / WEBP</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="h-16 w-full rounded-lg border-2 border-dashed border-indigo-200 bg-indigo-50/50 flex flex-col items-center justify-center text-center gap-1 text-indigo-600">
                        <ImageIcon className="w-4 h-4 shrink-0" />
                        <span className="text-[10px] font-medium leading-none whitespace-nowrap">+ 点击选择图片</span>
                      </div>
                      <div className="h-16 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center text-[10px] text-slate-400">
                        示例晒图1.jpg
                      </div>
                    </div>
                  </div>
                )}

                {/* 模块 D：文件上传 */}
                {modules.includes('file') && (
                  <div className="bg-white p-3 rounded-xl border border-slate-200/90 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 text-xs font-bold text-slate-800">
                        <Paperclip className="w-3.5 h-3.5 text-violet-600" />
                        <span>文件上传</span>
                      </span>
                      <span className="text-[10px] text-slate-400">Excel / PDF / Word</span>
                    </div>
                    <div className="p-2.5 rounded-lg border border-dashed border-violet-200 bg-violet-50/40 flex items-center justify-center gap-1.5 text-violet-700 text-[11px] font-medium">
                      <Upload className="w-3.5 h-3.5" />
                      <span>点击选择文档并上传</span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* H5 底部提交按钮 */}
            <div className="pt-1">
              <button
                type="button"
                className="w-full py-2.5 rounded-xl bg-blue-600 text-white font-bold text-xs shadow-sm flex items-center justify-center gap-1.5 cursor-default"
              >
                <Send className="w-3.5 h-3.5" />
                <span>提交上传</span>
              </button>
            </div>
          </div>
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
  /* 2026-11-12 任务D：一键推送（创建者分级管理员专属测试按钮；超管只读不显示） */
  const [pushingId, setPushingId] = useState<number | null>(null);
  const [pushConfirmTpl, setPushConfirmTpl] = useState<TemplateItem | null>(null);

  // 弹窗表单状态（任务类型简化为两大类：'notice' 纯通知提醒 | 'upload' 上传任务）
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'notice' | 'upload'>('notice');
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
  const [noticeItems, setNoticeItems] = useState<NoticeMessageItem[]>(createDefaultNoticeItems);
  /** 正在上传数据图片的通知项下标（用于禁用按钮 + 显示上传中） */
  const [uploadingAssetId, setUploadingAssetId] = useState<number | null>(null);

  // 上传任务专用状态（提醒文本 + 选择 H5 上传项 [字段/文本/图片/文件] + 是否需要审核）
  const [uploadRemindText, setUploadRemindText] = useState(
    '请按时点击下方 H5 链接，完成本时段业务数据报数及现场图片/文件上传。',
  );
  const [uploadModules, setUploadModules] = useState<UploadH5Module[]>(['fields', 'image', 'text']);
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
    setEditingTpl(null);
    setName('');
    setKind(defaultKind);
    setIsAdhoc(false);
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
    setNoticeItems(createDefaultNoticeItems());
    setUploadRemindText(
      '请按时点击下方 H5 链接，完成今日上门走访户数报数及图片上传。',
    );
    setUploadModules(['fields', 'image', 'text']);
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
    setEditingTpl(tpl);
    setName(tpl.name);
    setKind(tpl.kind === 'notice' ? 'notice' : 'upload');
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
    setRequireReview(tpl.kind === 'notice' ? false : tpl.spec?.require_review !== false);
    setEnabled(tpl.enabled);

    if (tpl.kind === 'notice') {
      if (Array.isArray(tpl.spec?.notice_items) && tpl.spec.notice_items.length > 0) {
        const items = tpl.spec.notice_items.map((item) => ({ ...item }));
        /* 2026-10-29 H5.5：老模板可能只有顶层 attach_image_name（或只有 spec 里的 file_caption），
           两处都回填到 file 项的 file_caption，保证编辑框能显示已存的图片名称 */
        const savedName = String(tpl.attach_image_name ?? '').trim();
        if (savedName) {
          for (const it of items) {
            if (it.format === 'file' && !String(it.file_caption ?? '').trim()) {
              it.file_caption = savedName;
            }
          }
        }
        setNoticeItems(items);
      } else {
        setNoticeItems([
          {
            id: `ni_${tpl.id}_1`,
            format: 'text',
            text_content: tpl.text_tpl || '',
          },
        ]);
      }
    } else {
      setUploadRemindText(tpl.text_tpl || '');
      setUploadModules(resolveUploadModules(tpl));
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

  // 添加一条同时下发的提醒子任务
  const addNoticeItem = (defaultFormat: NoticeContentFormat = 'text') => {
    setNoticeItems((prev) => [
      ...prev,
      {
        id: `ni_${Date.now()}_${prev.length + 1}`,
        format: defaultFormat,
        text_content: defaultFormat === 'text' ? '' : undefined,
        file_name: defaultFormat === 'file' ? '' : undefined,
        file_caption: defaultFormat === 'file' ? '' : undefined,
      },
    ]);
  };

  const updateNoticeItem = (idx: number, patch: Partial<NoticeMessageItem>) => {
    setNoticeItems((prev) => prev.map((item, i) => (i === idx ? { ...item, ...patch } : item)));
  };

  const removeNoticeItem = (idx: number) => {
    setNoticeItems((prev) => prev.filter((_, i) => i !== idx));
  };

  /* 2026-10-28：数据图片提醒改为「真上传」——图片先落到服务端 notice_attach/，
     保存模板时把 image_path 一起提交，到点由调度器直接推送这张图。 */
  const handleNoticeImageUpload = async (idx: number, file: File | null) => {
    if (!file) return;
    const okExt = /\.(png|jpg|jpeg|webp)$/i.test(file.name);
    if (!okExt) {
      toast.warning('数据图片提醒只支持 png / jpg / jpeg / webp 图片', '文件类型不支持');
      return;
    }
    if (file.size > 9 * 1024 * 1024) {
      toast.warning('图片不能超过 9MB，请压缩后再上传', '图片过大');
      return;
    }
    setUploadingAssetId(idx);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('读取本地文件失败'));
        reader.readAsDataURL(file);
      });
      const res = await api.post<{ ok: boolean; path: string; file_name: string; size: number }>(
        '/api/admin/template-assets',
        { filename: file.name, content_base64: dataUrl },
      );
      updateNoticeItem(idx, {
        image_path: res.path,
        file_name: res.file_name || file.name,
        file_size: res.size || file.size,
        file_mime: file.type,
      });
      toast.success(`已上传「${res.file_name || file.name}」，到点会随提醒一起推送。`, '图片已就绪');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '图片上传失败');
    } finally {
      setUploadingAssetId(null);
    }
  };

  const handleToggleEnabled = async (tpl: TemplateItem) => {
    setBusyId(tpl.id);
    try {
      const nextEnabled = !tpl.enabled;
      const tplRoleIds = getTemplateRoleIds(tpl);
      await api.put(`/api/admin/templates/${tpl.id}`, {
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

  const handleSaveTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      toast.warning('请填写任务模板名称');
      return;
    }
    if (roleIds.length === 0) {
      toast.warning('请至少选择 1 个所属业务角色');
      return;
    }
    if (!isAdhoc && !remindAt.trim()) {
      toast.warning('请填写执行时间');
      return;
    }
    /* 2026-10-29 执行日：必须至少选一天，否则永远不会执行（2026-11-02 起周期固定按周）*/
    /* 2026-11-10 月度模式：切换为「每月执行」时必须至少选一个日期 */
    if (!isAdhoc && execMode === 'weekly' && execWeekdays.length === 0) {
      toast.warning('请至少选择一天「执行日」', '缺少执行日');
      return;
    }
    if (!isAdhoc && execMode === 'monthly' && execDomDays.length === 0) {
      toast.warning('每月执行至少选择一个日期（或点「每月月底」快捷）', '缺少执行日');
      return;
    }

    let finalTextTpl = '';
    let spec: Record<string, any> = {};

    if (kind === 'notice') {
      if (noticeItems.length === 0) {
        toast.warning('请至少保留 1 条提醒内容');
        return;
      }
      for (let i = 0; i < noticeItems.length; i++) {
        const item = noticeItems[i];
        if (item.format === 'text' && !item.text_content?.trim()) {
          toast.warning(`请填写第 ${i + 1} 条文本提醒内容`);
          return;
        }
        if (item.format === 'file' && !item.image_path) {
          toast.warning(
            `第 ${i + 1} 条勾选了「数据图片提醒」，请先上传要推送的图片`,
            '缺少提醒图片',
          );
          return;
        }
        /* 2026-10-29：图片名称是**必填**——没有名称，H5 图文页就没有标题可显示。
           只在保存时用弹窗提醒（不写死在界面上的红字提示）。 */
        if (item.format === 'file' && item.image_path && !item.file_caption?.trim()) {
          toast.warning(
            `第 ${i + 1} 条已上传图片，请填写「图片名称（描述）」`,
            '缺少图片名称',
          );
          return;
        }
      }
      finalTextTpl = summarizeNoticeItemsToText(noticeItems);
      spec = {
        require_review: false,
        notice_items: noticeItems.map((ni) => ({
          ...ni,
          text_content: ni.text_content?.trim(),
          file_name: ni.file_name?.trim(),
          file_caption: ni.file_caption?.trim(),
        })),
      };
    } else {
      // 统一上传任务校验（2026-11-12 任务E1：截止配置改为 开关+类型+时刻）
      if (hasDeadline && !deadlineTime.trim()) {
        toast.warning('请填写截止时刻');
        return;
      }
      if (!uploadRemindText.trim()) {
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
      finalTextTpl = uploadRemindText.trim();
      spec = {
        require_review: requireReview,
        upload_modules: uploadModules,
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

    /* 通知类：取第一条已上传的「数据图片提醒」图片，作为到点直接推送的附件 */
    const noticeImagePath =
      noticeItems.find((ni) => ni.format === 'file' && ni.image_path)?.image_path || '';
    /* 2026-10-29 H5.5：图片名称（描述）——发送直链文案与 H5 页标题用 */
    const noticeImageName =
      noticeItems.find((ni) => ni.format === 'file' && ni.image_path)?.file_caption?.trim() || '';

    setSaving(true);
    try {
      /* 2026-11-12 任务E1：截止三件套随保存提交——
         无截止 → has_deadline=false（deadline/deadline_type/deadline_time 全空）；
         有截止 → deadline_type ∈ {same_day, weekend, month_end} + deadline_time（HH:MM）。
         旧列 deadline 同步存 deadline_time，保证未升级的读取链路一致。 */
      const finalDeadline = kind === 'notice' || !hasDeadline ? '' : deadlineTime.trim();
      const finalHasDeadline = kind !== 'notice' && hasDeadline && !isAdhoc;
      const finalDeadlineType = finalHasDeadline ? deadlineType : null;
      const finalDeadlineTime = finalHasDeadline ? deadlineTime.trim() : null;
      if (editingTpl) {
        await api.put(`/api/admin/templates/${editingTpl.id}`, {
          name: name.trim(),
          role_id: roleIds[0],
          role_ids: roleIds,
          remind_at: isAdhoc ? '' : remindAt.trim(),
          is_adhoc: kind !== 'notice' && isAdhoc,
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
          exec_weekdays: isAdhoc ? null : execWeekdays.length === 7 ? null : execWeekdays,
          exec_dom_days: isAdhoc ? [] : execMode === 'monthly' ? execDomDays : [],
          /* 2026-10-28：审核开关以前只塞进 spec.require_review，后端读的是顶层 need_review，
             导致「需要审核」永远不生效、提交后直接变已完成。这里显式带上。 */
          need_review: kind === 'notice' ? false : requireReview,
          /* 通知类模板的每个提醒时刻都存在 notice_times，界面上只有一个「执行时间」输入框，
             必须显式映射过去，否则改了时间不会真的改发送时刻（后端对该类模板忽略 remind_at）。 */
          ...(kind === 'notice'
            ? { notice_times: [remindAt.trim()], attach_image: Boolean(noticeImagePath), attach_image_path: noticeImagePath || null, attach_image_name: noticeImageName || null }
            : {}),
        });
        await refreshTemplates();
        setModalOpen(false);
        toast.success(
          `任务模板「${name.trim()}」已更新保存（共关联 ${roleIds.length} 个业务角色）。`,
          '编辑已保存',
        );
      } else {
        await api.post('/api/admin/templates', {
          name: name.trim(),
          kind: kind === 'notice' ? 'notice' : 'upload',
          period: 'day',
          role_id: roleIds[0],
          role_ids: roleIds,
          remind_at: isAdhoc ? '' : remindAt.trim(),
          is_adhoc: kind !== 'notice' && isAdhoc,
          deadline: finalDeadline,
          has_deadline: finalHasDeadline,
          deadline_type: finalDeadlineType,
          deadline_time: finalDeadlineTime,
          text_tpl: finalTextTpl,
          spec,
          enabled,
          /* 2026-11-02：不再有周期概念，选哪几天就每周那几天触发；七天全选等价「每天」存 null */
          exec_weekdays: isAdhoc ? null : execWeekdays.length === 7 ? null : execWeekdays,
          exec_dom_days: isAdhoc ? [] : execMode === 'monthly' ? execDomDays : [],
          need_review: kind === 'notice' ? false : requireReview,
          ...(kind === 'notice'
            ? { attach_image: Boolean(noticeImagePath), attach_image_path: noticeImagePath || null, attach_image_name: noticeImageName || null }
            : {}),
        });
        await refreshTemplates();
        setModalOpen(false);
        toast.success(
          `已成功创建新模板「${name.trim()}」（已关联 ${roleIds.length} 个业务角色）。`,
          '新建模板成功',
        );
      }
    } catch (err) {
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

  /* 2026-11-12 任务D：一键推送确认动作——立即向当前「待提交」成员各发一条提醒
     （与当期定时提醒互不影响、内容同版式；服务端另有 2 分钟冷却防手抖 + 审计）。
     2026-11-13 修复一：2600 人推送改为「请求内建实例 + 后台投递」——接口立即返回 queued，
     不再同步等待 108 秒（避免浏览器/网关超时与事件循环冻结）。 */
  const handleConfirmPush = async () => {
    if (!pushConfirmTpl) return;
    const target = pushConfirmTpl;
    setPushingId(target.id);
    try {
      const res = await api.post<{
        queued?: boolean;
        instance_count?: number;
        pushed_at?: string;
        deadline_at?: string;
        stats?: { sent?: number; pushed_instances?: number };
      }>(`/api/admin/templates/${target.id}/push-now`);
      if (res?.queued) {
        toast.success(
          `当期已生成 ${res.instance_count ?? 0} 条任务实例，提醒消息已在后台逐条投递` +
            `（2600 人约需 2 分钟）。发送结果可在「提醒日志 / 任务实例」页核对。`,
          '一键推送已启动',
        );
      } else {
        const stats = res?.stats || {};
        toast.success(
          `已向 ${stats.pushed_instances ?? 0} 名待提交成员推送提醒（发送成功 ${stats.sent ?? 0} 条）。`,
          '一键推送完成',
        );
      }
      setPushConfirmTpl(null);
    } catch (err) {
      // 429 冷却（2 分钟内已推过）等错误由服务端文案直接透出，不关弹窗让用户看清原因
      toast.error(err instanceof Error ? plainError(err.message) : '推送未成功');
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
                              未分配角色
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
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-violet-50 text-violet-700 border-violet-200 text-xs font-semibold" title="无推送时刻/无截止，成员回复【菜单】自主获取链接">
                            <Smartphone className="w-3.5 h-3.5 shrink-0" />
                            <span>不定期（菜单自选）</span>
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
                        {tpl.is_adhoc && !isNotice ? (
                          /* 不定期任务无推送时刻（调度物理隔离） */
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-violet-50/80 text-violet-700 border border-violet-200/70 font-mono text-xs font-bold">
                            <Clock className="w-3.5 h-3.5 shrink-0" />
                            <span>—（菜单自选）</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-blue-50/80 text-blue-700 border border-blue-200/70 font-mono text-xs font-bold">
                            <Clock className="w-3.5 h-3.5 shrink-0" />
                            <span>{tpl.remind_at || '—'}</span>
                          </span>
                        )}
                      </td>

                      {/* 5. 任务内容配置 */}
                      <td className="py-4 px-3 max-w-xl">
                        {isNotice ? (
                          items.length > 0 ? (
                            <div className="space-y-2">
                              {items.map((ni, idx) => (
                                <div
                                  key={ni.id || idx}
                                  className="bg-slate-50/90 border border-slate-200/80 rounded-xl px-3.5 py-2.5 text-xs"
                                >
                                  <div className="flex items-start gap-2.5">
                                    <span
                                      className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border whitespace-nowrap shrink-0 mt-0.5 ${
                                        ni.format === 'file'
                                          ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
                                          : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                      }`}
                                    >
                                      {ni.format === 'file' ? '文件/图片' : '文本'}
                                    </span>

                                    <div className="min-w-0 flex-1">
                                      {ni.format === 'text' ? (
                                        <div
                                          className={`text-slate-700 leading-relaxed ${
                                            isExpanded ? 'whitespace-pre-line' : 'line-clamp-2'
                                          }`}
                                        >
                                          {ni.text_content}
                                        </div>
                                      ) : ni.image_path ? (
                                        <div className="space-y-2">
                                          {/* 文件/附件标题胶囊 */}
                                          <div className="flex flex-wrap items-center gap-2">
                                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-white border border-slate-200 rounded-lg text-[11px] font-semibold text-slate-700 shadow-2xs">
                                              <ImageIcon className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
                                              <span className="truncate max-w-[280px]">
                                                {ni.file_name || '0815_支局1+1155核心指标完成率与沉淀商机通报.png'}
                                              </span>
                                            </span>
                                            <a
                                              href={`/api/admin/template-assets/${encodeURIComponent(
                                                String(ni.image_path).split('/').pop() || '',
                                              )}`}
                                              target="_blank"
                                              rel="noopener noreferrer"
                                              className="text-[11px] text-blue-600 hover:text-blue-700 inline-flex items-center gap-1 font-medium hover:underline"
                                            >
                                              <ExternalLink className="w-3 h-3" />
                                              <span>查看原图</span>
                                            </a>
                                          </div>

                                          {/* 预览缩略图与文字说明上下/图文分栏 */}
                                          <div className="flex flex-col sm:flex-row items-start gap-3">
                                            <a
                                              href={`/api/admin/template-assets/${encodeURIComponent(
                                                String(ni.image_path).split('/').pop() || '',
                                              )}`}
                                              target="_blank"
                                              rel="noopener noreferrer"
                                              className="relative group rounded-xl overflow-hidden border border-slate-200 hover:border-indigo-400 shrink-0 shadow-2xs transition-all block bg-slate-900"
                                              title="点击查看高清图表"
                                            >
                                              <img
                                                src={`/api/admin/template-assets/${encodeURIComponent(
                                                  String(ni.image_path).split('/').pop() || '',
                                                )}`}
                                                alt={ni.file_name || '数据通报图'}
                                                className="w-36 h-20 object-cover group-hover:scale-105 transition-transform"
                                              />
                                              <div className="absolute inset-0 bg-slate-900/30 group-hover:bg-transparent transition-colors flex items-center justify-center">
                                                <span className="opacity-0 group-hover:opacity-100 bg-black/70 text-white text-[10px] px-2 py-0.5 rounded backdrop-blur-xs transition-opacity font-medium">
                                                  放大
                                                </span>
                                              </div>
                                            </a>

                                            {ni.file_caption ? (
                                              <div className="flex-1 min-w-0 bg-white border border-slate-200/80 rounded-xl p-2.5 text-xs text-slate-700 leading-relaxed whitespace-pre-line">
                                                {ni.file_caption}
                                              </div>
                                            ) : null}
                                          </div>
                                        </div>
                                      ) : (
                                        <div className="text-[11px] text-rose-600 font-semibold">
                                          未上传图片（编辑模板后可上传）
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              ))}

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
                                const dlLabel = formatDeadlineRuleLabel(
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
                            {/* 2026-11-12 任务D：一键推送（上传任务专属；notice 按时刻自动发）。 */}
                            {tpl.kind !== 'notice' && (
                              <button
                                onClick={() => setPushConfirmTpl(tpl)}
                                disabled={!tpl.enabled || busyId === tpl.id || pushingId === tpl.id}
                                title={
                                  tpl.enabled
                                    ? '立即向待提交成员推送一条提醒（测试用；不影响当期定时提醒，同模板 2 分钟内不可重复推送）'
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
              kind === 'upload' ? 'max-w-5xl' : 'max-w-2xl'
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
                    {kind === 'notice'
                      ? '纯通知提醒模式：到点向角色成员发送文本或文件/图片通知'
                      : '上传任务模式：发送提醒文本 + 自动附上专属 H5 链接（按所选字段/文本/图片/文件合成）'}
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
                  kind === 'upload' ? 'lg:grid-cols-12 gap-6' : 'gap-4'
                } items-start`}
              >
                {/* 左侧表单配置区 */}
                <div className={kind === 'upload' ? 'lg:col-span-7 space-y-4' : 'space-y-4'}>
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
                    <div className="sm:col-span-2">
                      <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5">
                        <label className="font-semibold text-slate-700 flex items-center gap-1.5">
                          <span>所属业务角色（支持多选）</span>
                          <span className="text-rose-500">*</span>
                          <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-bold">
                            已选 {roleIds.length} / {roles.length} 个角色
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
                    </div>

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
                        }}
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 disabled:opacity-65 disabled:cursor-not-allowed font-semibold"
                      >
                        <option value="notice">通知提醒</option>
                        <option value="upload">上传任务（提醒文本 + H5链接上传）</option>
                      </select>
                    </div>

                    {/* 2026-11-12 晚 · 不定期任务（菜单自选）：Pull 模式开关（仅上传任务可勾） */}
                    {kind === 'upload' && (
                      <div className="sm:col-span-2 -mt-1">
                        <label
                          className={`inline-flex items-start gap-2 rounded-xl border px-3 py-2 cursor-pointer transition-colors ${
                            isAdhoc
                              ? 'bg-violet-50 border-violet-300'
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
                            <span className="font-bold text-slate-800">不定期任务（菜单自选）</span>
                            <span className="text-slate-500 block">
                              不定时推送催报：无推送时间、无截止时间，成员私聊回复【菜单】自主获取一次性填报链接，
                              随到随报、提交后链接自动作废、可再次上报。勾选后「执行日 / 执行时间 / 截止配置」自动禁用。
                            </span>
                          </span>
                        </label>
                      </div>
                    )}

                    {/* 2026-11-02 用户口径：去掉「执行周期」概念 —— 管理员只选星期几，固定在每周选中的那几天触发 */}
                    {/* 2026-11-10 双模式：每周（周几）/ 每月（几号）；月底=31（调度自动适配小月/闰年） */}
                    <div className={isAdhoc ? 'opacity-45 pointer-events-none select-none' : ''}>
                      <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                        <span>执行日</span>
                        <span className="text-[10px] text-slate-400 font-normal">
                          {isAdhoc
                            ? '不定期任务无执行日（不推送）'
                            : execMode === 'weekly'
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
                    <div className={(kind === 'notice' ? 'sm:col-span-2 ' : '') + (isAdhoc ? 'opacity-45 pointer-events-none select-none' : '')}>
                      <label className="font-semibold text-slate-700 block mb-1">
                        执行时间 {isAdhoc ? <span className="text-[10px] text-violet-600 font-normal">（不定期任务无推送时刻）</span> : <span className="text-rose-500">*</span>}
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
                      <div className={'sm:col-span-2 ' + (isAdhoc ? 'opacity-45 pointer-events-none select-none' : '')}>
                        <label className="font-semibold text-slate-700 block mb-1.5">
                          截止配置 <span className="text-[11px] font-normal text-slate-400">（不设截止 = 任务长期有效；不定期任务无截止概念）</span>
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

                    {/* 立即启用自动调度（重排版：独占一行，与上方字段左对齐） */}
                    <div className="sm:col-span-2 pt-1">
                      <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={(e) => setEnabled(e.target.checked)}
                          className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500"
                        />
                        <span className="font-semibold text-slate-800">立即启用自动调度</span>
                      </label>
                    </div>
                  </div>

                  {/* ==================== 分支 A：纯通知提醒配置 ==================== */}
                  {kind === 'notice' && (
                    <div className="space-y-3 pt-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <label className="font-bold text-slate-800 flex items-center gap-1.5">
                          <Layers className="w-4 h-4 text-blue-600" />
                          <span>办公软件提醒内容配置（支持同时间多条并列下发）</span>
                          <span className="text-rose-500">*</span>
                        </label>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => addNoticeItem('text')}
                            className="px-2.5 py-1.5 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 font-semibold text-[11px] inline-flex items-center gap-1 cursor-pointer"
                          >
                            <Plus className="w-3.5 h-3.5" />
                            <span>新增文本提醒</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => addNoticeItem('file')}
                            className="px-2.5 py-1.5 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 font-semibold text-[11px] inline-flex items-center gap-1 cursor-pointer"
                          >
                            <Plus className="w-3.5 h-3.5" />
                            <span>新增文件/图片提醒</span>
                          </button>
                        </div>
                      </div>

                      <div className="space-y-3">
                        {noticeItems.map((item, idx) => (
                          <div
                            key={item.id || idx}
                            className="p-3.5 rounded-2xl bg-slate-50/90 border border-slate-200/90 space-y-3"
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200/70 pb-2.5">
                              <div className="flex items-center gap-2">
                                <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 font-bold text-[11px]">
                                  第 {idx + 1} 次提醒
                                </span>
                                <div className="inline-flex rounded-xl bg-white p-0.5 border border-slate-200">
                                  <button
                                    type="button"
                                    onClick={() => updateNoticeItem(idx, { format: 'text' })}
                                    className={`px-3 py-1 rounded-lg text-[11px] font-semibold inline-flex items-center gap-1 cursor-pointer transition-all ${
                                      item.format === 'text'
                                        ? 'bg-blue-600 text-white shadow-2xs'
                                        : 'text-slate-600 hover:text-slate-900'
                                    }`}
                                  >
                                    <FileText className="w-3 h-3" />
                                    <span>文本</span>
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => updateNoticeItem(idx, { format: 'file' })}
                                    className={`px-3 py-1 rounded-lg text-[11px] font-semibold inline-flex items-center gap-1 cursor-pointer transition-all ${
                                      item.format === 'file'
                                        ? 'bg-indigo-600 text-white shadow-2xs'
                                        : 'text-slate-600 hover:text-slate-900'
                                    }`}
                                  >
                                    <ImageIcon className="w-3 h-3" />
                                    <span>文件（含图片）</span>
                                  </button>
                                </div>
                              </div>

                              {noticeItems.length > 1 && (
                                <button
                                  type="button"
                                  onClick={() => removeNoticeItem(idx)}
                                  className="px-2 py-1 rounded-lg text-rose-600 hover:bg-rose-50 font-medium text-[11px] inline-flex items-center gap-1 cursor-pointer"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                  <span>移除</span>
                                </button>
                              )}
                            </div>

                            {item.format === 'text' ? (
                              <textarea
                                rows={4}
                                value={item.text_content || ''}
                                onChange={(e) =>
                                  updateNoticeItem(idx, { text_content: e.target.value })
                                }
                                placeholder="请输入提醒事项......"
                                className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:border-blue-500 leading-relaxed"
                              />
                            ) : (
                              <div className="space-y-2.5">
                                {/* 2026-10-28：数据图片提醒只需要一张图，到点直接推图，不再填名称/描述 */}
                                <div className="flex flex-wrap items-center gap-2">
                                  <label
                                    className={`px-3.5 py-2 rounded-xl font-semibold text-[11px] inline-flex items-center justify-center gap-1.5 shrink-0 ${
                                      uploadingAssetId === idx
                                        ? 'bg-slate-300 text-white cursor-wait'
                                        : 'bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer'
                                    }`}
                                  >
                                    <Upload className="w-3.5 h-3.5" />
                                    <span>{uploadingAssetId === idx ? '上传中…' : item.image_path ? '重新上传图片' : '上传数据提醒图片'}</span>
                                    <input
                                      type="file"
                                      accept="image/png,image/jpeg,image/webp"
                                      className="hidden"
                                      disabled={uploadingAssetId === idx}
                                      onChange={(e) => {
                                        void handleNoticeImageUpload(idx, e.target.files?.[0] || null);
                                        e.currentTarget.value = '';
                                      }}
                                    />
                                  </label>
                                  {item.image_path ? (
                                    <span className="inline-flex items-center gap-1 text-indigo-700 font-medium text-[11px]">
                                      <Paperclip className="w-3.5 h-3.5 shrink-0" />
                                      <span className="truncate max-w-[220px]">{item.file_name || item.image_path}</span>
                                      <button
                                        type="button"
                                        onClick={() => updateNoticeItem(idx, { image_path: undefined, file_name: undefined, file_size: undefined })}
                                        className="text-rose-600 hover:bg-rose-50 rounded px-1 cursor-pointer"
                                      >
                                        移除
                                      </button>
                                    </span>
                                  ) : (
                                    <span className="text-rose-600 text-[11px] font-semibold">未上传图片 —— 保存前必须先上传</span>
                                  )}
                                </div>
                                <div className="text-[11px] text-slate-500 leading-relaxed">
                                  到点会把这张图随提醒发给成员（企业微信直接发图片消息，量子密信发可点开的图片链接），请填写图片名称（如「上午上门情况统计图」），成员看到的就是这张图。
                                </div>
                                {item.image_path ? (
                                  <div className="space-y-1.5">
                                    <label className="block text-[11px] font-semibold text-slate-600">
                                      图片名称（描述）
                                      <span className="text-rose-600 ml-0.5">*</span>
                                    </label>
                                    <input
                                      type="text"
                                      value={item.file_caption || ''}
                                      onChange={(e) => updateNoticeItem(idx, { file_caption: e.target.value })}
                                      placeholder="如：上午上门情况统计图"
                                      className="w-full bg-white border border-slate-200 rounded-xl px-2.5 py-2 text-slate-800 outline-none focus:border-blue-500 text-sm"
                                    />
                                  </div>
                                ) : null}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* ==================== 分支 B：统一上传任务配置 ==================== */}
                  {kind === 'upload' && (
                    <div className="space-y-4">
                      {/* 步骤1：发送给用户的提醒文本（系统自动附上 H5 链接） */}
                      <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/90 space-y-2">
                        <label className="font-bold text-slate-800 flex items-center justify-between">
                          <span>1. 发送给用户的提醒文本（自动附上专属 H5 链接） *</span>
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
                      </div>

                      {/* 步骤2：选择 H5 链接中包含的上传组件（字段报数 / 文本 / 图片 / 文件） */}
                      <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/90 space-y-3">
                        <div className="font-bold text-slate-800">
                          2. 选择 H5 页面需要员工上传的内容类型（可多选组合） *
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
                                        type: e.target.value as 'number' | 'text' | 'select',
                                      })
                                    }
                                    className="bg-slate-50 border border-slate-200 rounded-lg px-2 py-1.5 text-[11px]"
                                  >
                                    <option value="number">数字报数</option>
                                    <option value="text">单行文本</option>
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
                          <span>3. 上传后是否需要审核</span>
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
                      taskName={name}
                      remindAt={remindAt}
                      deadline={hasDeadline ? deadlineTime : ''}
                      deadlineType={hasDeadline ? deadlineType : null}
                      remindText={uploadRemindText}
                      modules={uploadModules}
                      fields={fields}
                      textLabel={textLabel}
                      textPlaceholder={textPlaceholder}
                    />
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
                  disabled={saving}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
                >
                  {saving ? '正在保存…' : editingTpl ? '保存修改' : '确认创建'}
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

      {/* 一键推送确认弹窗（2026-11-12 任务D：测试用按钮，管理员自行判断场景） */}
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
            <p className="text-xs text-slate-600 leading-relaxed">
              将立即向模板「
              <span className="font-bold text-slate-900">{pushConfirmTpl.name}</span>
              」当前<b>待提交</b>的成员各推送一条提醒（内容与定时提醒一致）。
              <br />
              此操作<b>不影响</b>当天已定的定时提醒，两条各发各的；同模板 2 分钟内不可重复推送。
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setPushConfirmTpl(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={handleConfirmPush}
                disabled={pushingId === pushConfirmTpl.id}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-semibold text-xs cursor-pointer disabled:opacity-60"
              >
                {pushingId === pushConfirmTpl.id ? '推送中…' : '确认推送'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
