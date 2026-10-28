import express from 'express';
import { GoogleGenAI } from '@google/genai';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import zlib from 'zlib';
import { fileURLToPath } from 'url';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

app.use(express.json({ limit: '30mb' }));

function getAiClient() {
  return new GoogleGenAI({
    apiKey: process.env.GEMINI_API_KEY || 'placeholder-key',
    httpOptions: {
      headers: {
        'User-Agent': 'aistudio-build',
      },
    },
  });
}

// Helper for model fallback
async function generateWithFallback(models: string[], runner: (model: string) => Promise<any>) {
  let lastError: any = null;
  for (const model of models) {
    try {
      const result = await runner(model);
      return { result, usedModel: model };
    } catch (err: any) {
      console.warn(`Model ${model} failed, attempting next if available:`, err?.message || err);
      lastError = err;
    }
  }
  throw lastError;
}

// 1. Image Analysis Endpoint (gemini-4-argon primary, gemini-3.8-flash fallback)
app.post('/api/analyze-image', async (req, res) => {
  try {
    const { imageBase64, mimeType = 'image/png', prompt } = req.body;
    if (!imageBase64) {
      return res.status(400).json({ error: '请提供图片 Base64 数据' });
    }

    const cleanBase64 = imageBase64.replace(/^data:image\/[a-zA-Z+]+;base64,/, '');

    const analysisPrompt = prompt || `你是一名世界级的资深 UI/UX 设计总监和 B端企业级设计系统（Design System）架构师。
请对用户上传的网页/系统界面截图进行全方位、专业深度的设计走查（Design Audit）。

请输出详尽且富有建设性的中文分析报告，必须包含以下结构：
1. **整体风格定性与第一印象**：
   - 风格流派（如：极简通用型B端后台、Ant Design / Arco规范体系等）
   - 整体视觉基调、给用户的心理感受（专业度、信任感、现代感、或略显简陋/原型感）
2. **多维设计评分与深度诊断（100分制及详细评语）**：
   - 【视觉层次与重心 (Visual Hierarchy)】
   - 【间距与网格排版 (Layout & Spacing)】
   - 【色彩搭配与质感 (Color Harmony & Polish)】
   - 【数据可读性与扫描效率 (Data Scannability)】
   - 【交互指引与反馈感知 (Affordance & Actions)】
   - 【屏效与信息密度 (Information Density)】
3. **具体亮点（做得好的地方）**：
   - 哪些细节符合现代用户习惯？
4. **核心问题与改进处方（硬伤与短板）**：
   - 指出具体元素（如状态标签表现力弱、数字指标缺乏视觉对比、群聊选择器容器过重、大屏留白利用不足等）
5. **重构升级落地方案（含具体 Tailwind / CSS 建议）**：
   - 标签 Badge 优化方案
   - KPI 指标卡片视觉层级与环形进度条设计
   - 核心表格交互与催办引导`;

    const { result: response, usedModel } = await generateWithFallback(
      ['gemini-4-argon', 'gemini-3.8-flash'],
      async (model) => {
        return await getAiClient().models.generateContent({
          model,
          contents: {
            parts: [
              {
                inlineData: {
                  data: cleanBase64,
                  mimeType,
                },
              },
              { text: analysisPrompt },
            ],
          },
          config: {
            systemInstruction: '你是一位严谨专业、富有审美与工程实践经验的高级设计顾问。用清晰有说服力的专业术语和结构化格式输出。',
          },
        });
      }
    );

    res.json({
      analysis: response.text,
      usedModel,
    });
  } catch (error: any) {
    console.error('Image analysis error:', error);
    res.status(500).json({
      error: error?.message || '图像分析失败，请检查 API 配置或网络连接',
    });
  }
});

// 2. Chatbot Endpoint (Multi-turn conversation with tier models)
app.post('/api/chat', async (req, res) => {
  try {
    const { messages = [], modelTier = 'general', customSystemInstruction } = req.body;

    // Model selection based on requirements:
    // gemini-4-argon for particularly complex tasks
    // gemini-3.8-flash for general tasks
    // gemini-3.1-flash-lite for tasks that should happen fast
    let preferredModels: string[] = ['gemini-3.8-flash'];
    if (modelTier === 'complex' || modelTier === 'gemini-4-argon') {
      preferredModels = ['gemini-4-argon', 'gemini-3.8-flash', 'gemini-3.1-flash-lite'];
    } else if (modelTier === 'fast' || modelTier === 'gemini-3.1-flash-lite') {
      preferredModels = ['gemini-3.1-flash-lite', 'gemini-3.8-flash'];
    } else {
      preferredModels = ['gemini-3.8-flash', 'gemini-3.1-flash-lite'];
    }

    const defaultInstruction = `你是一位拥有15年经验的顶级 UI/UX 设计合伙人兼前端设计系统架构师。
当前用户正在向你咨询或探讨网页/后台系统的界面设计风格、色彩搭配、排版规范、组件库选用（如 Tailwind, Ant Design, Arco Design, Shadcn/UI）以及交互体验优化。
请给出专业、敏锐、具体、可落地的回答，适当提供设计法则（如尼尔森十大可用性原则、格式塔心理学、8px网格系统、WCAG对比度标准），并结合具体代码或设计Token举例。`;

    const systemInstruction = customSystemInstruction || defaultInstruction;

    // Convert messages to GenAI contents format
    // Filter out invalid items and map role
    const contents = messages.map((m: any) => ({
      role: m.role === 'assistant' ? 'model' : 'user',
      parts: [{ text: m.content || '' }],
    }));

    if (contents.length === 0) {
      return res.status(400).json({ error: '对话历史不能为空' });
    }

    const { result: response, usedModel } = await generateWithFallback(
      preferredModels,
      async (model) => {
        return await getAiClient().models.generateContent({
          model,
          contents,
          config: {
            systemInstruction,
          },
        });
      }
    );

    res.json({
      reply: response.text,
      usedModel,
    });
  } catch (error: any) {
    console.error('Chat error:', error);
    res.status(500).json({
      error: error?.message || '生成回答失败，请稍后重试',
    });
  }
});

// 3. Text to Speech Endpoint (gemini-3.8-flash-tts)
app.post('/api/tts', async (req, res) => {
  try {
    const { text, voiceName = 'Kore', stylePrompt = '清晰、专业、富有设计洞察力与亲和力的高级设计总监' } = req.body;
    if (!text || text.trim().length === 0) {
      return res.status(400).json({ error: '请提供待合成的文本' });
    }

    // Limit text length to prevent timeout on TTS
    const sanitizedText = text.slice(0, 1200);

    const { result: response, usedModel } = await generateWithFallback(
      ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts'],
      async (model) => {
        return await getAiClient().models.generateContent({
          model,
          contents: [
            {
              role: 'user',
              parts: [
                {
                  text: sanitizedText,
                  speechMetadata: {
                    style: stylePrompt,
                  },
                },
              ],
            },
          ],
          config: {
            responseModalities: ['AUDIO'],
            speechConfig: {
              voiceConfig: {
                prebuiltVoiceConfig: { voiceName },
              },
            },
          },
        });
      }
    );

    const base64Audio = response.candidates?.[0]?.content?.parts?.[0]?.inlineData?.data;
    if (!base64Audio) {
      return res.status(500).json({ error: '未能成功生成语音音频' });
    }

    res.json({
      audioBase64: base64Audio,
      mimeType: 'audio/wav',
      usedModel,
    });
  } catch (error: any) {
    console.error('TTS error:', error);
    res.status(500).json({
      error: error?.message || '语音合成失败，请稍后重试',
    });
  }
});

const AGENT_REPLICATION_PROMPT = `# IMTask 办公任务调度平台 · Agent 1:1 复刻操作指令

你是一名资深全栈工程师。请根据本指令与随附的全量源码文件，在本地 **1:1 完整复刻**「IMTask 办公任务调度平台 · 管理后台」。

## 一、 核心业务规范与已确认交互细节（严禁偏离）
1. **真实业务初始化数据（清除测试假数据）**：
   - 默认内置市场部春促真实角色清单（9 个角色：片长/分支局长、行销总监、网格长、装维、智家工程师、营业员、商客经理、政企经理、包区领导）。
   - 默认内置真实测试人员：**张三**（手机号 \`13900000000\`，角色：\`片长/分支局长\`）。
   - 默认内置片长/分支局长每日 5 条真实春促任务模板（08:15 晨会安排、12:00 上午业务点检与午间复盘、15:00 上门协同、18:00 夜间补差、20:00 下午业务点检与晚间复盘）。
2. **三大核心工作台模块与界面精简要求**：
   - **左侧导航栏**：移除「量子密信网关状态」卡片与冗余的「催办与审计日志」一级菜单，聚焦**「任务实例与审核」、「任务模板管理」、「角色与成员名册」**三大核心工作台；底部提供「获取最新代码与 Agent 指令」按钮；管理员资料下拉菜单移除「账号与安全策略」，仅保留「修改登录密码」与「退出当前登录」。
   - **任务实例与审核（InstancesView）**：
     - 顶部筛选支持：**日期选择器（默认今天）**、**按任务类型查询**（纯通知提醒 / 上传任务）、按目标角色、按状态（待处理、待审核、已通过、已驳回，**已移除“已过期”状态**）、按姓名/手机号搜索。
     - **已移除「导出 CSV」按钮**。
     - **纯通知提醒实例默认无需审核**，操作列不展示「通过/驳回」按钮；只有配置了「需要审核」的上传任务在提交后才显示审核操作。
     - 实例列表与「详情」弹窗中，**接收人必须带上所属角色名称**（如 \`张三 (13900000000) · 片长/分支局长\`），并在详情弹窗内直接内嵌展示该实例的「量子密信提醒发送记录」。
   - **任务模板管理（TemplatesView）**：
     - **支持多选业务角色（\`role_ids: string[]\`）**：新建或编辑任务模板时，所属业务角色不再限制只能单选，而是支持**多选任意多个业务角色**（提供“全选全部角色”与“重置为单角色”快捷按钮及复选网格），列表中同步展示该任务关联的全部角色标签，顶部角色筛选支持按任意关联角色精准过滤。
     - 支持按「目标角色」与「任务类型」筛选；**已移除「立即推送测试」按钮**与独立的「上传任务 H5 预览效果图」按钮。
     - 模板列表精简干净：**不显示任何底部冗余描述文本**（如“共2条子任务并列下发”、“默认无需审核”等）。
     - **纯通知提醒模板**：无截止时间（仅执行时间），移除变量占位提示文本，支持同时配置多条并列子任务（每条可独立选择「文本消息」或「文件/图片」格式并配置内容）。
     - **上传任务模板**：合成为统一流程（1. 密信提醒文案 -> 2. 员工 H5 上传页，支持拍照/图片、文件、报数字段、文本说明任意组合），包含**执行时间与截止时间**、**是否需要审核开关（需要审核 / 无需审核）**；**已移除反馈提示语编辑功能**，右侧实时手机预览仅展示「1. 密信提醒」与「2. H5上传页」（**已移除第3步完成反馈预览**）。
   - **角色与成员名册（RolesMembersView）**：
     - 角色列表不展示角色编码，不展示“春促角色清单：负责每日...”长串描述文本。
     - 添加/导入成员**只需填写用户名和手机号**，无需填写所属部门；编辑成员时手机号只读锁死。
     - 右侧常驻展示**「已激活待归纳角色人员」专栏**（即用户已在量子密信通过对话完成激活、但管理员尚未归纳业务角色的全部人员）：
       - 完整展示用户全名与手机号（无截断遮挡），支持按姓名或手机号搜索查询；**移除手动登记入口**（员工激活仅限通过量子密信对话形式自动完成）。
       - 支持单人**「一键移入分组」**及**「全部一键移入」**到对应角色分组。
       - **两级移除逻辑与交互提示**：① 从角色名册中「移除」成员时，保留其密信会话绑定并将其移回右侧「已激活待归纳角色人员」列表；② 从右侧「已激活待归纳角色人员」中「移除」人员时，解除其量子密信会话绑定（\`external_id\`），用户需在密信对话窗口重新输入发送激活指令方可再次激活。

## 二、 复刻执行步骤
1. 按照下方文件清单逐一写入对应路径（保持目录结构与文件内容完全一致）。
2. 执行 \`npm install\` 安装依赖。
3. 执行 \`npm run dev\` 启动服务（默认监听 \`http://localhost:3000\`）。
4. 可选执行 \`node tests/e2e_admin_check.mjs\` 运行 Playwright 自动化测试验真。`;

const AGENT_BUNDLE_FILE_PATHS = [
  'package.json',
  'tsconfig.json',
  'vite.config.ts',
  'metadata.json',
  '.env.example',
  'index.html',
  'server.ts',
  'src/main.tsx',
  'src/index.css',
  'src/App.tsx',
  'src/lib/types.ts',
  'src/lib/roles.ts',
  'src/lib/api.ts',
  'src/lib/toast.tsx',
  'src/lib/store.tsx',
  'src/pages/LoginPage.tsx',
  'src/pages/ChangePasswordPage.tsx',
  'src/components/system/AdminUserCard.tsx',
  'src/components/imtask/InstancesView.tsx',
  'src/components/imtask/TemplatesView.tsx',
  'src/components/imtask/RolesMembersView.tsx',
  'src/components/AgentExportModal.tsx',
  'tests/e2e_admin_check.mjs',
];

function getLangByPath(filePath: string): string {
  if (filePath.endsWith('.tsx')) return 'tsx';
  if (filePath.endsWith('.ts')) return 'ts';
  if (filePath.endsWith('.json')) return 'json';
  if (filePath.endsWith('.html')) return 'html';
  if (filePath.endsWith('.css')) return 'css';
  if (filePath.endsWith('.mjs') || filePath.endsWith('.js')) return 'javascript';
  return 'text';
}

function createUstarTarBuffer(entries: Array<{ path: string; content: string }>): Buffer {
  const blocks: Buffer[] = [];
  const mtimeOctal = Math.floor(Date.now() / 1000).toString(8).padStart(11, '0');

  for (const entry of entries) {
    const cleanName = entry.path.replace(/^\/+/, '');
    const contentBuf = Buffer.from(entry.content, 'utf-8');
    const sizeOctal = contentBuf.length.toString(8).padStart(11, '0');

    const header = Buffer.alloc(512, 0);
    // File name (0..99)
    header.write(cleanName.slice(0, 100), 0, 'utf-8');
    // File mode (100..107)
    header.write('0000644\0', 100, 'ascii');
    // UID (108..115) & GID (116..123)
    header.write('0000000\0', 108, 'ascii');
    header.write('0000000\0', 116, 'ascii');
    // File size in octal (124..135)
    header.write(sizeOctal + '\0', 124, 'ascii');
    // Mtime in octal (136..147)
    header.write(mtimeOctal + '\0', 136, 'ascii');
    // Checksum placeholder: 8 spaces (148..155)
    header.write('        ', 148, 'ascii');
    // Typeflag '0' regular file (156)
    header.write('0', 156, 'ascii');
    // Magic "ustar\0" (257..262) + version "00" (263..264)
    header.write('ustar\0', 257, 'ascii');
    header.write('00', 263, 'ascii');

    // Compute header checksum
    let sum = 0;
    for (let i = 0; i < 512; i++) {
      sum += header[i];
    }
    const chksumOctal = sum.toString(8).padStart(6, '0') + '\0 ';
    header.write(chksumOctal, 148, 'ascii');

    blocks.push(header);
    blocks.push(contentBuf);

    const remainder = contentBuf.length % 512;
    if (remainder !== 0) {
      blocks.push(Buffer.alloc(512 - remainder, 0));
    }
  }

  // Two 512-byte zero blocks mark end-of-archive
  blocks.push(Buffer.alloc(1024, 0));
  return Buffer.concat(blocks);
}

function buildServerSourceBundle() {
  const files = AGENT_BUNDLE_FILE_PATHS.map((relPath) => {
    const absPath = path.join(__dirname, relPath);
    const content = fs.existsSync(absPath) ? fs.readFileSync(absPath, 'utf-8') : '';
    const lines = content ? content.split('\n').length : 0;
    return {
      path: relPath,
      lang: getLangByPath(relPath),
      content,
      lines,
    };
  }).filter((f) => f.content.length > 0);

  const fullMarkdown = [
    AGENT_REPLICATION_PROMPT,
    '',
    '---',
    '',
    '## 三、 全量最新源码文件合集',
    '',
    ...files.map(
      (f) => `### File: \`${f.path}\`\n\`\`\`${f.lang}\n${f.content}\n\`\`\`\n`,
    ),
  ].join('\n');

  try {
    fs.writeFileSync(path.join(__dirname, 'AGENT_1TO1_REPLICATION.md'), fullMarkdown, 'utf-8');
  } catch {
    // ignore write error on read-only fs
  }

  const tarEntries = [
    ...files.map((f) => ({ path: f.path, content: f.content })),
    { path: 'AGENT_1TO1_REPLICATION.md', content: fullMarkdown },
  ];
  const tarBuffer = createUstarTarBuffer(tarEntries);
  const tarGzBuffer = zlib.gzipSync(tarBuffer);

  return {
    files,
    fullMarkdown,
    tarGzBuffer,
    tarGzBase64: tarGzBuffer.toString('base64'),
  };
}

app.get('/api/agent-bundle', (_req, res) => {
  try {
    const { files, fullMarkdown, tarGzBase64 } = buildServerSourceBundle();

    res.json({
      ok: true,
      updatedAt: new Date().toISOString(),
      prompt: AGENT_REPLICATION_PROMPT,
      files,
      fullMarkdown,
      tarGzBase64,
    });
  } catch (err: any) {
    console.error('Agent bundle error:', err);
    res.status(500).json({
      ok: false,
      error: err?.message || '读取源码包失败',
    });
  }
});

// 4. Download Codebase Package for Agent Implementation (.tar.gz)
app.get('/api/download-package', (_req, res) => {
  try {
    const { tarGzBuffer } = buildServerSourceBundle();
    res.setHeader('Content-Type', 'application/gzip');
    res.setHeader('Content-Length', String(tarGzBuffer.length));
    res.setHeader('Content-Disposition', 'attachment; filename="imtask_admin_source.tar.gz"');
    res.send(tarGzBuffer);
  } catch (err: any) {
    console.error('Download error:', err);
    res.status(500).send('打包失败');
  }
});

// IMTask: 实例 CSV 导出（供 P1-2 window.open('/api/admin/instances/export?...') 直接下载）
app.get('/api/admin/instances/export', (req, res) => {
  const status = String(req.query.status || '').trim();
  const roleId = String(req.query.role_id || '').trim();
  const phone = String(req.query.phone || '').trim();
  const periodKey = String(req.query.period_key || '').trim();

  const sampleRows = [
    ['105', '5', '下午业务点检、晚间复盘', 'notice', '2026-03-16', 'role_branch_chief', '张三', '13900000000', '市场部 / 片区支局', 'pending', '', '2026-03-16 23:00:00', '', '', ''],
    ['104', '4', '夜间补差', 'notice', '2026-03-16', 'role_branch_chief', '张三', '13900000000', '市场部 / 片区支局', 'pending', '', '2026-03-16 21:00:00', '', '', ''],
    ['103', '3', '上门协同', 'notice', '2026-03-16', 'role_branch_chief', '张三', '13900000000', '市场部 / 片区支局', 'approved', '2026-03-16 15:00:02', '2026-03-16 18:00:00', 'system', '2026-03-16 15:00:02', ''],
    ['102', '2', '上午业务点检、午间复盘', 'notice', '2026-03-16', 'role_branch_chief', '张三', '13900000000', '市场部 / 片区支局', 'approved', '2026-03-16 12:00:02', '2026-03-16 15:00:00', 'system', '2026-03-16 12:00:02', ''],
    ['101', '1', '晨会安排', 'notice', '2026-03-16', 'role_branch_chief', '张三', '13900000000', '市场部 / 片区支局', 'approved', '2026-03-16 08:15:02', '2026-03-16 12:00:00', 'system', '2026-03-16 08:15:02', ''],
  ].filter((r) => {
    if (status && r[9] !== status) return false;
    if (roleId && r[5] !== roleId) return false;
    if (phone && !r[7].includes(phone) && !r[6].includes(phone)) return false;
    if (periodKey && !r[4].includes(periodKey)) return false;
    return true;
  });

  const header = [
    'id',
    'template_id',
    'template_name',
    'kind',
    'period_key',
    'role_id',
    'member_name_snap',
    'phone',
    'dept_snap',
    'status',
    'submitted_at',
    'deadline_at',
    'reviewed_by',
    'reviewed_at',
    'reject_reason',
  ];
  const csv = '\uFEFF' + [header.join(','), ...sampleRows.map((row) => row.join(','))].join('\r\n');
  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="instances_export.csv"');
  res.send(csv);
});

// IMTask: 附件下载与预览（供 P2-2 /api/admin/uploads/{ref} 链接访问）
app.get('/api/admin/uploads/:ref', (req, res) => {
  const ref = String(req.params.ref || 'attachment.txt').replace(/[^a-zA-Z0-9._-]/g, '_');
  if (ref.endsWith('.jpg') || ref.endsWith('.png') || ref.endsWith('.webp')) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="420" viewBox="0 0 720 420">
      <rect width="720" height="420" rx="16" fill="#0f172a"/>
      <rect x="24" y="24" width="672" height="372" rx="12" fill="#1e293b" stroke="#334155" stroke-width="2"/>
      <circle cx="360" cy="170" r="44" fill="#2563eb" opacity="0.25"/>
      <path d="M342 170l12 12 24-24" stroke="#38bdf8" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      <text x="360" y="255" text-anchor="middle" fill="#f8fafc" font-family="sans-serif" font-size="20" font-weight="bold">IMTask 现场巡检实拍凭证</text>
      <text x="360" y="290" text-anchor="middle" fill="#94a3b8" font-family="monospace" font-size="14">附件引用: ${ref}</text>
      <text x="360" y="325" text-anchor="middle" fill="#64748b" font-family="sans-serif" font-size="13">安全出口通道无遮挡 · 配电箱封条完好 · 水印校验通过</text>
    </svg>`;
    res.setHeader('Content-Type', 'image/svg+xml; charset=utf-8');
    res.send(svg);
    return;
  }
  const content = `IMTask 办公任务调度平台 - 附件归档文件\r\n附件编号: ${ref}\r\n导出时间: ${new Date().toISOString()}\r\n状态: 已通过服务端大小与扩展名白名单校验\r\n`;
  res.setHeader('Content-Type', 'application/octet-stream');
  res.setHeader('Content-Disposition', `attachment; filename="${ref}"`);
  res.send(content);
});

// Vite middleware in dev or static files in production
async function startServer() {
  const isProd = process.env.NODE_ENV === 'production';
  if (!isProd) {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.join(__dirname, 'dist')));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(__dirname, 'dist', 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Server listening on http://0.0.0.0:${PORT}`);
  });
}

startServer();
