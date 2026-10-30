import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  KeyRound,
  Loader2,
  Plug,
  RefreshCw,
  Save,
  ShieldCheck,
} from 'lucide-react';
import { getCsrfToken } from '../../lib/api';
import { copyText } from '../../lib/clipboard';
import { useToast } from '../../lib/toast';

/* ================================================================== */
/* 2026-10-30 通道配置向导                                              */
/* ------------------------------------------------------------------ */
/* 目标：换一家公司部署时，后台填表即可，⛔ 不改代码、不写 .env。          */
/* 页面只做三件事：看状态（掩码+回调地址）→ 填凭证 → 测试连接 → 保存。   */
/* 保存后必须重启服务才生效（后端只在校启动时读一次，不做热更新）。       */
/* ================================================================== */

interface ChannelField {
  key: string;
  label: string;
  required: boolean;
  secret: boolean;
  hint?: string;
}

interface ChannelStatus {
  channel: string;
  label: string;
  configured: boolean;
  source: 'db' | 'env' | 'none';
  masked: Record<string, string>;
  fields: ChannelField[];
  callback_url: string;
  callback_path: string;
  is_active: boolean;
}

interface ChannelConfigResp {
  channels: ChannelStatus[];
  active_channel: string;
  any_configured: boolean;
  public_base_url: string;
  enc_key_source: string;
  enc_key_desc: string;
}

/* 这个接口把「连通性结果」放在 ok 字段里（ok=false 表示连接失败，但请求本身成功），
   与全局 api 封装「ok===false 就抛错」的约定冲突，所以这里单独发请求。 */
async function postJson<T>(url: string, body: unknown): Promise<{ status: number; data: T }> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
    body: JSON.stringify(body),
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { status: res.status, data: data as T };
}

const SOURCE_LABEL: Record<string, string> = {
  db: '数据库（向导保存）',
  env: '环境变量（.env 现状）',
  none: '未配置',
};

const ChannelSettingsView: React.FC = () => {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [state, setState] = useState<ChannelConfigResp | null>(null);

  const [picked, setPicked] = useState<string>('');
  const [form, setForm] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const [savedHint, setSavedHint] = useState<string | null>(null);
  /* 是否把这条通道设为「启用通道」。默认不勾 —— 否则「只是先存个备用的企微凭证」
     会在重启后把在跑的通道换掉，等于静默改线上行为。 */
  const [setActive, setSetActive] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch('/api/admin/channel-config', { credentials: 'same-origin' });
      if (res.status === 401) throw new Error('登录已过期，请重新登录');
      const data = (await res.json()) as ChannelConfigResp & { ok?: boolean; message?: string };
      if (!res.ok || data?.ok === false) throw new Error(data?.message || `HTTP_${res.status}`);
      setState(data);
      setPicked((prev) => prev || data.channels[0]?.channel || '');
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '读取通道配置失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const current = useMemo(
    () => state?.channels.find((c) => c.channel === picked) || null,
    [state, picked],
  );

  /* 切通道：清空表单与上一次的测试结果，避免把 A 的凭证保存到 B */
  const handlePick = (channel: string) => {
    setPicked(channel);
    setForm({});
    setTestResult(null);
    setSavedHint(null);
  };

  const setField = (key: string, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setTestResult(null);
  };

  const filledCount = useMemo(
    () => Object.values(form).filter((v) => v.trim()).length,
    [form],
  );

  const doTest = async () => {
    if (!current) return;
    setTesting(true);
    setTestResult(null);
    try {
      const { status, data } = await postJson<{ ok: boolean; detail: string }>(
        '/api/admin/channel-config/test',
        { channel: current.channel, payload: form },
      );
      if (status === 401) throw new Error('登录已过期，请重新登录');
      if (status >= 400) throw new Error((data as any)?.message || `HTTP_${status}`);
      setTestResult({ ok: !!data.ok, detail: data.detail || (data.ok ? '连接成功' : '连接失败') });
    } catch (err) {
      setTestResult({ ok: false, detail: err instanceof Error ? err.message : '测试请求失败' });
    } finally {
      setTesting(false);
    }
  };

  const doSave = async () => {
    if (!current) return;
    if (filledCount === 0) {
      toast.warning('请先填写至少一项凭证');
      return;
    }
    setSaving(true);
    try {
      const { status, data } = await postJson<{ ok: boolean; message?: string; masked?: Record<string, string> }>(
        '/api/admin/channel-config',
        { channel: current.channel, payload: form, set_active: setActive },
      );
      if (status === 401) throw new Error('登录已过期，请重新登录');
      if (status >= 400 || data?.ok === false) {
        throw new Error((data as any)?.message || `HTTP_${status}`);
      }
      setSavedHint(data.message || '重启服务后生效');
      setForm({});
      toast.success('凭证已加密保存，重启服务后生效', '保存成功');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  /* 2026-10-30：后台是 http://内网IP 访问，非安全上下文下 navigator.clipboard 是 undefined，
     原来直接用它 → 点一下就报「浏览器不允许自动复制」。改走 copyText 的两级降级。 */
  const copy = async (text: string, label: string) => {
    if (!text) {
      toast.warning('该通道还没有回调地址，请先配置 public_base_url');
      return;
    }
    const ok = await copyText(text);
    if (ok) toast.success(`已复制${label}`);
    else toast.error('复制失败，请点一下地址框全选后手动复制（Ctrl+C）');
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-24 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
        <div className="h-64 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="bg-white rounded-2xl border border-rose-200 p-6">
        <div className="flex items-center gap-2 text-rose-700 font-bold text-sm">
          <AlertTriangle className="w-4 h-4" />
          读取通道配置失败
        </div>
        <p className="mt-2 text-xs text-slate-600">{loadError}</p>
        <button
          onClick={() => void load()}
          className="mt-3 px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-xs font-semibold cursor-pointer"
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 顶部说明 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Plug className="w-4 h-4 text-blue-600" />
              通道配置
            </h2>
            <p className="mt-1 text-xs text-slate-500 leading-relaxed">
              首次安装只需在此填写凭证并保存，<span className="font-semibold text-slate-700">无需修改代码或 .env</span>。
              凭证以 AES-GCM 加密后存库，密钥与数据库分开保存。
            </p>
            {/* 2026-10-30 用户口径：必填 = 全链路（发送 + 回调接收），缺一项都拦住 */}
            <p className="mt-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5 leading-relaxed">
              以上必填项（<span className="font-semibold">标 * 的</span>）缺任一项，保存会被拦截；
              其中<span className="font-semibold">回调 Token / 加密密钥</span>缺失会导致回调验签失败，
              用户消息（绑定、申请补交）机器人收不到。
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-slate-600 bg-slate-50 hover:bg-slate-100 border border-slate-200 flex items-center gap-1 cursor-pointer shrink-0"
          >
            <RefreshCw className="w-3 h-3" />
            刷新
          </button>
        </div>

        {!state?.any_configured && (
          <div className="mt-3 p-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold">还没有任何通道配置完成。</span>
              填好凭证并保存、重启服务后，任务提醒才会真正发出去。
            </div>
          </div>
        )}
      </div>

      {/* 状态卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {(state?.channels || []).map((c) => (
          <div
            key={c.channel}
            className={`bg-white rounded-2xl border p-4 ${
              c.channel === picked ? 'border-blue-300 ring-1 ring-blue-100' : 'border-slate-200/80'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-bold text-sm text-slate-900 truncate">{c.label}</span>
                {c.is_active && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-blue-50 text-blue-600 border border-blue-200/70 font-semibold">
                    当前启用
                  </span>
                )}
              </div>
              <span
                className={`text-[10px] px-2 py-0.5 rounded-md font-semibold ${
                  c.configured
                    ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                    : 'bg-slate-100 text-slate-500 border border-slate-200'
                }`}
              >
                {c.configured ? '已配置' : '未配置'}
              </span>
            </div>

            <div className="mt-1 text-[11px] text-slate-500">
              来源：{SOURCE_LABEL[c.source] || c.source}
            </div>

            <div className="mt-2.5 space-y-1">
              {Object.keys(c.masked).length === 0 ? (
                <div className="text-[11px] text-slate-400">暂无凭证</div>
              ) : (
                Object.entries(c.masked).map(([k, v]) => {
                  const f = c.fields.find((x) => x.key === k);
                  return (
                    <div key={k} className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="text-slate-500">{f?.label || k}</span>
                      <span className="font-mono text-slate-700">{v}</span>
                    </div>
                  );
                })
              )}
            </div>

            <div className="mt-3 pt-2.5 border-t border-slate-100">
              <div className="text-[10px] font-semibold text-slate-400 mb-1">回调地址</div>
              <div className="flex items-center gap-1.5">
                {/* 用 readonly input 而不是 code：复制失败时还能点一下全选手动复制 */}
                <input
                  readOnly
                  value={c.callback_url || ''}
                  placeholder="（未配置 public_base_url）"
                  onFocus={(e) => e.currentTarget.select()}
                  onClick={(e) => e.currentTarget.select()}
                  title={c.callback_url || ''}
                  className="flex-1 min-w-0 text-[11px] font-mono text-slate-700 bg-slate-50 border border-slate-200 rounded-lg px-2 py-1 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 cursor-text"
                />
                <button
                  onClick={() => void copy(c.callback_url, '回调地址')}
                  className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 border border-slate-200 cursor-pointer shrink-0"
                  title="复制回调地址"
                >
                  <Copy className="w-3 h-3" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 配置表单 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-5">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-blue-600" />
            填写凭证
          </h3>
          <div className="flex items-center gap-1.5">
            {(state?.channels || []).map((c) => (
              <button
                key={c.channel}
                onClick={() => handlePick(c.channel)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors cursor-pointer ${
                  picked === c.channel
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-slate-50 text-slate-600 border-slate-200 hover:border-blue-300'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
          {(current?.fields || []).map((f) => (
            <div key={f.key} className={f.key === 'app_secret' || f.key === 'secret' ? 'md:col-span-1' : ''}>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                {f.label}
                {f.required && <span className="text-rose-500 ml-0.5">*</span>}
              </label>
              <input
                type={f.secret ? 'password' : 'text'}
                value={form[f.key] || ''}
                onChange={(e) => setField(f.key, e.target.value)}
                /* 2026-10-30：原来 placeholder 和下方说明都用 hint，两行一模一样的字；
                   现在 placeholder 只负责「留空沿用」，字段说明统一放下面一行。 */
                placeholder={
                  current?.configured && f.secret
                    ? `留空则沿用已保存的值（${current.masked[f.key] || '****'}）`
                    : ''
                }
                autoComplete="off"
                className="w-full px-3 py-2 rounded-lg border border-slate-200 text-xs font-mono focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50"
              />
              {f.hint && <div className="mt-1 text-[10px] text-slate-400">{f.hint}</div>}
            </div>
          ))}
        </div>

        {testResult && (
          <div
            className={`mt-3 p-2.5 rounded-xl text-xs flex items-start gap-2 ${
              testResult.ok
                ? 'bg-emerald-50 border border-emerald-200 text-emerald-800'
                : 'bg-rose-50 border border-rose-200 text-rose-800'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            )}
            <div className="break-all">{testResult.detail}</div>
          </div>
        )}

        {savedHint && (
          <div className="mt-3 p-2.5 rounded-xl bg-blue-50 border border-blue-200 text-blue-800 text-xs flex items-start gap-2">
            <ShieldCheck className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold">已加密保存。</span>
              {savedHint}
              <div className="mt-1 font-mono text-[11px] text-blue-700">systemctl restart imtask</div>
            </div>
          </div>
        )}

        <div className="mt-4 flex items-center gap-2 flex-wrap">
          <button
            onClick={() => void doTest()}
            disabled={testing || !current}
            className="px-3.5 py-2 rounded-xl text-xs font-semibold bg-white border border-slate-200 text-slate-700 hover:border-blue-300 disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
          >
            {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
            测试连接
          </button>
          <button
            onClick={() => void doSave()}
            disabled={saving || !current}
            className="px-3.5 py-2 rounded-xl text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            保存
          </button>
          <span className="text-[11px] text-slate-400">
            凭证保存后需重启服务生效；测试连接只探活，不会写入数据库。
          </span>
        </div>

        <label className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={setActive}
            onChange={(e) => setSetActive(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-slate-300 text-blue-600 focus:ring-0 cursor-pointer"
          />
          重启后使用该通道发送消息（不勾选则只保存凭证，不改变当前启用的通道）
        </label>
      </div>
    </div>
  );
};

export default ChannelSettingsView;
