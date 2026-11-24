import { useState, type FormEvent } from 'react';
import { Search } from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useToast } from '../../lib/toast';

interface Candidate {
  userid: string;
  name: string;
  dept_paths: string[];
  alias: string;
  position: string;
}

const inputClass = 'min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-blue-500';
const buttonClass = 'rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50';

export function WecomBotMemberForm({ roleId, onAdded }: { roleId: string; onAdded: () => Promise<unknown> }) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const endpoint = `/api/admin/roles/${encodeURIComponent(roleId)}/members/wecom-bot`;

  function updateName(value: string) {
    setName(value);
    setCandidates(null);
    setSelected('');
    setError('');
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    setCandidates(null);
    setSelected('');
    try {
      const result = await api.post<{ candidates: Candidate[] }>(endpoint, { name });
      setCandidates(result.candidates);
      if (result.candidates.length === 1) setSelected(result.candidates[0].userid);
    } catch (err) {
      setError(err instanceof Error ? plainError(err.message) : '查询企微通讯录失败');
    } finally {
      setBusy(false);
    }
  }

  async function bind() {
    if (busy || !selected) return;
    if (!/^1[3-9]\d{9}$/.test(phone.trim())) {
      setError('绑定时请填写有效的 11 位手机号。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api.post(endpoint, { name, phone: phone.trim(), userid: selected });
      setName('');
      setPhone('');
      setCandidates(null);
      setSelected('');
      toast.success('企微成员已绑定。请员工首次打开机器人单聊并发送一条消息。');
      try {
        await onAdded();
      } catch {
        toast.warning('成员已绑定，名册刷新失败。');
      }
    } catch (err) {
      setError(err instanceof Error ? plainError(err.message) : '绑定企微成员失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={search} className="space-y-3 border-b border-slate-100 bg-slate-50/70 px-4 py-3 sm:px-5">
      <div className="flex flex-col gap-2 text-xs lg:flex-row lg:items-center">
        <span className="shrink-0 font-semibold text-slate-700">添加企微机器人成员</span>
        <input aria-label="企微姓名" placeholder="企微姓名" required disabled={busy} maxLength={128}
          className={`${inputClass} flex-1`} value={name} onChange={(e) => updateName(e.target.value)} />
        <input aria-label="手机号" placeholder="手机号（绑定时填写）" disabled={busy} inputMode="numeric" maxLength={11}
          className={`${inputClass} flex-1`} value={phone} onChange={(e) => { setPhone(e.target.value); setError(''); }} />
        <button type="submit" disabled={busy} className={`${buttonClass} inline-flex items-center gap-1.5`}>
          <Search className="h-3.5 w-3.5" /> 查询企微通讯录
        </button>
      </div>
      <p className="text-xs text-slate-500">按企微姓名查询，部门由通讯录自动带出。手机号用于绑定 IMTask 名册。</p>
      {error && <p role="alert" className="text-xs font-semibold text-rose-700">{error}</p>}
      {candidates?.length === 0 && <p role="status" className="text-xs text-slate-600">没有找到同名的企微成员。</p>}
      {!!candidates?.length && (
        <fieldset disabled={busy} className="space-y-2 rounded-xl border border-slate-200 bg-white p-3">
          <legend className="px-1 text-xs font-semibold text-slate-800">
            {candidates.length > 1 ? `找到 ${candidates.length} 个同名账号，请核对后选择` : '核对企微成员'}
          </legend>
          {candidates.map((candidate) => {
            const details = (
              <span className="min-w-0 break-words">
                <span className="font-medium text-slate-800">{candidate.name} · {candidate.userid}</span>
                <span className="block text-xs text-slate-600">
                  {[candidate.position, candidate.alias].filter(Boolean).join(' · ')}
                </span>
                {(candidate.dept_paths.length ? candidate.dept_paths : ['企微通讯录暂无部门信息']).map((path) => (
                  <span key={path} className="block text-xs text-slate-600">{path}</span>
                ))}
              </span>
            );
            return candidates.length === 1 ? (
              <div key={candidate.userid} className="px-2 py-1.5 text-sm">{details}</div>
            ) : (
              <label key={candidate.userid} className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-slate-50">
                <input type="radio" name={`wecom-member-${roleId}`} className="mt-1" checked={selected === candidate.userid}
                  onChange={() => setSelected(candidate.userid)} />
                {details}
              </label>
            );
          })}
          <button type="button" disabled={!selected || busy} onClick={() => void bind()} className={buttonClass}>确认并绑定</button>
        </fieldset>
      )}
    </form>
  );
}
