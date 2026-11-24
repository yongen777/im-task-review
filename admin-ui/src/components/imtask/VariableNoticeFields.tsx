import { useState } from 'react';
import { getCsrfToken } from '../../lib/realApi';
import { VariableNoticeConfig, VariableNoticeIssue } from '../../lib/types';

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

export function VariableNoticeFields({ body, issues, validation, busy, onUpload }: {
  body: string;
  issues: VariableNoticeIssue[];
  validation?: VariableNoticeValidation;
  busy: boolean;
  onUpload: (file: File) => void;
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
      <p className="text-slate-600">上传含手机号、姓名及正文变量的表格。仅可选用当前平台下您名下已启用、已激活的成员。</p>
      <p className="text-slate-600">每次导入一张表，最多 5,000 人、4 MB。</p>
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
        <label className={`rounded-lg bg-blue-700 px-3 py-1.5 font-medium text-white ${!variables.length || busy ? 'opacity-50' : 'cursor-pointer'}`}>
          {busy ? '正在校验…' : validation ? '重新导入' : '上传变量表格'}
          <input type="file" className="sr-only" accept=".xlsx,.csv" disabled={!variables.length || busy}
            onChange={event => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) onUpload(file);
            }} />
        </label>
      </div>
      {downloadError && <p role="alert" className="text-rose-700">{downloadError}</p>}
      {validation && <p role="status" className="font-semibold text-emerald-700">校验通过：共 {validation.config.rows.length} 人</p>}
      <VariableNoticeIssues issues={issues} />
    </section>
  );
}
