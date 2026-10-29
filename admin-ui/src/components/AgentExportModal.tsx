import React, { useCallback, useEffect, useState } from 'react';
import {
  X,
  Copy,
  Check,
  Download,
  Terminal,
  FileCode,
  Sparkles,
  RefreshCw,
  FileText,
} from 'lucide-react';
import { useToast } from '../lib/toast';

interface AgentSourceFile {
  path: string;
  lang: string;
  content: string;
  lines: number;
}

interface AgentBundleResponse {
  ok: boolean;
  updatedAt: string;
  prompt: string;
  files: AgentSourceFile[];
  fullMarkdown: string;
  tarGzBase64?: string;
}

interface AgentExportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const CLIENT_PROMPT_FALLBACK = `# IMTask 办公任务调度平台 · Agent 1:1 复刻操作指令

你是一名资深全栈工程师。请根据本指令与随附的全量源码文件，在本地 **1:1 完整复刻**「IMTask 办公任务调度平台 · 管理后台」。

## 一、 核心业务规范与已确认交互细节（严禁偏离）
1. **真实业务初始化数据（清除测试假数据）**：
   - 默认内置市场部春促真实角色清单（9 个角色：片长/分支局长、行销总监、网格长、装维、智家工程师、营业员、商客经理、政企经理、包区领导）。
   - 默认内置真实测试人员：**张三**（手机号 \`13900000000\`，角色：\`片长/分支局长\`）。
   - 默认内置片长/分支局长每日 5 条真实春促任务模板（08:15 晨会安排、12:00 上午业务点检与午间复盘、15:00 上门协同、18:00 夜间补差、20:00 下午业务点检与晚间复盘）。
2. **四大核心模块与界面精简要求**：
   - **左侧导航栏**：移除「量子密信网关状态」卡片；底部提供「获取最新代码与 Agent 指令」按钮；管理员资料下拉菜单移除「账号与安全策略」，仅保留「修改登录密码」与「退出当前登录」。
   - **任务实例与审核（InstancesView）**：
     - 顶部筛选支持：**日期选择器（默认今天）**、**按任务类型查询**（纯通知提醒 / 上传任务）、按目标角色、按状态（待处理、待审核、已通过、已驳回，**已移除“已过期”状态**）、按姓名/手机号搜索。
     - **已移除「导出 CSV」按钮**。
     - **纯通知提醒实例默认无需审核**，操作列不展示「通过/驳回」按钮；只有配置了「需要审核」的上传任务在提交后才显示审核操作。
     - 实例列表与「详情」弹窗中，**接收人必须带上所属角色名称**（如 \`张三 (13900000000) · 片长/分支局长\`）。
   - **任务模板管理（TemplatesView）**：
     - 支持按「目标角色」与「任务类型」筛选；**已移除「立即推送测试」按钮**与独立的「上传任务 H5 预览效果图」按钮。
     - 模板列表精简干净：**不显示任何底部冗余描述文本**（如“共2条子任务并列下发”、“默认无需审核”等）。
     - **纯通知提醒模板**：无截止时间（仅执行时间），移除变量占位提示文本，支持同时配置多条并列子任务（每条可独立选择「文本消息」或「文件/图片」格式并配置内容）。
     - **上传任务模板**：合成为统一流程（1. 密信提醒文案 -> 2. 员工 H5 上传页，支持拍照/图片、文件、报数字段、文本说明任意组合），包含**执行时间与截止时间**、**是否需要审核开关（需要审核 / 无需审核）**；**已移除反馈提示语编辑功能**，右侧实时手机预览仅展示「1. 密信提醒」与「2. H5上传页」（**已移除第3步完成反馈预览**）。
   - **角色与成员名册（RolesMembersView）**：
     - 角色列表不展示角色编码，不展示“春促角色清单：负责每日...”长串描述文本。
     - 添加/导入成员**只需填写用户名和手机号**，无需填写所属部门；编辑成员时手机号只读锁死。
     - 右侧常驻展示**「全部未激活人员」专栏**，支持登记未激活人员、搜索、单人**「一键移入分组」**及多选**「批量移入当前角色分组」**并自动完成激活。

## 二、 复刻执行步骤
1. 按照下方文件清单逐一写入对应路径（保持目录结构与文件内容完全一致）。
2. 执行 \`npm install\` 安装依赖。
3. 执行 \`npm run dev\` 启动服务（默认监听 \`http://localhost:3000\`）。
4. 可选执行 \`node tests/e2e_admin_check.mjs\` 运行 Playwright 自动化测试验真。`;

const RAW_CLIENT_FILES = import.meta.glob(
  [
    '/package.json',
    '/tsconfig.json',
    '/vite.config.ts',
    '/metadata.json',
    '/index.html',
    '/server.ts',
    '/src/main.tsx',
    '/src/index.css',
    '/src/App.tsx',
    '/src/lib/*.ts',
    '/src/lib/*.tsx',
    '/src/pages/*.tsx',
    '/src/components/system/*.tsx',
    '/src/components/imtask/*.tsx',
    '/src/components/AgentExportModal.tsx',
    '/tests/e2e_admin_check.mjs',
  ],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>;

function buildClientUstarTarBytes(entries: Array<{ path: string; content: string }>): Uint8Array {
  const encoder = new TextEncoder();
  const writeAscii = (buf: Uint8Array, str: string, offset: number, maxLen: number) => {
    for (let i = 0; i < str.length && i < maxLen; i++) {
      buf[offset + i] = str.charCodeAt(i) & 0xff;
    }
  };

  const chunks: Uint8Array[] = [];
  const mtimeOctal = Math.floor(Date.now() / 1000).toString(8).padStart(11, '0');

  for (const entry of entries) {
    const cleanName = entry.path.replace(/^\/+/, '');
    const contentBytes = encoder.encode(entry.content);
    const sizeOctal = contentBytes.length.toString(8).padStart(11, '0');

    const header = new Uint8Array(512);
    writeAscii(header, cleanName, 0, 100);
    writeAscii(header, '0000644\0', 100, 8);
    writeAscii(header, '0000000\0', 108, 8);
    writeAscii(header, '0000000\0', 116, 8);
    writeAscii(header, sizeOctal + '\0', 124, 12);
    writeAscii(header, mtimeOctal + '\0', 136, 12);
    writeAscii(header, '        ', 148, 8);
    writeAscii(header, '0', 156, 1);
    writeAscii(header, 'ustar\0', 257, 6);
    writeAscii(header, '00', 263, 2);

    let sum = 0;
    for (let i = 0; i < 512; i++) {
      sum += header[i];
    }
    const chksumOctal = sum.toString(8).padStart(6, '0') + '\0 ';
    writeAscii(header, chksumOctal, 148, 8);

    chunks.push(header);
    chunks.push(contentBytes);

    const remainder = contentBytes.length % 512;
    if (remainder !== 0) {
      chunks.push(new Uint8Array(512 - remainder));
    }
  }

  chunks.push(new Uint8Array(1024));

  const totalLength = chunks.reduce((acc, c) => acc + c.length, 0);
  const out = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

async function createTarGzBlobFromBundle(bundle: AgentBundleResponse): Promise<Blob> {
  if (bundle.tarGzBase64) {
    try {
      const binaryStr = window.atob(bundle.tarGzBase64);
      const bytes = new Uint8Array(binaryStr.length);
      for (let i = 0; i < binaryStr.length; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }
      // Verify GZIP magic header 0x1f 0x8b
      if (bytes.length > 10 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
        return new Blob([bytes], { type: 'application/gzip' });
      }
    } catch {
      // Fall back to client-side CompressionStream below
    }
  }

  const entries = [
    ...bundle.files.map((f) => ({ path: f.path, content: f.content })),
    { path: 'AGENT_1TO1_REPLICATION.md', content: bundle.fullMarkdown },
  ];
  const tarBytes = buildClientUstarTarBytes(entries);
  /* 2026-10-29：TS 5.7 起 Uint8Array<ArrayBufferLike> 不再直接兼容 BlobPart / BufferSource，
     这里取一份独立 ArrayBuffer 再传，行为不变。 */
  const tarBuffer = tarBytes.buffer.slice(
    tarBytes.byteOffset,
    tarBytes.byteOffset + tarBytes.byteLength,
  ) as ArrayBuffer;

  if (typeof CompressionStream !== 'undefined') {
    const cs = new CompressionStream('gzip');
    const writer = cs.writable.getWriter();
    await writer.write(tarBuffer);
    await writer.close();
    const compressedBuffer = await new Response(cs.readable).arrayBuffer();
    return new Blob([compressedBuffer], { type: 'application/gzip' });
  }

  return new Blob([tarBuffer], { type: 'application/x-tar' });
}

function buildClientFallbackBundle(): AgentBundleResponse {
  const getLang = (p: string) => {
    if (p.endsWith('.tsx')) return 'tsx';
    if (p.endsWith('.ts')) return 'ts';
    if (p.endsWith('.json')) return 'json';
    if (p.endsWith('.html')) return 'html';
    if (p.endsWith('.css')) return 'css';
    if (p.endsWith('.mjs') || p.endsWith('.js')) return 'javascript';
    return 'text';
  };
  const files: AgentSourceFile[] = Object.entries(RAW_CLIENT_FILES)
    .map(([rawPath, content]) => {
      const cleanPath = rawPath.replace(/^\//, '');
      const text = typeof content === 'string' ? content : '';
      return {
        path: cleanPath,
        lang: getLang(cleanPath),
        content: text,
        lines: text ? text.split('\n').length : 0,
      };
    })
    .filter((f) => f.content.length > 0);

  const fullMarkdown = [
    CLIENT_PROMPT_FALLBACK,
    '',
    '---',
    '',
    '## 三、 全量最新源码文件合集',
    '',
    ...files.map((f) => `### File: \`${f.path}\`\n\`\`\`${f.lang}\n${f.content}\n\`\`\`\n`),
  ].join('\n');

  return {
    ok: true,
    updatedAt: new Date().toISOString(),
    prompt: CLIENT_PROMPT_FALLBACK,
    files,
    fullMarkdown,
  };
}

export const AgentExportModal: React.FC<AgentExportModalProps> = ({ isOpen, onClose }) => {
  const toast = useToast();
  const [bundle, setBundle] = useState<AgentBundleResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'all' | 'prompt' | 'files'>('all');
  const [selectedFilePath, setSelectedFilePath] = useState<string>('src/App.tsx');
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const fetchLatestBundle = useCallback(async (showSuccessToast = false) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/agent-bundle?t=${Date.now()}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data: AgentBundleResponse = await res.json();
      if (data && data.ok && Array.isArray(data.files) && data.files.length > 0) {
        setBundle(data);
        setSelectedFilePath((prev) =>
          data.files.some((f) => f.path === prev) ? prev : data.files[0].path,
        );
        if (showSuccessToast) {
          toast.success(`已同步最新代码（共 ${data.files.length} 个文件）`);
        }
        return;
      }
      throw new Error('Invalid bundle payload');
    } catch {
      const fallback = buildClientFallbackBundle();
      setBundle(fallback);
      setSelectedFilePath((prev) =>
        fallback.files.some((f) => f.path === prev) ? prev : fallback.files[0]?.path || 'src/App.tsx',
      );
      if (showSuccessToast) {
        toast.success(`已加载内置最新源码快照（共 ${fallback.files.length} 个文件）`);
      }
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (isOpen) {
      fetchLatestBundle(false);
    }
  }, [isOpen, fetchLatestBundle]);

  if (!isOpen) return null;

  const copyText = async (text: string, key: string, successLabel: string) => {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const el = document.createElement('textarea');
        el.value = text;
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
      }
      setCopiedKey(key);
      toast.success(successLabel, '复制成功');
      window.setTimeout(() => setCopiedKey(null), 2200);
    } catch {
      toast.error('复制未成功，请手动全选复制');
    }
  };

  const downloadMarkdownFile = () => {
    if (!bundle?.fullMarkdown) return;
    const blob = new Blob([bundle.fullMarkdown], { type: 'text/markdown;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'AGENT_1TO1_REPLICATION.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success('已下载 AGENT_1TO1_REPLICATION.md（含操作指令与全量最新代码）');
  };

  const downloadTarGzFile = async () => {
    if (!bundle) return;
    try {
      const blob = await createTarGzBlobFromBundle(bundle);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'imtask_admin_source.tar.gz';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success(
        `已生成并下载真实源码压缩包 imtask_admin_source.tar.gz（含 ${bundle.files.length} 个源码文件）`,
      );
    } catch {
      toast.error('生成源码包失败，请重试或下载 .md 全量文档');
    }
  };

  const currentFile = bundle?.files.find((f) => f.path === selectedFilePath) || bundle?.files[0];

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-5xl w-full max-h-[90vh] flex flex-col shadow-2xl border border-slate-200 overflow-hidden animate-in fade-in zoom-in-95">
        {/* 顶部标题栏 */}
        <div className="px-6 py-4 bg-white border-b border-slate-200 flex flex-wrap items-center justify-between gap-3 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center shadow-xs">
              <Terminal className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-bold text-base text-slate-900">获取最新源码与 Agent 1:1 复刻指令</h3>
                <span className="text-[11px] bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-md font-mono font-semibold">
                  实时同步当前最新代码
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                每次打开均直接从服务端磁盘实时读取最新代码文件（共 {bundle?.files.length || 22} 个文件），一键复制或下载真实源码包发给你的 Agent 即可 1:1 复刻。
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchLatestBundle(true)}
              disabled={loading}
              className="px-3 py-2 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200/80 text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span>刷新最新代码</span>
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-500 hover:text-slate-700 cursor-pointer transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 一键快捷操作条 */}
        <div className="px-6 py-3.5 bg-blue-50/70 border-b border-blue-100 flex flex-wrap items-center justify-between gap-3 shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab('all')}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'all'
                  ? 'bg-blue-600 text-white shadow-xs'
                  : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>全量指令 + 源码合集（推荐直接发给 Agent）</span>
            </button>
            <button
              onClick={() => setActiveTab('prompt')}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'prompt'
                  ? 'bg-blue-600 text-white shadow-xs'
                  : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50'
              }`}
            >
              <FileText className="w-3.5 h-3.5" />
              <span>仅查看 Agent 操作指令</span>
            </button>
            <button
              onClick={() => setActiveTab('files')}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'files'
                  ? 'bg-blue-600 text-white shadow-xs'
                  : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50'
              }`}
            >
              <FileCode className="w-3.5 h-3.5" />
              <span>按文件逐个查看与复制 ({bundle?.files.length || 0})</span>
            </button>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() =>
                bundle &&
                copyText(
                  bundle.fullMarkdown,
                  'copy_all',
                  '已复制「Agent 复刻指令 + 全量最新源码合集」，可直接粘贴发给你的 Agent！',
                )
              }
              disabled={!bundle}
              className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs flex items-center gap-1.5 shadow-xs cursor-pointer disabled:opacity-60"
            >
              {copiedKey === 'copy_all' ? (
                <Check className="w-4 h-4" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
              <span>
                {copiedKey === 'copy_all' ? '已复制全量指令+源码' : '一键复制全量指令+源码'}
              </span>
            </button>

            <button
              onClick={downloadMarkdownFile}
              disabled={!bundle}
              className="px-3.5 py-2 rounded-xl bg-white hover:bg-slate-50 text-slate-800 border border-slate-200 font-semibold text-xs flex items-center gap-1.5 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5 text-blue-600" />
              <span>下载 .md 文档</span>
            </button>

            <button
              onClick={downloadTarGzFile}
              disabled={!bundle}
              className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs flex items-center gap-1.5 shadow-xs cursor-pointer disabled:opacity-60 transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              <span>下载 .tar.gz 源码包</span>
            </button>
          </div>
        </div>

        {/* 主体内容区 */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          {loading || !bundle ? (
            <div className="p-16 text-center text-slate-400 text-xs">
              正在实时打包最新源码文件与操作指令…
            </div>
          ) : activeTab === 'all' ? (
            <div className="flex-1 flex flex-col p-5 overflow-hidden space-y-3">
              <div className="flex items-center justify-between text-xs text-slate-600">
                <span>
                  以下为实时生成的 <strong>AGENT_1TO1_REPLICATION.md</strong> 完整内容（含业务约束 + 全部{' '}
                  {bundle.files.length} 个源码文件全文）：
                </span>
                <span className="font-mono text-[11px] text-slate-400">
                  总长度：{bundle.fullMarkdown.length.toLocaleString()} 字符
                </span>
              </div>
              <textarea
                readOnly
                value={bundle.fullMarkdown}
                onFocus={(e) => e.currentTarget.select()}
                className="flex-1 w-full bg-slate-950 text-slate-100 font-mono text-xs p-4 rounded-xl border border-slate-800 outline-none resize-none leading-relaxed"
              />
            </div>
          ) : activeTab === 'prompt' ? (
            <div className="flex-1 flex flex-col p-5 overflow-hidden space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-600 font-medium">
                  Agent 1:1 复刻操作指令 Prompt（包含全部最新业务规则与执行步骤）：
                </span>
                <button
                  onClick={() =>
                    copyText(bundle.prompt, 'copy_prompt', '已复制 Agent 操作指令 Prompt')
                  }
                  className="px-3 py-1.5 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 font-semibold text-xs flex items-center gap-1.5 cursor-pointer"
                >
                  {copiedKey === 'copy_prompt' ? (
                    <Check className="w-3.5 h-3.5" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  <span>{copiedKey === 'copy_prompt' ? '已复制指令' : '复制操作指令'}</span>
                </button>
              </div>
              <textarea
                readOnly
                value={bundle.prompt}
                className="flex-1 w-full bg-slate-950 text-slate-100 font-mono text-xs p-4 rounded-xl border border-slate-800 outline-none resize-none leading-relaxed"
              />
            </div>
          ) : (
            <div className="flex-1 grid grid-cols-12 min-h-0 overflow-hidden">
              {/* 左侧文件树 */}
              <div className="col-span-4 border-r border-slate-200 bg-slate-50/70 overflow-y-auto p-3 space-y-1">
                <div className="px-2 py-1 text-[11px] font-bold text-slate-400 uppercase">
                  最新源码文件列表 ({bundle.files.length})
                </div>
                {bundle.files.map((f) => {
                  const active = f.path === currentFile?.path;
                  return (
                    <button
                      key={f.path}
                      onClick={() => setSelectedFilePath(f.path)}
                      className={`w-full text-left px-3 py-2 rounded-xl text-xs font-mono flex items-center justify-between gap-2 cursor-pointer transition-colors ${
                        active
                          ? 'bg-blue-600 text-white font-semibold shadow-2xs'
                          : 'text-slate-700 hover:bg-slate-200/70'
                      }`}
                    >
                      <span className="truncate">{f.path}</span>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${
                          active ? 'bg-white/20 text-white' : 'bg-slate-200 text-slate-600'
                        }`}
                      >
                        {f.lines}行
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* 右侧单文件代码预览与复制 */}
              <div className="col-span-8 flex flex-col p-4 overflow-hidden space-y-2.5">
                {currentFile && (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-xs text-slate-900">
                          {currentFile.path}
                        </span>
                        <span className="text-[11px] text-slate-400 font-mono">
                          ({currentFile.lines} 行)
                        </span>
                      </div>
                      <button
                        onClick={() =>
                          copyText(
                            currentFile.content,
                            `file_${currentFile.path}`,
                            `已复制 ${currentFile.path} 最新源码`,
                          )
                        }
                        className="px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs flex items-center gap-1.5 cursor-pointer"
                      >
                        {copiedKey === `file_${currentFile.path}` ? (
                          <Check className="w-3.5 h-3.5" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                        <span>
                          {copiedKey === `file_${currentFile.path}`
                            ? '已复制该文件'
                            : '复制当前文件源码'}
                        </span>
                      </button>
                    </div>
                    <textarea
                      readOnly
                      value={currentFile.content}
                      className="flex-1 w-full bg-slate-950 text-slate-100 font-mono text-xs p-4 rounded-xl border border-slate-800 outline-none resize-none leading-relaxed"
                    />
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
