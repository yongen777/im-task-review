import { useEffect, useId, useState } from 'react';
import { api, plainError } from '../../lib/api';
import { copyText } from '../../lib/clipboard';
import { getCsrfToken } from '../../lib/realApi';
import { DataInboxRecord, VariableNoticeConfig, VariableNoticeIssue } from '../../lib/types';

export interface VariableNoticeSample {
  phone: string;
  name: string;
  body: string;
}

export interface VariableNoticeValidation {
  config: VariableNoticeConfig;
  samples: VariableNoticeSample[];
}

export const normalizeVariableBody = (body: string) => body.replace(/\\r\\n|\\n/g, '\n').trim();

export function getNoticeVariables(body: string): string[] {
  return [...new Set([...body.matchAll(/\{([^{}\r\n]+)\}/g)].map(match => match[1].trim()).filter(Boolean))];
}

export function VariableNoticeIssues({ issues }: { issues: VariableNoticeIssue[] }) {
  if (!issues.length) return null;
  return (
    <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
      <p className="font-semibold">表格校验失败，请修正后重新导入</p>
      <div className="mt-1.5 max-h-48 overflow-auto">
        <table className="w-full text-left">
          <thead><tr><th className="pr-3">行号</th><th className="pr-3">手机号</th><th>原因</th></tr></thead>
          <tbody>{issues.map((issue, index) => (
            <tr key={index}>
              <td className="whitespace-nowrap pr-3 align-top">{issue.row}</td>
              <td className="pr-3 align-top">{issue.phone || '—'}</td>
              <td className="break-all">{issue.reason}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

export function VariableNoticeFields({ body, issues, validation, busy, onUpload, automatic = false }: {
  body: string;
  issues: VariableNoticeIssue[];
  validation?: VariableNoticeValidation;
  busy: boolean;
  onUpload: (file: File) => void;
  automatic?: boolean;
}) {
  const variables = getNoticeVariables(body);
  const [format, setFormat] = useState<'xlsx' | 'csv'>('xlsx');
  const [downloadError, setDownloadError] = useState('');

  async function downloadTemplate() {
    setDownloadError('');
    try {
      const response = await fetch('/api/admin/variable-notices/template', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        body: JSON.stringify({ body, format }),
      });
      if (!response.ok) throw new Error((await response.json()).message || '模板下载失败');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `通知变量模板.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '模板下载失败');
    }
  }

  return (
    <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs">
      {variables.length ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-slate-600">正文变量</span>
          {variables.map(name => <span key={name} className="rounded bg-blue-50 px-2 py-0.5 text-blue-800">{`{${name}}`}</span>)}
        </div>
      ) : <p className="text-amber-800">请在通知正文填写至少一个变量，例如 {'{姓名}'}。</p>}
      <p className="text-slate-600">表格须包含手机号、姓名和正文变量，收件人须在您名下的当前平台名册中启用并激活。</p>
      <p className="text-slate-600">每次最多 5,000 人、{automatic ? 20 : 4} MB。</p>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="模板格式" value={format} onChange={event => setFormat(event.target.value as 'xlsx' | 'csv')}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1.5">
          <option value="xlsx">XLSX</option>
          <option value="csv">CSV</option>
        </select>
        <button type="button" disabled={!variables.length} onClick={downloadTemplate}
          className="rounded-lg border border-blue-200 bg-white px-3 py-1.5 font-medium text-blue-700 disabled:opacity-50">
          下载变量模板
        </button>
        {!automatic && <label className={`rounded-lg bg-blue-700 px-3 py-1.5 font-medium text-white ${!variables.length || busy ? 'opacity-50' : 'cursor-pointer'}`}>
          {busy ? '正在校验…' : validation ? '重新导入' : '上传变量表格'}
          <input type="file" className="sr-only" accept=".xlsx,.csv" disabled={!variables.length || busy}
            onChange={event => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) onUpload(file);
            }} />
        </label>}
      </div>
      {downloadError && <p role="alert" className="text-rose-700">{downloadError}</p>}
      {validation && <p role="status" className="font-semibold text-emerald-700">校验通过：共 {validation.config.rows.length} 人</p>}
      <VariableNoticeIssues issues={issues} />
    </section>
  );
}

export interface DataInboxDraft {
  key: string;
  maxAge: string;
  minRows: string;
}

const inboxStatus: Record<DataInboxRecord['status'], string> = {
  waiting: '等待文件', processing: '投递中', success: '投递成功', failed: '已拦截或投递未完成', skipped: '重复文件已跳过',
};

interface DataInboxTokenStatus {
  configured: boolean;
  enabled: boolean;
  token_prefix: string | null;
  updated_at: string | null;
  last_used_at: string | null;
}

interface IssuedDataInboxToken extends DataInboxTokenStatus {
  token: string;
  upload_url: string | null;
}

function DataInboxTokenFields({ sourceKey }: { sourceKey: string }) {
  const [status, setStatus] = useState<DataInboxTokenStatus>();
  const [issued, setIssued] = useState<IssuedDataInboxToken>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copyFeedback, setCopyFeedback] = useState('');
  const path = `/api/admin/data-inbox/${encodeURIComponent(sourceKey)}/token`;

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      api.get<DataInboxTokenStatus>(path).then(result => {
        if (active) setStatus(result);
      }).catch(err => {
        if (active) setError(err instanceof Error ? plainError(err.message) : '令牌状态读取失败');
      });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [path]);

  async function changeToken(disable: boolean) {
    if (busy || !status) return;
    if (status.enabled && !window.confirm(disable
      ? '停用后，该数据源将拒收 HTTP 上传。确定停用？'
      : '重置后，旧令牌会立即失效。确定重置？')) return;
    setBusy(true);
    setError('');
    setCopyFeedback('');
    try {
      if (disable) {
        setStatus(await api.delete<DataInboxTokenStatus>(path));
        setIssued(undefined);
      } else {
        const result = await api.post<IssuedDataInboxToken>(path);
        const { configured, enabled, token_prefix, updated_at, last_used_at } = result;
        setStatus({ configured, enabled, token_prefix, updated_at, last_used_at });
        setIssued(result);
      }
    } catch (err) {
      setError(err instanceof Error ? plainError(err.message) : '令牌操作失败');
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string, label: string) {
    setCopyFeedback(await copyText(value) ? `已复制${label}` : '复制失败，请选中文字手动复制');
  }

  const uploadUrl = issued?.upload_url?.replace(/'/g, "''");
  const command = issued && uploadUrl
    ? `curl.exe -sS -H 'X-Data-Token: ${issued.token}' -F 'file=@C:\\路径\\通知变量表格.xlsx' --url '${uploadUrl}'`
    : '';
  const buttonClass = 'rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-medium text-slate-700 disabled:opacity-50';

  return (
    <section className="space-y-2 border-y border-slate-200 py-2" aria-label="上传令牌">
      {status ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-slate-700">
            <span className="font-semibold">上传令牌</span>
            {!status.configured ? ' · 未生成' : status.enabled ? (
              <> · 已启用 <code>{status.token_prefix}</code> · 最近使用 {status.last_used_at || '暂无'}</>
            ) : ' · 已停用'}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <button type="button" className={buttonClass} disabled={busy} onClick={() => void changeToken(false)}>
              {status.configured ? '重置令牌' : '生成上传令牌'}
            </button>
            {status.enabled && (
              <button type="button" className={buttonClass} disabled={busy} onClick={() => void changeToken(true)}>
                停用
              </button>
            )}
          </div>
        </div>
      ) : !error && <p role="status" className="text-slate-500">正在读取上传令牌状态…</p>}
      {error && <p role="alert" className="text-rose-700">{error}</p>}
      {issued && (
        <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-amber-900">
          <p className="font-semibold">明文仅展示一次，遗忘可直接重置</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 break-all">{issued.token}</code>
            <button type="button" className={buttonClass} onClick={() => void copy(issued.token, '令牌')}>复制令牌</button>
          </div>
          {command ? (
            <>
              <p>Windows PowerShell 上传命令。将示例文件路径换成实际路径后运行。</p>
              <code className="block break-all rounded-md bg-white px-2 py-1.5">{command}</code>
              <button type="button" className={buttonClass} onClick={() => void copy(command, '上传命令')}>复制上传命令</button>
            </>
          ) : <p>缺少业务访问地址，暂无法生成上传命令。</p>}
          {copyFeedback && <p role="status">{copyFeedback}</p>}
        </div>
      )}
    </section>
  );
}

export function DataInboxFields({ value, onChange, record, loading, loadFailed, canManageToken }: {
  value: DataInboxDraft;
  onChange: (value: DataInboxDraft) => void;
  record: DataInboxRecord | null;
  loading: boolean;
  loadFailed: boolean;
  canManageToken: boolean;
}) {
  const helpId = useId();
  const inputClass = 'mt-1 block w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2';
  const key = value.key.trim();
  const validKey = /^[A-Za-z0-9_-]{2,64}$/.test(key);
  const pathKey = validKey ? key : '{source_key}';
  return (
    <fieldset className="space-y-2 rounded-xl border border-slate-200 p-3 text-xs">
      <legend className="px-1 font-semibold">自动数据源接入（选填）</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        <label>数据源键
          <input className={inputClass} value={value.key} maxLength={64} placeholder="例如 scores_01"
            onChange={event => onChange({ ...value, key: event.target.value })} />
        </label>
        <label>文件有效时长（小时）
          <input className={inputClass} type="number" min={1} step={1} value={value.maxAge}
            aria-describedby={`${helpId}-max-age`}
            onChange={event => onChange({ ...value, maxAge: event.target.value })} />
        </label>
        <label>最低行数
          <input className={inputClass} type="number" min={1} step={1} value={value.minRows}
            aria-describedby={`${helpId}-min-rows`}
            onChange={event => onChange({ ...value, minRows: event.target.value })} />
        </label>
      </div>
      <div className="space-y-1 text-slate-500 leading-relaxed">
        <p id={`${helpId}-max-age`}>
          <span className="font-medium text-slate-600">文件有效时长</span>
          {' · 按文件上传到正式推送的最大间隔填写，与任务周期无关。超时旧表会被拦截，本次不推送。自动定时取数建议填 12–24 小时，提前跨天手工上传可填 72–168 小时。'}
        </p>
        <p id={`${helpId}-min-rows`}>
          <span className="font-medium text-slate-600">最低行数</span>
          {' · 用于防止空表或残缺表，只计数据行，低于下限会拦截本次推送。固定全员通知建议按正常人数的 70%–80% 取整数，给人员变动留出余量。按动态未完成名单催办时可填 1。'}
        </p>
      </div>
      <p className="text-slate-600">同机文件放入接入目录的 <code>{pathKey}/incoming/</code>。跨机请请求业务端口，携带文件字段 <code>file</code> 和上传令牌。</p>
      <code className="block break-all text-slate-800">POST /api/data-inbox/{pathKey}/upload</code>
      {canManageToken && validKey && <DataInboxTokenFields key={key} sourceKey={key} />}
      {loading && <p role="status">正在读取最近接入记录…</p>}
      {!loading && (record?.source_key === key ? (
        <p className="break-words text-slate-700">
          最近接入 {record.created_at} · {inboxStatus[record.status]}
          {record.row_count !== null && ` · ${record.row_count} 行`}
          {record.error_msg && ` · ${record.error_msg}`}
        </p>
      ) : <p className="text-slate-500">{loadFailed ? '最近接入记录读取失败' : '暂无接入记录'}</p>)}
    </fieldset>
  );
}
