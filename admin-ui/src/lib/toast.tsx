import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-react';

export type ToastKind = 'success' | 'error' | 'warning' | 'info';

export interface ToastItem {
  id: string;
  kind: ToastKind;
  title?: string;
  message: string;
  duration?: number;
}

interface ToastContextValue {
  showToast: (kind: ToastKind, message: string, title?: string, duration?: number) => void;
  success: (message: string, title?: string) => void;
  error: (message: string, title?: string) => void;
  warning: (message: string, title?: string) => void;
  info: (message: string, title?: string) => void;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

/**
 * 将后端返回的错误码、英文技术报错或复杂信息翻译为通俗易懂的中文提示
 */
const ERROR_CODE_DICT: Record<string, string> = {
  // 模板相关
  NAME_REQUIRED: '请填写任务模板名称，名称不能为空。',
  INVALID_KIND: '请选择有效的任务类型（表单填报、拍照上传、文件上报或純通知）。',
  INVALID_PERIOD: '任务周期设置无效：普通任务支持每日/每周/每月，纯通知任务仅支持每日或每周。',
  PERIOD_IMMUTABLE: '任务模板创建后，「任务类型」与「执行周期」不可更改；如需调整请新建模板。',
  ROLE_NOT_FOUND: '所选的目标角色不存在，请先在「角色与成员」中创建该角色。',
  REMIND_AT_INVALID: '提醒时间格式不正确，请按 HH:MM 格式填写（例如 09:00，多个时间用英文逗号分隔）。',
  DEADLINE_INVALID: '截止时间格式不正确，请按要求填写（例如每日填 18:00，每周填 5 18:00）。',
  DEADLINE_ORDER: '截止时间必须晚于提醒时间，请检查并调整提醒时间与截止时间的先后顺序。',
  TEXT_TPL_REQUIRED: '请填写下发给员工的提醒文案内容。',
  FIELDS_REQUIRED: '表单类任务至少需要配置 1 个填报字段，请点击「添加字段」补充。',
  FIELD_INVALID: '表单字段配置不完整：请确保每个字段都填写了字段标识与中文名称，下拉选项需提供候选项。',
  TEMPLATE_NOT_FOUND: '找不到该任务模板，可能已被删除，请刷新列表后重试。',
  TEMPLATE_HAS_INSTANCES: '该模板已经生成过历史任务实例，为保障审计数据完整不可直接删除；建议将其设为「停用」。',
  LOOPBACK_PUBLIC_URL: '当前外网地址（PUBLIC_BASE_URL）仍是本地回环地址（127.0.0.1/localhost），员工手机端无法打开填报链接，请先配置真实可访问域名后再启用模板。',

  // 角色与成员相关
  ROLE_CODE_INVALID: '角色编码格式不正确：请使用 2~32 位英文字母、数字或下划线（如 role_store_mgr）。',
  ROLE_NAME_REQUIRED: '请填写角色的中文名称（如「门店店长」）。',
  ROLE_EXISTS: '该角色编码已存在，请换一个编码或直接编辑现有角色。',
  ROLE_HAS_TEMPLATES: '仍有任务模板绑定在此角色上，无法直接删除；请先解绑或删除关联模板。',
  PHONE_INVALID: '手机号格式不正确，请输入 11 位中国大陆手机号码（1 开头）。',
  MEMBER_NAME_REQUIRED: '请填写成员姓名，姓名不能为空。',
  MEMBER_NOT_FOUND: '在该角色下未找到此手机号对应的成员，请刷新列表后再试。',
  CSV_EMPTY: '导入内容为空，请粘贴或选择包含「手机号,姓名,部门」的 CSV 数据。',
  CSV_TOO_LARGE: '单次导入数据量过大，请分批导入（每次不超过 2000 行）。',

  // 任务实例与审核相关
  STATUS_FILTER_INVALID: '状态筛选条件无效，请重新选择筛选状态。',
  INSTANCE_NOT_FOUND: '未找到该任务实例，请刷新列表后重试。',
  INVALID_ACTION: '审核动作无效，仅支持「通过（approve）」或「驳回（reject）」。',
  REASON_REQUIRED: '驳回任务时必须填写具体的驳回原因（至少 2 个字），方便员工知晓如何修改重交。',
  STATE_CONFLICT: '该任务当前状态不是「待审核（已提交）」，无法执行审核操作，请刷新页面查看最新状态。',
  UPLOAD_NOT_FOUND: '找不到对应的附件文件，可能已被清理或尚未上传成功。',

  // 認證與安全相關
  UNAUTHORIZED: '当前登录已过期或尚未登录，请重新登录后台。',
  SESSION_EXPIRED: '登录会话已过期，请重新输入账号密码登录。',
  CSRF_INVALID: '安全校验令牌（CSRF）已失效，请刷新页面或重新登录后再操作。',
  INVALID_CREDENTIALS: '账号或密码不正确，请核对后重新输入。',
  WRONG_PASSWORD: '原密码输入不正确，请核对当前登录密码后重试。',
  WEAK_PASSWORD: '新密码强度不足：长度至少为 8 位，且必须同时包含字母和数字。',
  PASSWORD_SAME: '新密码不能与当前原密码相同，请设置一个新的密码。',
  RATE_LIMITED: '连续输错密码次数过多，账号已被临时保护锁定，请稍后再试。',

  // 通道与催办结果相关
  NO_TARGET: '该手机号尚未在量子密信中完成身份激活绑定，系统暂无法向其推送单聊消息。',
  NEED_ACTIVATE: '员工尚未给机器人发送过消息激活会话（平台返回 500），需引导员工在密信中向机器人发任意消息完成激活。',
  CHANNEL_NOT_CONFIGURED: '量子密信通道（AppID / AppSecret）尚未配置，当前处于本地演练模式，未实际发出外部消息。',
  HTTP_TIMEOUT: '请求量子密信网关超时，系统将按退避策略（30秒 / 2分钟 / 5分钟）自动重试。',
};

export function humanizeMessage(raw: unknown, fallback = '操作未成功，请稍后重试'): string {
  if (!raw) return fallback;
  const text = typeof raw === 'string' ? raw.trim() : String(raw);
  if (!text) return fallback;

  // 1. 直接匹配错误码
  if (ERROR_CODE_DICT[text]) {
    return ERROR_CODE_DICT[text];
  }

  // 2. JSON 字符串解析
  if (text.startsWith('{') && text.endsWith('}')) {
    try {
      const parsed = JSON.parse(text);
      const code = parsed.code || parsed.error_code || parsed.error;
      if (code && ERROR_CODE_DICT[code]) {
        return ERROR_CODE_DICT[code];
      }
      if (parsed.message && typeof parsed.message === 'string') {
        return humanizeMessage(parsed.message, fallback);
      }
      if (parsed.detail && typeof parsed.detail === 'string') {
        return humanizeMessage(parsed.detail, fallback);
      }
    } catch {
      // ignore JSON parse failure
    }
  }

  // 3. 匹配包含已知错误码的复合文本，如 "STATE_CONFLICT: only submitted can be reviewed"
  for (const [code, friendly] of Object.entries(ERROR_CODE_DICT)) {
    if (text.includes(code)) {
      return friendly;
    }
  }

  // 4. 常见英文/技术错误转写为通俗中文
  const lower = text.toLowerCase();
  if (lower.includes('failed to fetch') || lower.includes('networkerror')) {
    return '网络连接异常，无法连接到后台服务，请检查网络或服务运行状态。';
  }
  if (lower.includes('401') || lower.includes('unauthorized')) {
    return '登录状态已失效，请重新登录管理后台。';
  }
  if (lower.includes('403') || lower.includes('forbidden') || lower.includes('csrf')) {
    return '安全校验未通过或权限不足，请刷新页面后重试。';
  }
  if (lower.includes('404') || lower.includes('not found')) {
    return '请求的记录或接口不存在，请刷新列表后再试。';
  }
  if (lower.includes('500') || lower.includes('internal server error')) {
    return '服务器处理请求时遇到异常，请稍后重试。';
  }

  return text;
}

/**
 * 将提醒日志里的英文错误码或状态翻译为易懂的中文说明
 */
export function humanizeReminderLogError(result: string, error: string | null): string {
  if (result === 'sent') {
    return '已成功通过量子密信下发提醒消息';
  }
  if (result === 'no_target') {
    return '未发送：该成员手机号尚未在密信中激活绑定（无 external_id）';
  }
  if (result === 'need_activate') {
    return '需重新激活：密信接口返回 500，员工需在密信向机器人发任意消息恢复会话';
  }
  if (!error) {
    return result === 'failed' ? '发送失败：通道返回异常，系统将自动退避重试' : '—';
  }
  return humanizeMessage(error, error);
}

// 允许在 React 组件树外（如 api.ts 全局拦截）触发 Toast
let externalShowToast: ToastContextValue['showToast'] | null = null;

export const globalToast = {
  success: (msg: string, title?: string) =>
    externalShowToast?.('success', humanizeMessage(msg, msg), title),
  error: (msg: string, title?: string) =>
    externalShowToast?.('error', humanizeMessage(msg, '操作失败，请稍后重试'), title),
  warning: (msg: string, title?: string) =>
    externalShowToast?.('warning', humanizeMessage(msg, msg), title),
  info: (msg: string, title?: string) =>
    externalShowToast?.('info', humanizeMessage(msg, msg), title),
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const showToast = useCallback(
    (kind: ToastKind, rawMessage: string, title?: string, duration = 4000) => {
      const message =
        kind === 'error' || kind === 'warning'
          ? humanizeMessage(rawMessage, rawMessage)
          : rawMessage;
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [...prev.slice(-4), { id, kind, title, message, duration }]);
      if (duration > 0) {
        window.setTimeout(() => {
          dismiss(id);
        }, duration);
      }
    },
    [dismiss],
  );

  useEffect(() => {
    externalShowToast = showToast;
    return () => {
      externalShowToast = null;
    };
  }, [showToast]);

  const value = useMemo<ToastContextValue>(
    () => ({
      showToast,
      success: (msg, title) => showToast('success', msg, title ?? '操作成功', 3500),
      error: (msg, title) => showToast('error', msg, title ?? '操作未成功', 5000),
      warning: (msg, title) => showToast('warning', msg, title ?? '请注意', 4500),
      info: (msg, title) => showToast('info', msg, title ?? '提示信息', 3800),
      dismiss,
    }),
    [showToast, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* 全局右上角悬浮 Toast 容器（不随页面滚动消失） */}
      <div
        aria-live="polite"
        className="fixed top-4 right-4 z-[9999] flex flex-col gap-2.5 w-full max-w-sm pointer-events-none px-4 sm:px-0"
      >
        {toasts.map((item) => {
          const styleMap: Record<
            ToastKind,
            {
              border: string;
              bg: string;
              iconBg: string;
              titleColor: string;
              textColor: string;
              icon: React.ReactNode;
            }
          > = {
            success: {
              border: 'border-emerald-200/90',
              bg: 'bg-white/95',
              iconBg: 'bg-emerald-50 text-emerald-600',
              titleColor: 'text-emerald-950',
              textColor: 'text-slate-600',
              icon: <CheckCircle2 className="w-4 h-4 shrink-0" />,
            },
            error: {
              border: 'border-rose-200/90',
              bg: 'bg-white/95',
              iconBg: 'bg-rose-50 text-rose-600',
              titleColor: 'text-rose-950',
              textColor: 'text-slate-600',
              icon: <AlertCircle className="w-4 h-4 shrink-0" />,
            },
            warning: {
              border: 'border-amber-200/90',
              bg: 'bg-white/95',
              iconBg: 'bg-amber-50 text-amber-600',
              titleColor: 'text-amber-950',
              textColor: 'text-slate-600',
              icon: <AlertTriangle className="w-4 h-4 shrink-0" />,
            },
            info: {
              border: 'border-blue-200/90',
              bg: 'bg-white/95',
              iconBg: 'bg-blue-50 text-blue-600',
              titleColor: 'text-slate-900',
              textColor: 'text-slate-600',
              icon: <Info className="w-4 h-4 shrink-0" />,
            },
          };

          const cfg = styleMap[item.kind];

          return (
            <div
              key={item.id}
              className={`pointer-events-auto flex items-start gap-3 p-3.5 rounded-2xl border ${cfg.border} ${cfg.bg} backdrop-blur-md shadow-lg shadow-slate-900/10 transition-all duration-200 animate-in fade-in slide-in-from-top-2`}
            >
              <div
                className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${cfg.iconBg}`}
              >
                {cfg.icon}
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                {item.title && (
                  <div className={`text-xs font-bold leading-tight mb-0.5 ${cfg.titleColor}`}>
                    {item.title}
                  </div>
                )}
                <div className={`text-xs leading-relaxed break-words ${cfg.textColor}`}>
                  {item.message}
                </div>
              </div>
              <button
                onClick={() => dismiss(item.id)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors cursor-pointer shrink-0"
                title="关闭提示"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
};

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    return {
      showToast: (kind, msg, title) => globalToast[kind](msg, title),
      success: (msg, title) => globalToast.success(msg, title),
      error: (msg, title) => globalToast.error(msg, title),
      warning: (msg, title) => globalToast.warning(msg, title),
      info: (msg, title) => globalToast.info(msg, title),
      dismiss: () => undefined,
    };
  }
  return ctx;
}
