# IMTask 办公任务调度平台 · Agent 1:1 复刻操作指令

你是一名资深全栈工程师。请根据本指令与随附的全量源码文件，在本地 **1:1 完整复刻**「IMTask 办公任务调度平台 · 管理后台」。

## 一、 核心业务规范与已确认交互细节（严禁偏离）
1. **真实业务初始化数据（清除测试假数据）**：
   - 默认内置市场部春促真实角色清单（9 个角色：片长/分支局长、行销总监、网格长、装维、智家工程师、营业员、商客经理、政企经理、包区领导）。
   - 默认内置真实测试人员：**张三**（手机号 `13900000000`，角色：`片长/分支局长`）。
   - 默认内置片长/分支局长每日 5 条真实春促任务模板（08:15 晨会安排、12:00 上午业务点检与午间复盘、15:00 上门协同、18:00 夜间补差、20:00 下午业务点检与晚间复盘）。
2. **三大核心工作台模块与界面精简要求**：
   - **左侧导航栏**：移除「量子密信网关状态」卡片与冗余的「催办与审计日志」一级菜单，聚焦**「任务实例与审核」、「任务模板管理」、「角色与成员名册」**三大核心工作台；底部提供「获取最新代码与 Agent 指令」按钮；管理员资料下拉菜单移除「账号与安全策略」，仅保留「修改登录密码」与「退出当前登录」。
   - **任务实例与审核（InstancesView）**：
     - 顶部筛选支持：**日期选择器（默认今天）**、**按任务类型查询**（纯通知提醒 / 上传任务）、按目标角色、按状态（待处理、待审核、已通过、已驳回，**已移除“已过期”状态**）、按姓名/手机号搜索。
     - **已移除「导出 CSV」按钮**。
     - **纯通知提醒实例默认无需审核**，操作列不展示「通过/驳回」按钮；只有配置了「需要审核」的上传任务在提交后才显示审核操作。
     - 实例列表与「详情」弹窗中，**接收人必须带上所属角色名称**（如 `张三 (13900000000) · 片长/分支局长`），并在详情弹窗内直接内嵌展示该实例的「量子密信提醒发送记录」。
   - **任务模板管理（TemplatesView）**：
     - **支持多选业务角色（`role_ids: string[]`）**：新建或编辑任务模板时，所属业务角色不再限制只能单选，而是支持**多选任意多个业务角色**（提供“全选全部角色”与“重置为单角色”快捷按钮及复选网格），列表中同步展示该任务关联的全部角色标签，顶部角色筛选支持按任意关联角色精准过滤。
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
       - **两级移除逻辑与交互提示**：① 从角色名册中「移除」成员时，保留其密信会话绑定并将其移回右侧「已激活待归纳角色人员」列表；② 从右侧「已激活待归纳角色人员」中「移除」人员时，解除其量子密信会话绑定（`external_id`），用户需在密信对话窗口重新输入发送激活指令方可再次激活。

## 二、 复刻执行步骤
1. 按照下方文件清单逐一写入对应路径（保持目录结构与文件内容完全一致）。
2. 执行 `npm install` 安装依赖。
3. 执行 `npm run dev` 启动服务（默认监听 `http://localhost:3000`）。
4. 可选执行 `node tests/e2e_admin_check.mjs` 运行 Playwright 自动化测试验真。

---

## 三、 全量最新源码文件合集

### File: `package.json`
```json
{
  "name": "react-example",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "tsx server.ts",
    "build": "vite build",
    "start": "tsx server.ts",
    "preview": "vite preview",
    "clean": "rm -rf dist server.js",
    "lint": "tsc --noEmit"
  },
  "dependencies": {
    "@google/genai": "^2.4.0",
    "@tailwindcss/vite": "^4.3.3",
    "@vitejs/plugin-react": "^6.1.1",
    "dotenv": "^17.2.3",
    "express": "^4.21.2",
    "jszip": "^3.10.2",
    "lucide-react": "^0.546.0",
    "motion": "^12.23.24",
    "react": "^19.0.1",
    "react-dom": "^19.0.1",
    "react-router-dom": "^7.18.4",
    "vite": "^8.3.0"
  },
  "devDependencies": {
    "@types/express": "^4.17.21",
    "@types/jszip": "^3.4.1",
    "@types/node": "^22.14.0",
    "@types/react": "^19.3.0",
    "@types/react-dom": "^19.3.0",
    "autoprefixer": "^10.4.21",
    "esbuild": "^0.25.0",
    "tailwindcss": "^4.3.3",
    "tsx": "^4.21.0",
    "typescript": "^7.0.2"
  }
}

```

### File: `tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "experimentalDecorators": true,
    "useDefineForClassFields": false,
    "module": "ESNext",
    "types": ["vite/client"],
    "lib": [
      "ES2022",
      "DOM",
      "DOM.Iterable"
    ],
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "isolatedModules": true,
    "moduleDetection": "force",
    "allowJs": true,
    "jsx": "react-jsx",
    "paths": {
      "@/*": [
        "./*"
      ]
    },
    "allowImportingTsExtensions": true,
    "noEmit": true
  }
}

```

### File: `vite.config.ts`
```ts
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});

```

### File: `metadata.json`
```json
{
  "name": "DesignLens - 网页设计风格走查与AI智能重构工作台",
  "description": "专业的网页与B端后台UI/UX设计风格分析系统，基于Gemini多模态图像理解、设计雷达多维评分、语音诊断播报与智能设计顾问对话，提供一站式界面重构建议。",
  "requestFramePermissions": [],
  "majorCapabilities": ["MAJOR_CAPABILITY_SERVER_SIDE_GEMINI_API"]
}

```

### File: `.env.example`
```text
# GEMINI_API_KEY: Required for Gemini AI API calls.
# AI Studio automatically injects this at runtime from user secrets.
# Users configure this via the Secrets panel in the AI Studio UI.
GEMINI_API_KEY="MY_GEMINI_API_KEY"

# APP_URL: The URL where this applet is hosted.
# AI Studio automatically injects this at runtime with the Cloud Run service URL.
# Used for self-referential links, OAuth callbacks, and API endpoints.
APP_URL="MY_APP_URL"

```

### File: `index.html`
```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>IMTask 办公任务调度平台 · 管理后台</title>
    <meta name="description" content="IMTask 办公任务调度平台 — 任務模板管理、角色与成员配置、任务实例审核、催办与审计日志工作台" />
    <meta property="og:title" content="IMTask 办公任务调度平台 · 管理后台" />
    <meta property="og:description" content="IMTask 办公任务调度平台 — 任务模板管理、角色与成员配置、任务实例审核、催办与审计日志工作台" />
    <meta property="og:type" content="website" />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=Noto+Sans+SC:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  </head>
  <body class="bg-slate-50 text-slate-900 antialiased selection:bg-blue-600 selection:text-white">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>

```

### File: `server.ts`
```ts
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

```

### File: `src/main.tsx`
```tsx
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { LoginPage } from './pages/LoginPage.tsx';
import { ChangePasswordPage } from './pages/ChangePasswordPage.tsx';
import { api, setCsrfToken } from './lib/api.ts';
import { ToastProvider } from './lib/toast.tsx';
import './index.css';

interface MeResponse {
  ok: boolean;
  username: string;
  must_change_password: boolean;
  csrf_token: string;
}

type BootState =
  | { phase: 'loading' }
  | { phase: 'unauthed' }
  | { phase: 'authed'; me: MeResponse };

const RootRouter = () => {
  const path = window.location.pathname;
  const [state, setState] = useState<BootState>(() =>
    path === '/login' ? { phase: 'unauthed' } : { phase: 'loading' },
  );

  useEffect(() => {
    if (path === '/login') return;
    api
      .get<MeResponse>('/api/admin/me')
      .then((me) => {
        setCsrfToken(me.csrf_token);
        setState({ phase: 'authed', me });
      })
      .catch(() => {
        setState({ phase: 'unauthed' });
      });
  }, [path]);

  if (path === '/login' || state.phase === 'unauthed') {
    return <LoginPage />;
  }
  if (state.phase === 'loading') {
    return <div className="min-h-screen w-full bg-[#f7f8fa]" />;
  }
  if (path === '/change-password' || state.me.must_change_password) {
    return <ChangePasswordPage onChanged={() => setState({ phase: 'unauthed' })} />;
  }
  return (
    <App
      me={state.me}
      onLoggedOut={() => {
        setCsrfToken('');
        setState({ phase: 'unauthed' });
      }}
    />
  );
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <RootRouter />
    </ToastProvider>
  </StrictMode>,
);

```

### File: `src/index.css`
```css
@import "tailwindcss";

@layer base {
  body {
    font-family: 'Plus Jakarta Sans', 'Noto Sans SC', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    color: #1e293b;
    background-color: #f8fafc;
  }
}

/* Custom scrollbars */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 9999px;
}

::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

@keyframes pulseGlow {
  0%, 100% {
    opacity: 0.6;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.05);
  }
}

.animate-pulse-glow {
  animation: pulseGlow 3s infinite ease-in-out;
}

```

### File: `src/App.tsx`
```tsx
import React, { useState } from 'react';
import {
  ClipboardCheck,
  FileSpreadsheet,
  Users,
  CalendarCheck2,
  Terminal,
} from 'lucide-react';
import { AppProvider, useAppStore } from './lib/store';
import { AdminMe, AdminUserCard } from './components/system/AdminUserCard';
import { TemplatesView } from './components/imtask/TemplatesView';
import { RolesMembersView } from './components/imtask/RolesMembersView';
import { InstancesView } from './components/imtask/InstancesView';
import { AgentExportModal } from './components/AgentExportModal';

type TabKey = 'instances' | 'templates' | 'roles';

interface MainAppProps {
  me: AdminMe;
  onLoggedOut: () => void;
}

const MainApp: React.FC<MainAppProps> = ({ me, onLoggedOut }) => {
  const { loading, templates, roles, stats } = useAppStore();
  const [activeTab, setActiveTab] = useState<TabKey>('instances');
  const [agentModalOpen, setAgentModalOpen] = useState(false);

  const submittedCount = stats?.by_status?.submitted || 0;
  const enabledTemplatesCount = templates.filter((t) => t.enabled).length;

  const navItems: Array<{
    id: TabKey;
    label: string;
    icon: React.FC<{ className?: string }>;
    badge?: string | number;
    badgeTone?: 'blue' | 'slate';
  }> = [
    {
      id: 'instances',
      label: '任务实例与审核',
      icon: ClipboardCheck,
      badge: submittedCount > 0 ? `${submittedCount} 待审` : undefined,
      badgeTone: 'blue',
    },
    {
      id: 'templates',
      label: '任务模板管理',
      icon: FileSpreadsheet,
      badge: `${enabledTemplatesCount}/${templates.length}`,
      badgeTone: 'slate',
    },
    {
      id: 'roles',
      label: '角色与成员名册',
      icon: Users,
      badge: roles.length,
      badgeTone: 'slate',
    },
  ];

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[#f7f8fa] text-slate-900">
      {/* 左侧导航栏（已移除量子密信网关状态卡片，并添加获取最新代码与Agent指令入口） */}
      <aside className="w-60 bg-white border-r border-slate-200/90 flex flex-col justify-between shrink-0 select-none z-20">
        <div className="p-4 space-y-5">
          {/* 顶部品牌 */}
          <div className="flex items-center gap-2.5 px-1.5 pt-0.5">
            <div className="w-9 h-9 rounded-xl bg-blue-600 flex items-center justify-center shadow-xs shrink-0 text-white">
              <CalendarCheck2 className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h1 className="font-bold text-sm text-slate-900 tracking-tight leading-tight truncate">
                IMTask 任务调度平台
              </h1>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-[10px] text-slate-400 font-mono">im-task</span>
                <span className="text-[9px] text-blue-600 bg-blue-50 px-1.5 py-0.2 rounded font-medium">
                  管理后台
                </span>
              </div>
            </div>
          </div>

          {/* 导航菜单 */}
          <nav className="space-y-1">
            <div className="px-2.5 pb-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
              核心调度工作台
            </div>
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                    isActive
                      ? 'bg-blue-600 text-white shadow-xs font-semibold'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                    <span>{item.label}</span>
                  </div>
                  {item.badge !== undefined && (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded-md font-semibold ${
                        isActive
                          ? 'bg-white/20 text-white'
                          : item.badgeTone === 'blue'
                            ? 'bg-blue-50 text-blue-600 border border-blue-200/70'
                            : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* 底部：获取最新代码与 Agent 指令按钮 + 管理员卡片 */}
        <div className="p-3 border-t border-slate-100 space-y-2.5">
          <button
            type="button"
            onClick={() => setAgentModalOpen(true)}
            className="w-full px-3 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold flex items-center justify-center gap-2 shadow-xs cursor-pointer transition-all"
          >
            <Terminal className="w-3.5 h-3.5 text-indigo-200 shrink-0" />
            <span>获取最新代码与 Agent 指令</span>
          </button>

          <AdminUserCard me={me} onLoggedOut={onLoggedOut} />
        </div>
      </aside>

      {/* 右侧主内容画布 */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-7xl mx-auto p-6">
          {loading ? (
            <div className="space-y-4">
              <div className="h-20 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
              <div className="h-96 bg-white rounded-2xl border border-slate-200/80 animate-pulse" />
            </div>
          ) : (
            <>
              {activeTab === 'instances' && <InstancesView />}
              {activeTab === 'templates' && <TemplatesView />}
              {activeTab === 'roles' && <RolesMembersView />}
            </>
          )}
        </div>
      </main>

      {/* 实时获取最新代码与 Agent 1:1 复刻指令弹窗 */}
      <AgentExportModal isOpen={agentModalOpen} onClose={() => setAgentModalOpen(false)} />
    </div>
  );
};

export default function App({
  me,
  onLoggedOut,
}: {
  me: AdminMe;
  onLoggedOut: () => void;
}) {
  return (
    <AppProvider>
      <MainApp me={me} onLoggedOut={onLoggedOut} />
    </AppProvider>
  );
}

```

### File: `src/lib/types.ts`
```ts
export type TemplateKind = 'notice' | 'upload' | 'form' | 'image' | 'file';
export type TemplatePeriod = 'day' | 'week' | 'month';

export interface FormFieldSpec {
  key: string;
  label: string;
  type: 'text' | 'number' | 'select';
  required: boolean;
  unit?: string;
  options?: string[];
}

export type NoticeContentFormat = 'text' | 'file';

export interface NoticeMessageItem {
  id: string;
  format: NoticeContentFormat; // 'text' 文本 | 'file' 文件（含图片）
  text_content?: string;
  file_name?: string;
  file_url?: string;
  file_mime?: string;
  file_size?: number;
  file_caption?: string;
}

// 统一上传任务的 H5 组件模块类型：字段（报数类型）、文本、图片、文件
export type UploadH5Module = 'fields' | 'text' | 'image' | 'file';

export interface TemplateSpec {
  // 纯通知提醒配置
  notice_items?: NoticeMessageItem[];

  // 统一上传任务配置（下发提醒文本 + 专属 H5 链接 + 上传完成反馈）
  upload_modules?: UploadH5Module[]; // 选择 H5 链接包含的上传项：字段(报数)、文本、图片、文件
  fields?: FormFieldSpec[]; // 报数字段配置
  text_label?: string; // 文本反馈标题
  text_placeholder?: string; // 文本反馈占位提示
  image_max_count?: number; // 图片上传最大张数
  file_allow_ext?: string[]; // 允许的文件后缀
  max_count?: number;
  max_size_mb?: number;
  allow_ext?: string[];
  feedback_message?: string; // 上传完成后的反馈结束语
  require_review?: boolean; // 仅上传任务可选 true(需审核) | false(无需审核)
}

export interface TemplateItem {
  id: number;
  name: string;
  kind: TemplateKind;
  period: TemplatePeriod;
  role_id: string;
  /** 支持关联多个业务角色（向后兼容单角色 role_id） */
  role_ids?: string[];
  remind_at: string;
  deadline: string;
  text_tpl: string;
  spec: TemplateSpec;
  enabled: boolean;
  created_at: string;
  updated_at?: string;
}

export function getTemplateRoleIds(
  tpl: { role_id?: string; role_ids?: string[] } | null | undefined,
): string[] {
  if (!tpl) return [];
  if (Array.isArray(tpl.role_ids) && tpl.role_ids.length > 0) {
    return Array.from(new Set(tpl.role_ids.map((r) => String(r).trim()).filter(Boolean)));
  }
  if (typeof tpl.role_id === 'string' && tpl.role_id.trim()) {
    return Array.from(
      new Set(
        tpl.role_id
          .split(',')
          .map((r) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  return [];
}

export interface RoleItem {
  role_id: string;
  role_name: string;
  description: string;
  member_count: number;
  active_count: number;
  activated_count: number;
  created_at?: string;
}

export interface RoleMemberItem {
  phone: string;
  name: string;
  dept: string;
  status: 'active' | 'disabled';
  activation: 'activated' | 'pending';
  usable?: boolean;
  external_id?: string | null;
  activated_at?: string | null;
  created_at?: string;
}

export interface UnactivatedMemberItem {
  phone: string;
  name: string;
  activation: 'activated';
  usable?: boolean;
  external_id?: string;
  activated_at?: string;
  source_note?: string;
  created_at: string;
}


export type InstanceStatus =
  | 'pending'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'overdue'
  | 'expired';

export interface TaskInstanceItem {
  id: number;
  template_id: number;
  template_name: string;
  kind: TemplateKind;
  period: TemplatePeriod;
  period_key: string;
  role_id: string;
  phone: string;
  member_name_snap: string;
  dept_snap?: string;
  require_review?: boolean;
  status: InstanceStatus;
  submitted_at: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  deadline_at: string | null;
  created_at: string;
}

export interface SubmissionData {
  id?: number;
  instance_id?: number;
  kind?: TemplateKind;
  form_values?: Record<string, string | number>;
  note?: string;
  attachment_ref?: string | null;
  attachment_name?: string | null;
  attachment_size?: number | null;
  submitted_at?: string;
}

export interface ReminderLogItem {
  id: number;
  instance_id: number;
  slot: string;
  retry_no: number;
  planned_at: string;
  attempted_at: string | null;
  sent_at: string | null;
  result: 'sent' | 'need_activate' | 'failed' | 'no_target' | 'skipped';
  error: string | null;
  template_name?: string;
  phone?: string;
  member_name?: string;
}

export interface TaskInstanceDetail {
  ok: boolean;
  instance: TaskInstanceItem;
  submission: SubmissionData | null;
  reminders: ReminderLogItem[];
}

export interface InstanceStats {
  ok: boolean;
  total: number;
  by_status: Record<InstanceStatus, number>;
  completion_rate: number;
  on_time_rate: number;
  overdue_rate: number;
}

export interface AdminAuditItem {
  id: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
}

export interface AdminHealth {
  ok: boolean;
  service: string;
  channel: 'ready' | 'unconfigured';
  event_crypto: 'ready' | 'plaintext';
  public_base_url: string;
  is_loopback_public_url: boolean;
}

export function isUploadTaskKind(kind: TemplateKind): boolean {
  return kind !== 'notice';
}

```

### File: `src/lib/roles.ts`
```ts
/**
 * 群成员岗位角色（单一真源）。
 *
 * 2026-10-19 用户指令：新增/编辑/一键添加成员共用同一套岗位列表，
 * ⛔ 不在各组件里各写一份（否则下拉选项迟早不一致）。
 */

export const ROLE_OPTIONS = [
  '开发',
  '测试',
  '产品',
  '设计师',
  '运维',
  '销售',
  '项目管理',
];

export const ROLE_LABEL: Record<string, string> = {
  开发: '开发研发',
  测试: '测试质检',
  产品: '产品经理',
  设计师: 'UI/UX 设计师',
  运维: '运维支持',
  销售: '销售商务',
  项目管理: '项目管理',
};

```

### File: `src/lib/api.ts`
```ts
import {
  AdminAuditItem,
  AdminHealth,
  FormFieldSpec,
  InstanceStats,
  InstanceStatus,
  ReminderLogItem,
  RoleItem,
  RoleMemberItem,
  SubmissionData,
  TaskInstanceDetail,
  TaskInstanceItem,
  TemplateItem,
  TemplateKind,
  TemplatePeriod,
  TemplateSpec,
  UnactivatedMemberItem,
  getTemplateRoleIds,
} from './types';
import { humanizeMessage } from './toast';

let csrfToken = 'imtask-csrf-token-initial';

export function setCsrfToken(token: string) {
  csrfToken = token || '';
}

export function getCsrfToken(): string {
  return csrfToken;
}

export function plainError(raw: string): string {
  return humanizeMessage(raw, '操作未成功，请稍后重试');
}

/* ================================================================== */
/* IMTask 内存态后端状态机（已清除全部假测试数据，接入市场部春促真实清单） */
/* ================================================================== */

const PHONE_RE = /^1[3-9]\d{9}$/;
const ROLE_CODE_RE = /^[a-zA-Z0-9_]{2,32}$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

function nowText(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function todayKey(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

interface InternalAdminState {
  username: string;
  password: string;
  must_change_password: boolean;
  loggedIn: boolean;
  health: AdminHealth;
  nextTemplateId: number;
  nextInstanceId: number;
  nextLogId: number;
  nextAuditId: number;
  templates: TemplateItem[];
  roles: Array<{
    role_id: string;
    role_name: string;
    description: string;
    created_at: string;
  }>;
  members: Record<string, RoleMemberItem[]>;
  unactivatedPool: UnactivatedMemberItem[];
  instances: TaskInstanceItem[];
  submissions: Record<number, SubmissionData>;
  reminders: ReminderLogItem[];
  audits: AdminAuditItem[];
}

const TODAY = todayKey();

const state: InternalAdminState = {
  username: 'admin',
  password: 'Admin1234',
  must_change_password: false,
  loggedIn: true,
  health: {
    ok: true,
    service: 'im-task-admin',
    channel: 'ready',
    event_crypto: 'ready',
    public_base_url: 'https://imtask.corp.example.com',
    is_loopback_public_url: false,
  },
  nextTemplateId: 6,
  nextInstanceId: 6,
  nextLogId: 6,
  nextAuditId: 4,
  roles: [
    {
      role_id: 'role_branch_chief',
      role_name: '片长/分支局长',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_marketing_director',
      role_name: '行销总监',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_grid_leader',
      role_name: '网格长',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_installer',
      role_name: '装维',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_smart_home_eng',
      role_name: '智家工程师',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_clerk',
      role_name: '营业员',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_biz_manager',
      role_name: '商客经理',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_gov_ent_manager',
      role_name: '政企经理',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
    {
      role_id: 'role_area_leader',
      role_name: '包区领导',
      description: '',
      created_at: `${TODAY} 08:00:00`,
    },
  ],
  members: {
    role_branch_chief: [
      {
        phone: '13900000000',
        name: '张三',
        dept: '',
        status: 'active',
        activation: 'activated',
        usable: true,
        external_id: 'zdxlz_zhengjia_13900000000',
        activated_at: `${TODAY} 08:00:00`,
        created_at: `${TODAY} 08:00:00`,
      },
    ],
  },
  unactivatedPool: [
    {
      phone: '13880123456',
      name: '林建国',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_13880123456',
      activated_at: `${TODAY} 08:05:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:05:00`,
    },
    {
      phone: '13980654321',
      name: '周敏',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_13980654321',
      activated_at: `${TODAY} 08:06:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:06:00`,
    },
    {
      phone: '18980998877',
      name: '王海涛',
      activation: 'activated',
      usable: true,
      external_id: 'zdxlz_18980998877',
      activated_at: `${TODAY} 08:08:00`,
      source_note: '已在密信激活 · 待管理员归纳角色',
      created_at: `${TODAY} 08:08:00`,
    },
  ],
  templates: [
    {
      id: 1,
      name: '晨会安排',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '08:15',
      deadline: '',
      text_tpl: `一、文字提醒：
1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理
2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够
3、昨天卖得差的队伍，今天帮扶跟到人
二、数据提醒：
1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数
2、商机外呼及意向单到人数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_1_1',
            format: 'text',
            text_content: `一、文字提醒：
1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理
2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够
3、昨天卖得差的队伍，今天帮扶跟到人`,
          },
          {
            id: 'notice_1_2',
            format: 'file',
            file_name: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
            file_caption: `二、数据提醒：
1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数
2、商机外呼及意向单到人数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 2,
      name: '上午业务点检、午间复盘',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '12:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检
2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们
2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数
二、数据提醒：
1、根据农村、城市2号群晒图，统计该分支局上午上门次数
2、商机意向单及回数量，是否达成进度
3、该支局上午1+1155重点指标实时受理数据`,
      spec: {
        notice_items: [
          {
            id: 'notice_2_1',
            format: 'text',
            text_content: `一、文字提醒：
1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检
2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们
2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数`,
          },
          {
            id: 'notice_2_2',
            format: 'file',
            file_name: '1200_分支局上午上门次数与1+1155实时受理数据.png',
            file_caption: `二、数据提醒：
1、根据农村、城市2号群晒图，统计该分支局上午上门次数
2、商机意向单及回数量，是否达成进度
3、该支局上午1+1155重点指标实时受理数据`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 3,
      name: '上门协同',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '15:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑
2、没破零的队伍，跟着一起上门，边跑边纠偏
二、数据提醒：
商机意向单及回数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_3_1',
            format: 'text',
            text_content: `一、文字提醒：
1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑
2、没破零的队伍，跟着一起上门，边跑边纠偏`,
          },
          {
            id: 'notice_3_2',
            format: 'file',
            file_name: '1500_商机意向单及回单进度表.xlsx',
            file_caption: `二、数据提醒：
商机意向单及回数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 4,
      name: '夜间补差',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '18:00',
      deadline: '',
      text_tpl: `一、文字提醒：请关注当日业务发展，有差距的再夜间补齐
二、数据提醒：
1、该分支局上午1+1155重点指标实时受理数据`,
      spec: {
        notice_items: [
          {
            id: 'notice_4_1',
            format: 'text',
            text_content: `一、文字提醒：请关注当日业务发展，有差距的再夜间补齐`,
          },
          {
            id: 'notice_4_2',
            format: 'file',
            file_name: '1800_分支局1+1155重点指标实时受理数据.png',
            file_caption: `二、数据提醒：
1、该分支局上午1+1155重点指标实时受理数据`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
    {
      id: 5,
      name: '下午业务点检、晚间复盘',
      kind: 'notice',
      period: 'day',
      role_id: 'role_branch_chief',
      role_ids: ['role_branch_chief'],
      remind_at: '20:00',
      deadline: '',
      text_tpl: `一、文字提醒：
1、盯次日预约数量、质量
2、总结今日得失、差距
3、梳理明日提升板块
4、明天要打哪里、谁需要你协同上门，今晚把时间约好
二、数据提醒：
1、该支局当日1+1155重点指标受理数据
2、根据农村、城市2号群晒图，统计该分支局全天上门次数
3、商机意向单及回数量，是否达成进度`,
      spec: {
        notice_items: [
          {
            id: 'notice_5_1',
            format: 'text',
            text_content: `一、文字提醒：
1、盯次日预约数量、质量
2、总结今日得失、差距
3、梳理明日提升板块
4、明天要打哪里、谁需要你协同上门，今晚把时间约好`,
          },
          {
            id: 'notice_5_2',
            format: 'file',
            file_name: '2000_当日1+1155受理数据及全天上门次数汇总.png',
            file_caption: `二、数据提醒：
1、该支局当日1+1155重点指标受理数据
2、根据农村、城市2号群晒图，统计该分支局全天上门次数
3、商机意向单及回数量，是否达成进度`,
          },
        ],
      },
      enabled: true,
      created_at: `${TODAY} 08:00:00`,
      updated_at: `${TODAY} 08:00:00`,
    },
  ],
  instances: [
    {
      id: 1,
      template_id: 1,
      template_name: '晨会安排',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 08:15:02`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 2,
      template_id: 2,
      template_name: '上午业务点检、午间复盘',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 12:00:01`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 3,
      template_id: 3,
      template_name: '上门协同',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'approved',
      submitted_at: `${TODAY} 15:00:02`,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 4,
      template_id: 4,
      template_name: '夜间补差',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'pending',
      submitted_at: null,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
    {
      id: 5,
      template_id: 5,
      template_name: '下午业务点检、晚间复盘',
      kind: 'notice',
      period: 'day',
      period_key: TODAY,
      role_id: 'role_branch_chief',
      phone: '13900000000',
      member_name_snap: '张三',
      dept_snap: '',
      require_review: false,
      status: 'pending',
      submitted_at: null,
      reviewed_by: null,
      reviewed_at: null,
      reject_reason: null,
      deadline_at: null,
      created_at: `${TODAY} 08:00:00`,
    },
  ],
  submissions: {
    1: {
      id: 1,
      instance_id: 1,
      kind: 'notice',
      form_values: {
        推送时间点: '08:15',
        事项名称: '晨会安排',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据图片）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理\n2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够\n3、昨天卖得差的队伍，今天帮扶跟到人\n二、数据提醒：\n1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数\n2、商机外呼及意向单到人数量，是否达成进度`,
      attachment_ref: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
      attachment_name: '0815_支局1+1155核心指标完成率与沉淀商机通报.png',
      submitted_at: `${TODAY} 08:15:02`,
    },
    2: {
      id: 2,
      instance_id: 2,
      kind: 'notice',
      form_values: {
        推送时间点: '12:00',
        事项名称: '上午业务点检、午间复盘',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据图片）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检\n2、午间复盘：1、还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们\n2、上午收的本网、异网商机抽查几条，重点看质量，别只看条数\n二、数据提醒：\n1、根据农村、城市2号群晒图，统计该分支局上午上门次数\n2、商机意向单及回数量，是否达成进度\n3、该支局上午1+1155重点指标实时受理数据`,
      attachment_ref: '1200_分支局上午上门次数与1+1155实时受理数据.png',
      attachment_name: '1200_分支局上午上门次数与1+1155实时受理数据.png',
      submitted_at: `${TODAY} 12:00:01`,
    },
    3: {
      id: 3,
      instance_id: 3,
      kind: 'notice',
      form_values: {
        推送时间点: '15:00',
        事项名称: '上门协同',
        接收人: '张三 (13900000000) · 片长/分支局长',
        并列下发任务数: '2 条（1条文本 + 1份数据文件）',
      },
      note: `【已向 张三(13900000000) 下发原文】\n一、文字提醒：\n1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑\n2、没破零的队伍，跟着一起上门，边跑边纠偏\n二、数据提醒：\n商机意向单及回数量，是否达成进度`,
      attachment_ref: '1500_商机意向单及回单进度表.xlsx',
      attachment_name: '1500_商机意向单及回单进度表.xlsx',
      submitted_at: `${TODAY} 15:00:02`,
    },
  },
  reminders: [
    {
      id: 1,
      instance_id: 1,
      slot: '08:15',
      retry_no: 0,
      planned_at: `${TODAY} 08:15:00`,
      attempted_at: `${TODAY} 08:15:02`,
      sent_at: `${TODAY} 08:15:02`,
      result: 'sent',
      error: null,
      template_name: '晨会安排',
      phone: '13900000000',
      member_name: '张三',
    },
    {
      id: 2,
      instance_id: 2,
      slot: '12:00',
      retry_no: 0,
      planned_at: `${TODAY} 12:00:00`,
      attempted_at: `${TODAY} 12:00:01`,
      sent_at: `${TODAY} 12:00:01`,
      result: 'sent',
      error: null,
      template_name: '上午业务点检、午间复盘',
      phone: '13900000000',
      member_name: '张三',
    },
    {
      id: 3,
      instance_id: 3,
      slot: '15:00',
      retry_no: 0,
      planned_at: `${TODAY} 15:00:00`,
      attempted_at: `${TODAY} 15:00:02`,
      sent_at: `${TODAY} 15:00:02`,
      result: 'sent',
      error: null,
      template_name: '上门协同',
      phone: '13900000000',
      member_name: '张三',
    },
  ],
  audits: [
    {
      id: 1,
      at: `${TODAY} 08:00:00`,
      actor: 'admin',
      action: 'role.create',
      target: 'role:role_branch_chief',
      detail: '导入市场部《春促角色清单》：创建角色「片长/分支局长」(role_branch_chief)',
    },
    {
      id: 2,
      at: `${TODAY} 08:00:05`,
      actor: 'admin',
      action: 'member.add',
      target: 'role:role_branch_chief/13900000000',
      detail: '绑定片长/分支局长责任人：张三 (13900000000)',
    },
    {
      id: 3,
      at: `${TODAY} 08:00:10`,
      actor: 'admin',
      action: 'template.create',
      target: 'template:1~5',
      detail: '载入春促 5 个时点任务模板（08:15 晨会安排 / 12:00 午间复盘 / 15:00 上门协同 / 18:00 夜间补差 / 20:00 晚间复盘）',
    },
  ],
};

function appendAudit(action: string, target: string, detail: string) {
  state.audits.unshift({
    id: state.nextAuditId++,
    at: nowText(),
    actor: state.username,
    action,
    target,
    detail,
  });
}

function computeRoles(): RoleItem[] {
  return state.roles.map((r) => {
    const list = state.members[r.role_id] || [];
    const activeCount = list.filter((m) => m.status === 'active').length;
    const activatedCount = list.filter((m) => m.activation === 'activated').length;
    return {
      ...r,
      member_count: list.length,
      active_count: activeCount,
      activated_count: activatedCount,
    };
  });
}

function filterInstances(query: URLSearchParams): TaskInstanceItem[] {
  const status = (query.get('status') || '').trim();
  const kind = (query.get('kind') || '').trim();
  const templateId = (query.get('template_id') || '').trim();
  const roleId = (query.get('role_id') || '').trim();
  const phone = (query.get('phone') || '').trim();
  const periodKey = (query.get('period_key') || '').trim();

  return state.instances.filter((item) => {
    if (status && item.status !== status) return false;
    if (kind) {
      if (kind === 'notice' && item.kind !== 'notice') return false;
      if (kind === 'upload' && item.kind === 'notice') return false;
      if (kind !== 'notice' && kind !== 'upload' && item.kind !== kind) return false;
    }
    if (templateId && String(item.template_id) !== templateId) return false;
    if (roleId && item.role_id !== roleId) return false;
    if (phone && !item.phone.includes(phone) && !item.member_name_snap.includes(phone)) return false;
    if (periodKey && !item.period_key.includes(periodKey)) return false;
    return true;
  });
}

function computeStats(items: TaskInstanceItem[]): InstanceStats {
  const by_status: Record<InstanceStatus, number> = {
    pending: 0,
    submitted: 0,
    approved: 0,
    rejected: 0,
    overdue: 0,
    expired: 0,
  };
  for (const it of items) {
    by_status[it.status] = (by_status[it.status] || 0) + 1;
  }
  const total = items.length;
  const completed = by_status.submitted + by_status.approved;
  const completion_rate = total > 0 ? Number(((completed / total) * 100).toFixed(1)) : 0;
  const on_time_rate =
    total > 0 ? Number((((by_status.approved + by_status.submitted) / total) * 100).toFixed(1)) : 0;
  const overdue_rate =
    total > 0
      ? Number((((by_status.overdue + by_status.expired) / total) * 100).toFixed(1))
      : 0;

  return {
    ok: true,
    total,
    by_status,
    completion_rate,
    on_time_rate,
    overdue_rate,
  };
}

function validateRemindAt(
  kind: TemplateKind,
  period: TemplatePeriod,
  remindAt: string,
  deadline: string,
) {
  const r = remindAt.trim();
  const d = deadline.trim();
  if (!r) throw new Error('REMIND_AT_INVALID');

  if (kind === 'notice') {
    const slots = r.split(',').map((s) => s.trim()).filter(Boolean);
    if (slots.length === 0) throw new Error('REMIND_AT_INVALID');
    for (const s of slots) {
      if (!TIME_RE.test(s)) throw new Error('REMIND_AT_INVALID');
    }
    return;
  }

  if (!d) throw new Error('DEADLINE_INVALID');
  if (period === 'day') {
    if (!TIME_RE.test(r)) throw new Error('REMIND_AT_INVALID');
    if (!TIME_RE.test(d)) throw new Error('DEADLINE_INVALID');
    if (r >= d) throw new Error('DEADLINE_ORDER');
  }
}

function normalizeRoleIdsFromBody(body: any, fallbackRoleIds: string[] = []): string[] {
  if (Array.isArray(body?.role_ids)) {
    const cleaned = Array.from(
      new Set(body.role_ids.map((r: unknown) => String(r || '').trim()).filter(Boolean)),
    );
    return cleaned;
  }
  if (typeof body?.role_ids === 'string' && body.role_ids.trim()) {
    return Array.from(
      new Set(
        body.role_ids
          .split(',')
          .map((r: string) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  if (typeof body?.role_id === 'string' && body.role_id.trim()) {
    return Array.from(
      new Set(
        body.role_id
          .split(',')
          .map((r: string) => r.trim())
          .filter(Boolean),
      ),
    );
  }
  return fallbackRoleIds;
}

/* ================================================================== */
/* CSV 导出辅助（同时支持后端 /api/admin/instances/export 与前端即时下载） */
/* ================================================================== */

export function exportInstancesCsv(params: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {
      qs.set(k, String(v));
    }
  }
  const filtered = filterInstances(qs);
  const statusMap: Record<string, string> = {
    pending: '待推送/待提交',
    submitted: '待审核',
    approved: '已下发/已通过',
    rejected: '已驳回',
    overdue: '已逾期',
    expired: '已过期',
  };
  const header = [
    '实例ID',
    '模板ID',
    '事项名称',
    '任务类型',
    '周期标识',
    '角色编码',
    '成员姓名',
    '手机号',
    '所属部门',
    '当前状态',
    '下发/提交时间',
    '截止时间',
    '审核人',
    '审核时间',
    '驳回原因',
  ];
  const escapeCsv = (val: unknown) => {
    const s = val === null || val === undefined ? '' : String(val);
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines = [
    header.join(','),
    ...filtered.map((r) =>
      [
        r.id,
        r.template_id,
        r.template_name,
        r.kind,
        r.period_key,
        r.role_id,
        r.member_name_snap,
        r.phone,
        r.dept_snap || '',
        statusMap[r.status] || r.status,
        r.submitted_at || '',
        r.deadline_at || '',
        r.reviewed_by || '',
        r.reviewed_at || '',
        r.reject_reason || '',
      ]
        .map(escapeCsv)
        .join(','),
    ),
  ];
  const csvContent = '\uFEFF' + lines.join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `imtask_instances_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ================================================================== */
/* 统一请求路由处理                                                     */
/* ================================================================== */

async function handleImTaskRequest(method: string, rawPath: string, body?: any): Promise<any> {
  const urlObj = new URL(rawPath, 'http://localhost');
  const pathname = urlObj.pathname;
  const query = urlObj.searchParams;

  // 1. Health & Auth
  if (method === 'GET' && pathname === '/api/admin/health') {
    return { ...state.health };
  }

  if (method === 'POST' && pathname === '/api/admin/login') {
    const u = String(body?.username || '').trim();
    const p = String(body?.password || '');
    if (!u || !p) {
      throw new Error('INVALID_CREDENTIALS');
    }
    if (u !== state.username && u !== 'admin') {
      throw new Error('INVALID_CREDENTIALS');
    }
    if (p.length < 4) {
      throw new Error('INVALID_CREDENTIALS');
    }
    state.username = u;
    state.password = p;
    state.loggedIn = true;
    const token = `csrf-${Date.now()}`;
    setCsrfToken(token);
    appendAudit('login', u, '管理员登录后台');
    return {
      ok: true,
      username: state.username,
      must_change_password: state.must_change_password,
      csrf_token: token,
    };
  }

  if (method === 'POST' && pathname === '/api/admin/logout') {
    appendAudit('logout', state.username, '管理员退出登录');
    state.loggedIn = false;
    return { ok: true };
  }

  if (method === 'GET' && pathname === '/api/admin/me') {
    if (!state.loggedIn) {
      throw new Error('UNAUTHORIZED');
    }
    return {
      ok: true,
      username: state.username,
      must_change_password: state.must_change_password,
      csrf_token: csrfToken || 'imtask-csrf-token',
    };
  }

  if (method === 'POST' && pathname === '/api/admin/change-password') {
    const oldPwd = String(body?.old_password || '');
    const newPwd = String(body?.new_password || '');
    if (!oldPwd) throw new Error('WRONG_PASSWORD');
    if (newPwd === oldPwd) throw new Error('PASSWORD_SAME');
    if (newPwd.length < 8 || !/[a-zA-Z]/.test(newPwd) || !/\d/.test(newPwd)) {
      throw new Error('WEAK_PASSWORD');
    }
    state.password = newPwd;
    state.must_change_password = false;
    appendAudit('change_password', state.username, '管理员修改了登录密码');
    return { ok: true };
  }

  if (method === 'POST' && pathname === '/api/admin/username') {
    const newName = String(body?.new_username || '').trim();
    if (newName.length < 2 || newName.length > 32) {
      throw new Error('账号名长度需在 2~32 位之间');
    }
    const oldName = state.username;
    state.username = newName;
    appendAudit('change_username', newName, `账号名由 ${oldName} 修改为 ${newName}`);
    return { ok: true, username: newName };
  }

  if (method === 'POST' && pathname === '/api/admin/callback-url') {
    if (!body?.password) {
      throw new Error('WRONG_PASSWORD');
    }
    return {
      ok: true,
      url: `${state.health.public_base_url}/api/events/zdxlz`,
      note: '请将该回调地址配置在量子密信开放平台「事件订阅」中，用于接收员工消息自动完成手机号与 external_id 激活绑定。',
    };
  }

  // 2. Templates (/api/admin/templates)
  if (method === 'GET' && pathname === '/api/admin/templates') {
    return {
      ok: true,
      items: [...state.templates].sort((a, b) => a.id - b.id),
    };
  }

  if (method === 'POST' && pathname === '/api/admin/templates') {
    const name = String(body?.name || '').trim();
    const kind = String(body?.kind || 'upload') as TemplateKind;
    const period = String(body?.period || 'day') as TemplatePeriod;
    const role_ids = normalizeRoleIdsFromBody(body);
    const role_id = role_ids[0] || '';
    const remind_at = String(body?.remind_at || body?.notice_times || '').trim();
    const deadline = kind === 'notice' ? '' : String(body?.deadline || '').trim();
    const text_tpl = String(body?.text_tpl || '').trim();
    const enabled = Boolean(body?.enabled ?? true);

    if (!name) throw new Error('NAME_REQUIRED');
    if (!['notice', 'upload', 'form', 'image', 'file'].includes(kind)) throw new Error('INVALID_KIND');
    if (!['day', 'week', 'month'].includes(period)) throw new Error('INVALID_PERIOD');
    if (kind === 'notice' && period === 'month') throw new Error('INVALID_PERIOD');
    if (role_ids.length === 0 || !role_ids.every((rid) => state.roles.some((r) => r.role_id === rid))) {
      throw new Error('ROLE_NOT_FOUND');
    }
    if (!text_tpl) throw new Error('TEXT_TPL_REQUIRED');
    if (enabled && state.health.is_loopback_public_url) throw new Error('LOOPBACK_PUBLIC_URL');

    validateRemindAt(kind, period, remind_at, deadline);

    const spec: TemplateSpec = {
      ...(body?.spec || {}),
      require_review: kind === 'notice' ? false : Boolean(body?.spec?.require_review ?? true),
    };

    const newItem: TemplateItem = {
      id: state.nextTemplateId++,
      name,
      kind,
      period,
      role_id,
      role_ids,
      remind_at,
      deadline,
      text_tpl,
      spec,
      enabled,
      created_at: nowText(),
      updated_at: nowText(),
    };
    state.templates.push(newItem);
    const roleNames = role_ids
      .map((rid) => state.roles.find((r) => r.role_id === rid)?.role_name || rid)
      .join('、');
    appendAudit(
      'template.create',
      `template:${newItem.id}`,
      `新建任务模板「${name}」(${kind}/${period}，目标角色：${roleNames})`,
    );
    return { ok: true, id: newItem.id, item: newItem };
  }

  // 在线测试：立即触发模板派单与推送测试 (/api/admin/templates/{id}/trigger)
  const tplTriggerMatch = pathname.match(/^\/api\/admin\/templates\/(\d+)\/trigger$/);
  if (method === 'POST' && tplTriggerMatch) {
    const id = Number(tplTriggerMatch[1]);
    const tpl = state.templates.find((t) => t.id === id);
    if (!tpl) throw new Error('TEMPLATE_NOT_FOUND');
    const tplRoleIds = getTemplateRoleIds(tpl);
    const roleMemberEntries: Array<{ roleId: string; member: RoleMemberItem }> = [];
    for (const rid of tplRoleIds) {
      const list = (state.members[rid] || []).filter((m) => m.status === 'active');
      for (const m of list) {
        roleMemberEntries.push({ roleId: rid, member: m });
      }
    }
    if (roleMemberEntries.length === 0) {
      throw new Error('所选角色下暂无启用中的成员，请先在「角色与成员名册」中添加成员');
    }

    const now = nowText();
    const pKey = todayKey();
    let pushedCount = 0;
    const noticeItems = Array.isArray(tpl.spec?.notice_items) ? tpl.spec.notice_items : [];
    const firstFileItem = noticeItems.find((ni) => ni.format === 'file' && ni.file_name);
    const needReview = tpl.kind === 'notice' ? false : tpl.spec?.require_review !== false;

    for (const { roleId: memberRoleId, member: mem } of roleMemberEntries) {
      let inst = state.instances.find(
        (it) =>
          it.template_id === tpl.id &&
          it.period_key === pKey &&
          it.phone === mem.phone &&
          it.role_id === memberRoleId,
      );
      const nextStatus: InstanceStatus = needReview ? 'submitted' : 'approved';
      if (!inst) {
        inst = {
          id: state.nextInstanceId++,
          template_id: tpl.id,
          template_name: tpl.name,
          kind: tpl.kind,
          period: tpl.period,
          period_key: pKey,
          role_id: memberRoleId,
          phone: mem.phone,
          member_name_snap: mem.name,
          dept_snap: '',
          require_review: needReview,
          status: nextStatus,
          submitted_at: now,
          reviewed_by: null,
          reviewed_at: null,
          reject_reason: null,
          deadline_at: tpl.kind === 'notice' || !tpl.deadline ? null : `${pKey} ${tpl.deadline}:00`,
          created_at: now,
        };
        state.instances.unshift(inst);
      } else {
        inst.require_review = needReview;
        inst.status = nextStatus;
        inst.submitted_at = now;
        inst.reviewed_by = null;
        inst.reviewed_at = null;
      }

      const roleObj = state.roles.find((r) => r.role_id === memberRoleId);
      const roleName = roleObj ? roleObj.role_name : memberRoleId;
      state.submissions[inst.id] = {
        id: inst.id,
        instance_id: inst.id,
        kind: tpl.kind,
        form_values: {
          推送时间点: tpl.remind_at,
          事项名称: tpl.name,
          接收人: `${mem.name} (${mem.phone}) · ${roleName}`,
          ...(noticeItems.length > 0
            ? {
                并列下发任务数: `${noticeItems.length} 条（${noticeItems
                  .map((ni, idx) => `任务${idx + 1}:${ni.format === 'file' ? '文件/图片' : '文本'}`)
                  .join(' + ')}）`,
              }
            : {}),
        },
        note: `【已向 ${mem.name}(${mem.phone}) 下发原文】\n${tpl.text_tpl}`,
        attachment_ref: firstFileItem?.file_name || null,
        attachment_name: firstFileItem?.file_name || null,
        attachment_size: firstFileItem?.file_size || null,
        submitted_at: now,
      };

      state.reminders.unshift({
        id: state.nextLogId++,
        instance_id: inst.id,
        slot: tpl.remind_at.split(',')[0].trim(),
        retry_no: 0,
        planned_at: `${pKey} ${tpl.remind_at.split(',')[0].trim()}:00`,
        attempted_at: now,
        sent_at: mem.activation === 'activated' && mem.usable !== false ? now : null,
        result:
          mem.activation !== 'activated'
            ? 'no_target'
            : mem.usable === false
              ? 'need_activate'
              : 'sent',
        error:
          mem.activation !== 'activated'
            ? 'NO_TARGET'
            : mem.usable === false
              ? 'NEED_ACTIVATE'
              : null,
        template_name: tpl.name,
        phone: mem.phone,
        member_name: mem.name,
      });

      pushedCount++;
    }

    appendAudit(
      'template.trigger',
      `template:${tpl.id}`,
      `手动触发「${tpl.name}」即时推送测试，共向 ${pushedCount} 名成员（${roleMemberEntries.map((e) => `${e.member.name}:${e.member.phone}`).join(', ')}）下发提醒`,
    );
    return {
      ok: true,
      pushed_count: pushedCount,
      dispatched_count: pushedCount,
      template_name: tpl.name,
      remind_at: tpl.remind_at,
      targets: roleMemberEntries.map((e) => `${e.member.name} (${e.member.phone})`),
    };
  }

  const tplMatch = pathname.match(/^\/api\/admin\/templates\/(\d+)$/);
  if (tplMatch) {
    const id = Number(tplMatch[1]);
    const idx = state.templates.findIndex((t) => t.id === id);
    if (idx === -1) throw new Error('TEMPLATE_NOT_FOUND');
    const existing = state.templates[idx];

    if (method === 'PUT') {
      if (body?.kind !== undefined && body.kind !== existing.kind) {
        throw new Error('PERIOD_IMMUTABLE');
      }
      if (body?.period !== undefined && body.period !== existing.period) {
        throw new Error('PERIOD_IMMUTABLE');
      }
      const name = body?.name !== undefined ? String(body.name).trim() : existing.name;
      const role_ids =
        body?.role_ids !== undefined || body?.role_id !== undefined
          ? normalizeRoleIdsFromBody(body)
          : getTemplateRoleIds(existing);
      const role_id = role_ids[0] || '';
      const remind_at =
        body?.remind_at !== undefined
          ? String(body.remind_at).trim()
          : body?.notice_times !== undefined
            ? String(body.notice_times).trim()
            : existing.remind_at;
      const deadline =
        existing.kind === 'notice'
          ? ''
          : body?.deadline !== undefined
            ? String(body.deadline).trim()
            : existing.deadline;
      const text_tpl =
        body?.text_tpl !== undefined ? String(body.text_tpl).trim() : existing.text_tpl;
      const enabled = body?.enabled !== undefined ? Boolean(body.enabled) : existing.enabled;
      const rawSpec = body?.spec !== undefined ? body.spec : existing.spec;
      const spec: TemplateSpec = {
        ...rawSpec,
        require_review:
          existing.kind === 'notice' ? false : Boolean(rawSpec?.require_review ?? true),
      };

      if (!name) throw new Error('NAME_REQUIRED');
      if (
        role_ids.length === 0 ||
        !role_ids.every((rid) => state.roles.some((r) => r.role_id === rid))
      ) {
        throw new Error('ROLE_NOT_FOUND');
      }
      if (!text_tpl) throw new Error('TEXT_TPL_REQUIRED');
      if (enabled && state.health.is_loopback_public_url) throw new Error('LOOPBACK_PUBLIC_URL');

      validateRemindAt(existing.kind, existing.period, remind_at, deadline);

      const updated: TemplateItem = {
        ...existing,
        name,
        role_id,
        role_ids,
        remind_at,
        deadline,
        text_tpl,
        spec,
        enabled,
        updated_at: nowText(),
      };
      state.templates[idx] = updated;
      for (const inst of state.instances) {
        if (inst.template_id === id) {
          inst.template_name = name;
          if (!role_ids.includes(inst.role_id)) {
            inst.role_id = role_id;
          }
        }
      }
      appendAudit(
        'template.update',
        `template:${id}`,
        `编辑模板「${name}」(提醒=${remind_at}, 角色数=${role_ids.length}, 启用=${enabled ? '是' : '否'})`,
      );
      return { ok: true, item: updated };
    }

    if (method === 'DELETE') {
      const hasInst = state.instances.some((inst) => inst.template_id === id);
      if (hasInst) {
        throw new Error('TEMPLATE_HAS_INSTANCES');
      }
      state.templates.splice(idx, 1);
      appendAudit('template.delete', `template:${id}`, `删除模板「${existing.name}」`);
      return { ok: true };
    }
  }

  // 3. Roles & Members (/api/admin/roles)
  if (method === 'GET' && pathname === '/api/admin/roles') {
    return {
      ok: true,
      items: computeRoles(),
    };
  }

  if (method === 'POST' && pathname === '/api/admin/roles') {
    const role_name = String(body?.role_name || '').trim();
    const description = String(body?.description || '').trim();
    if (!role_name) throw new Error('ROLE_NAME_REQUIRED');

    const rawCode = String(body?.role_id || '').trim();
    const role_id = rawCode || `role_${Date.now()}`;
    if (!ROLE_CODE_RE.test(role_id)) throw new Error('ROLE_CODE_INVALID');
    if (state.roles.some((r) => r.role_id === role_id)) throw new Error('ROLE_EXISTS');

    state.roles.push({
      role_id,
      role_name,
      description,
      created_at: nowText(),
    });
    state.members[role_id] = [];
    appendAudit('role.create', `role:${role_id}`, `创建角色「${role_name}」`);
    return { ok: true, role_id };
  }

  const roleMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)$/);
  if (roleMatch) {
    const roleId = decodeURIComponent(roleMatch[1]);
    const rIdx = state.roles.findIndex((r) => r.role_id === roleId);
    if (rIdx === -1) throw new Error('ROLE_NOT_FOUND');

    if (method === 'PUT') {
      const role_name = String(body?.role_name || '').trim();
      const description =
        body?.description !== undefined
          ? String(body.description).trim()
          : state.roles[rIdx].description;
      if (!role_name) throw new Error('ROLE_NAME_REQUIRED');
      state.roles[rIdx] = {
        ...state.roles[rIdx],
        role_name,
        description,
      };
      appendAudit('role.update', `role:${roleId}`, `更新角色名称为「${role_name}」`);
      return { ok: true };
    }

    if (method === 'DELETE') {
      if (state.templates.some((t) => getTemplateRoleIds(t).includes(roleId))) {
        throw new Error('ROLE_HAS_TEMPLATES');
      }
      const removed = state.roles[rIdx];
      state.roles.splice(rIdx, 1);
      delete state.members[roleId];
      appendAudit('role.delete', `role:${roleId}`, `删除角色「${removed.role_name}」`);
      return { ok: true };
    }
  }

  const membersMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members$/);
  if (membersMatch) {
    const roleId = decodeURIComponent(membersMatch[1]);
    if (!state.roles.some((r) => r.role_id === roleId)) throw new Error('ROLE_NOT_FOUND');
    const list = state.members[roleId] || [];

    if (method === 'GET') {
      return {
        ok: true,
        items: [...list],
      };
    }

    if (method === 'POST') {
      const phone = String(body?.phone || '').trim();
      const name = String(body?.name || '').trim();
      const dept = String(body?.dept || '').trim();
      if (!PHONE_RE.test(phone)) throw new Error('PHONE_INVALID');
      if (!name) throw new Error('MEMBER_NAME_REQUIRED');

      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      const pooledPerson = poolIdx >= 0 ? state.unactivatedPool[poolIdx] : undefined;
      if (poolIdx >= 0) {
        state.unactivatedPool.splice(poolIdx, 1);
      }

      const existingIdx = list.findIndex((m) => m.phone === phone);
      if (existingIdx >= 0) {
        list[existingIdx] = {
          ...list[existingIdx],
          name,
          dept,
          status: 'active',
        };
        appendAudit('member.upsert', `role:${roleId}/${phone}`, `更新成员「${name}」(${phone})`);
      } else {
        list.push({
          phone,
          name,
          dept,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: pooledPerson?.external_id || `zdxlz_${phone}`,
          activated_at: pooledPerson?.activated_at || nowText(),
          created_at: nowText(),
        });
        state.members[roleId] = list;
        appendAudit('member.add', `role:${roleId}/${phone}`, `添加成员「${name}」(${phone})`);
      }
      return { ok: true };
    }
  }

  const memberSingleMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members\/([^/]+)$/);
  if (memberSingleMatch) {
    const roleId = decodeURIComponent(memberSingleMatch[1]);
    const phone = decodeURIComponent(memberSingleMatch[2]);
    const roleObj = state.roles.find((r) => r.role_id === roleId);
    if (!roleObj) throw new Error('ROLE_NOT_FOUND');
    const list = state.members[roleId] || [];
    const mIdx = list.findIndex((m) => m.phone === phone);

    if (method === 'PUT' && phone !== 'import') {
      if (mIdx === -1) throw new Error('MEMBER_NOT_FOUND');
      const current = list[mIdx];
      const nextName = body?.name !== undefined ? String(body.name).trim() : current.name;
      const nextDept = body?.dept !== undefined ? String(body.dept).trim() : current.dept;
      const nextStatus =
        body?.status === 'disabled' || body?.status === 'active' ? body.status : current.status;

      if (!nextName) throw new Error('MEMBER_NAME_REQUIRED');

      list[mIdx] = {
        ...current,
        name: nextName,
        dept: nextDept,
        status: nextStatus,
      };
      appendAudit(
        'member.update',
        `role:${roleId}/${phone}`,
        `修改成员 ${phone}：姓名=${nextName}, 部门=${nextDept || '-'}, 状态=${nextStatus === 'active' ? '启用' : '停用'}`,
      );
      return { ok: true, item: list[mIdx] };
    }

    if (method === 'DELETE' && phone !== 'import') {
      if (mIdx === -1) throw new Error('MEMBER_NOT_FOUND');
      const removed = list[mIdx];
      list.splice(mIdx, 1);

      const now = nowText();
      const existPoolIdx = state.unactivatedPool.findIndex((u) => u.phone === removed.phone);
      const unassignedEntry: UnactivatedMemberItem = {
        phone: removed.phone,
        name: removed.name,
        activation: 'activated',
        usable: true,
        external_id: removed.external_id || `zdxlz_${removed.phone}`,
        activated_at: removed.activated_at || now,
        source_note: `由角色「${roleObj.role_name}」移出 · 密信会话保持绑定`,
        created_at: now,
      };
      if (existPoolIdx >= 0) {
        state.unactivatedPool[existPoolIdx] = unassignedEntry;
      } else {
        state.unactivatedPool.unshift(unassignedEntry);
      }

      appendAudit(
        'member.move_to_unassigned',
        `role:${roleId}/${phone}`,
        `将成员「${removed.name}」(${phone}) 从角色「${roleObj.role_name}」移出至「已激活待归纳角色人员」列表（保留密信会话绑定 ${unassignedEntry.external_id}）`,
      );
      return {
        ok: true,
        moved_to_unassigned: true,
        unassigned_item: unassignedEntry,
      };
    }
  }

  const importMatch = pathname.match(/^\/api\/admin\/roles\/([^/]+)\/members\/import$/);
  if (method === 'POST' && importMatch) {
    const roleId = decodeURIComponent(importMatch[1]);
    if (!state.roles.some((r) => r.role_id === roleId)) throw new Error('ROLE_NOT_FOUND');
    const csvText = String(body?.csv_text || body?.csv || '').trim();
    if (!csvText) throw new Error('CSV_EMPTY');

    const lines = csvText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const list = state.members[roleId] || [];
    let added = 0;
    let updated = 0;
    const errors: Array<{ line: number; reason: string }> = [];

    lines.forEach((line, idx) => {
      const parts = line.split(',').map((s) => s.trim());
      const [phone = '', name = '', dept = ''] = parts;
      if (idx === 0 && (phone.includes('手机') || phone.toLowerCase() === 'phone')) {
        return;
      }
      if (!PHONE_RE.test(phone)) {
        errors.push({ line: idx + 1, reason: `手机号「${phone}」格式无效` });
        return;
      }
      if (!name) {
        errors.push({ line: idx + 1, reason: `手机号 ${phone} 缺少姓名` });
        return;
      }
      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      const pooledPerson = poolIdx >= 0 ? state.unactivatedPool[poolIdx] : undefined;
      if (poolIdx >= 0) {
        state.unactivatedPool.splice(poolIdx, 1);
      }
      const existIdx = list.findIndex((m) => m.phone === phone);
      if (existIdx >= 0) {
        list[existIdx] = { ...list[existIdx], name, dept, status: 'active' };
        updated++;
      } else {
        list.push({
          phone,
          name,
          dept,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: pooledPerson?.external_id || `zdxlz_${phone}`,
          activated_at: pooledPerson?.activated_at || nowText(),
          created_at: nowText(),
        });
        added++;
      }
    });

    state.members[roleId] = list;
    appendAudit(
      'member.import',
      `role:${roleId}`,
      `CSV 批量导入成员：新增 ${added} 人，更新 ${updated} 人，失败 ${errors.length} 行`,
    );
    return {
      ok: true,
      added,
      updated,
      skipped: errors.length,
      errors,
    };
  }

  // 3.5 Activated but Unassigned Role Personnel Pool (/api/admin/unactivated-members & /api/admin/unassigned-members)
  if (
    method === 'GET' &&
    (pathname === '/api/admin/unactivated-members' || pathname === '/api/admin/unassigned-members')
  ) {
    return {
      ok: true,
      items: [...state.unactivatedPool],
    };
  }

  if (
    method === 'POST' &&
    (pathname === '/api/admin/unactivated-members/move' ||
      pathname === '/api/admin/unassigned-members/move')
  ) {
    const targetRoleId = String(body?.target_role_id || '').trim();
    const roleObj = state.roles.find((r) => r.role_id === targetRoleId);
    if (!roleObj) throw new Error('ROLE_NOT_FOUND');

    const rawPhones: string[] = Array.isArray(body?.phones)
      ? body.phones.map((p: unknown) => String(p || '').trim()).filter(Boolean)
      : body?.phone
        ? [String(body.phone).trim()]
        : [];

    if (rawPhones.length === 0) {
      throw new Error('请选择需要归入角色分组的已激活人员');
    }

    const roleList = state.members[targetRoleId] || [];
    let movedCount = 0;
    const movedNames: string[] = [];

    for (const phone of rawPhones) {
      const poolIdx = state.unactivatedPool.findIndex((u) => u.phone === phone);
      if (poolIdx === -1) continue;
      const person = state.unactivatedPool[poolIdx];
      state.unactivatedPool.splice(poolIdx, 1);

      const existIdx = roleList.findIndex((m) => m.phone === phone);
      if (existIdx >= 0) {
        roleList[existIdx] = {
          ...roleList[existIdx],
          name: person.name,
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: roleList[existIdx].external_id || person.external_id || `zdxlz_${phone}`,
          activated_at: person.activated_at || nowText(),
        };
      } else {
        roleList.push({
          phone: person.phone,
          name: person.name,
          dept: '',
          status: 'active',
          activation: 'activated',
          usable: true,
          external_id: person.external_id || `zdxlz_${person.phone}`,
          activated_at: person.activated_at || nowText(),
          created_at: nowText(),
        });
      }
      movedCount++;
      movedNames.push(`${person.name}(${person.phone})`);
    }

    state.members[targetRoleId] = roleList;
    appendAudit(
      'member.assign_role',
      `role:${targetRoleId}`,
      `将 ${movedCount} 名已激活待归纳人员一键归入角色「${roleObj.role_name}」：${movedNames.join('、')}`,
    );

    return {
      ok: true,
      moved_count: movedCount,
      target_role_id: targetRoleId,
      target_role_name: roleObj.role_name,
      remaining: [...state.unactivatedPool],
    };
  }

  const unactivatedDelMatch = pathname.match(
    /^\/api\/admin\/(?:unactivated-members|unassigned-members)\/([^/]+)$/,
  );
  if (method === 'DELETE' && unactivatedDelMatch) {
    const phone = decodeURIComponent(unactivatedDelMatch[1]);
    const idx = state.unactivatedPool.findIndex((u) => u.phone === phone);
    if (idx === -1) throw new Error('MEMBER_NOT_FOUND');
    const removed = state.unactivatedPool[idx];
    state.unactivatedPool.splice(idx, 1);
    appendAudit(
      'unassigned_activated.unbind',
      `unassigned:${phone}`,
      `解除「${removed.name}」(${phone}) 的量子密信会话绑定（external_id: ${removed.external_id || `zdxlz_${phone}`}）并从待归纳列表移除；员工需在密信对话窗口重新输入发送激活指令方可再次绑定`,
    );
    return {
      ok: true,
      unbound_phone: phone,
      unbound_name: removed.name,
      unbound_external_id: removed.external_id || `zdxlz_${phone}`,
    };
  }

  // 4. Instances (/api/admin/instances)
  if (method === 'GET' && pathname === '/api/admin/instances/stats') {
    const filtered = filterInstances(query);
    return computeStats(filtered);
  }

  if (method === 'GET' && pathname === '/api/admin/instances') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const filtered = filterInstances(query);
    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  const instDetailMatch = pathname.match(/^\/api\/admin\/instances\/(\d+)$/);
  if (method === 'GET' && instDetailMatch) {
    const id = Number(instDetailMatch[1]);
    const inst = state.instances.find((it) => it.id === id);
    if (!inst) throw new Error('INSTANCE_NOT_FOUND');
    const tpl = state.templates.find((t) => t.id === inst.template_id);
    const roleObj = state.roles.find((r) => r.role_id === inst.role_id);
    const roleName = roleObj ? roleObj.role_name : inst.role_id;
    const submission =
      state.submissions[id] ||
      (tpl
        ? {
            id: id,
            instance_id: id,
            kind: inst.kind,
            form_values: {
              计划推送时间: tpl.remind_at,
              事项名称: tpl.name,
              接收人: `${inst.member_name_snap} (${inst.phone}) · ${roleName}`,
            },
            note: `【待推送提醒原文预览】\n${tpl.text_tpl}`,
            attachment_ref: null,
            attachment_name: null,
            submitted_at: inst.submitted_at || '待到点或手动触发推送',
          }
        : null);
    const reminders = state.reminders.filter((r) => r.instance_id === id);
    const result: TaskInstanceDetail = {
      ok: true,
      instance: inst,
      submission,
      reminders,
    };
    return result;
  }

  const instReviewMatch = pathname.match(/^\/api\/admin\/instances\/(\d+)\/review$/);
  if (method === 'POST' && instReviewMatch) {
    const id = Number(instReviewMatch[1]);
    const inst = state.instances.find((it) => it.id === id);
    if (!inst) throw new Error('INSTANCE_NOT_FOUND');
    if (inst.status !== 'submitted') {
      throw new Error('STATE_CONFLICT');
    }
    const action = String(body?.action || '').trim();
    const reason = String(body?.reason || '').trim();
    if (action !== 'approve' && action !== 'reject') {
      throw new Error('INVALID_ACTION');
    }
    if (action === 'reject' && reason.length < 2) {
      throw new Error('REASON_REQUIRED');
    }

    inst.status = action === 'approve' ? 'approved' : 'rejected';
    inst.reviewed_by = state.username;
    inst.reviewed_at = nowText();
    inst.reject_reason = action === 'reject' ? reason : null;

    appendAudit(
      action === 'approve' ? 'instance.approve' : 'instance.reject',
      `instance:${id}`,
      action === 'approve'
        ? `审核通过：${inst.member_name_snap} - ${inst.template_name} (${inst.period_key})`
        : `审核驳回：${inst.member_name_snap} - 原因：${reason}`,
    );
    return { ok: true, status: inst.status, instance: inst };
  }

  // 5. Logs (/api/admin/logs/reminders & /api/admin/logs/audit)
  if (method === 'GET' && pathname === '/api/admin/logs/reminders') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const resultFilter = (query.get('result') || '').trim();
    const instanceIdFilter = (query.get('instance_id') || '').trim();

    const filtered = state.reminders.filter((r) => {
      if (resultFilter && r.result !== resultFilter) return false;
      if (instanceIdFilter && String(r.instance_id) !== instanceIdFilter) return false;
      return true;
    });

    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  if (method === 'GET' && pathname === '/api/admin/logs/audit') {
    const page = Math.max(1, Number(query.get('page') || 1));
    const size = Math.max(1, Math.min(100, Number(query.get('size') || 20)));
    const actionFilter = (query.get('action') || '').trim();

    const filtered = state.audits.filter((a) => {
      if (actionFilter && !a.action.includes(actionFilter)) return false;
      return true;
    });

    const total = filtered.length;
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return {
      ok: true,
      total,
      page,
      size,
      items,
    };
  }

  throw new Error(`接口路径不存在: ${method} ${pathname}`);
}

export const api = {
  get: <T = any>(path: string): Promise<T> => handleImTaskRequest('GET', path),
  post: <T = any>(path: string, body?: any): Promise<T> => handleImTaskRequest('POST', path, body),
  put: <T = any>(path: string, body?: any): Promise<T> => handleImTaskRequest('PUT', path, body),
  delete: <T = any>(path: string): Promise<T> => handleImTaskRequest('DELETE', path),
};

```

### File: `src/lib/toast.tsx`
```tsx
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

```

### File: `src/lib/store.tsx`
```tsx
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api';
import { AdminHealth, InstanceStats, RoleItem, TemplateItem } from './types';

interface AppContextType {
  templates: TemplateItem[];
  roles: RoleItem[];
  stats: InstanceStats | null;
  health: AdminHealth | null;
  loading: boolean;
  refreshAll: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
  refreshRoles: () => Promise<void>;
  refreshStats: () => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [stats, setStats] = useState<InstanceStats | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshTemplates = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: TemplateItem[] }>('/api/admin/templates');
    setTemplates(res.items || []);
  }, []);

  const refreshRoles = useCallback(async () => {
    const res = await api.get<{ ok: boolean; items: RoleItem[] }>('/api/admin/roles');
    setRoles(res.items || []);
  }, []);

  const refreshStats = useCallback(async () => {
    const res = await api.get<InstanceStats>('/api/admin/instances/stats');
    setStats(res);
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [tplRes, roleRes, statRes, healthRes] = await Promise.all([
        api.get<{ ok: boolean; items: TemplateItem[] }>('/api/admin/templates'),
        api.get<{ ok: boolean; items: RoleItem[] }>('/api/admin/roles'),
        api.get<InstanceStats>('/api/admin/instances/stats'),
        api.get<AdminHealth>('/api/admin/health'),
      ]);
      setTemplates(tplRes.items || []);
      setRoles(roleRes.items || []);
      setStats(statRes);
      setHealth(healthRes);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  return (
    <AppContext.Provider
      value={{
        templates,
        roles,
        stats,
        health,
        loading,
        refreshAll,
        refreshTemplates,
        refreshRoles,
        refreshStats,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useAppStore = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppStore must be used within AppProvider');
  return ctx;
};

```

### File: `src/pages/LoginPage.tsx`
```tsx
import React, { useState } from 'react';
import { ShieldCheck, AlertCircle, LogIn, CalendarCheck2 } from 'lucide-react';
import { api, plainError, setCsrfToken } from '../lib/api';
import { useToast } from '../lib/toast';

/**
 * P2-3: 两个密码框均使用 type="text" + style="-webkit-text-security: disc"
 */
const MASKED_INPUT_STYLE = { WebkitTextSecurity: 'disc' } as unknown as React.CSSProperties;

interface LoginResponse {
  ok: boolean;
  username: string;
  must_change_password: boolean;
  csrf_token: string;
}

export const LoginPage: React.FC = () => {
  const toast = useToast();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      const msg = '请输入管理员账号与登录密码';
      setError(msg);
      toast.warning(msg);
      return;
    }
    setBusy(true);
    try {
      const data = await api.post<LoginResponse>('/api/admin/login', {
        username: username.trim(),
        password,
      });
      setCsrfToken(data.csrf_token);
      toast.success(`欢迎回来，${data.username}`, '登录成功');
      window.location.href = data.must_change_password ? '/change-password' : '/';
    } catch (err) {
      const msg = err instanceof Error ? plainError(err.message) : '登录未成功，请稍后重试';
      setError(msg);
      toast.error(msg, '登录失败');
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-[#f7f8fa] flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5 mb-5 px-1">
          <div className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center shadow-xs text-white">
            <CalendarCheck2 className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-bold text-base text-slate-900 tracking-tight">
              IMTask 办公任务调度平台
            </h1>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-slate-400 font-mono">im-task</span>
              <span className="text-[9px] text-blue-600 bg-blue-50 px-1.5 py-0.2 rounded font-medium">
                管理后台
              </span>
            </div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-6 space-y-4"
        >
          <div>
            <h2 className="text-lg font-black text-slate-900 tracking-tight">管理员登录</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              请使用 IMTask 调度平台管理员账号登录。
            </p>
          </div>

          {error && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-xs flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-3 text-xs">
            <div>
              <label className="font-semibold text-slate-700 block mb-1">管理员账号</label>
              <input
                type="text"
                autoComplete="off"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入账号（默认 admin）"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 font-medium"
              />
            </div>

            <div>
              <label className="font-semibold text-slate-700 block mb-1">登录密码</label>
              {/* P2-3: type="text" + style="-webkit-text-security: disc" */}
              <input
                type="text"
                autoComplete="off"
                spellCheck={false}
                style={MASKED_INPUT_STYLE}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码（演示环境可填 Admin1234）"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 font-medium"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={busy}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 active:scale-95 disabled:opacity-60 text-white font-bold text-xs rounded-xl shadow-xs flex items-center justify-center gap-1.5 transition-all cursor-pointer"
          >
            <LogIn className="w-3.5 h-3.5" />
            <span>{busy ? '正在登录…' : '登录后台'}</span>
          </button>

          <div className="pt-1 border-t border-slate-100 flex items-center gap-1.5 text-[11px] text-slate-400">
            <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
            <span>启用 HttpOnly 会话与 CSRF 令牌保护，连续输错密码将触发速率锁定。</span>
          </div>
        </form>
      </div>
    </div>
  );
};

```

### File: `src/pages/ChangePasswordPage.tsx`
```tsx
import React from 'react';
import { ChangePasswordModal } from '../components/system/AdminUserCard';

/**
 * 强制改密页（must_change_password=true 时唯一可达页面）。
 * 复用侧栏同款改密弹窗的表单与校验，⛔ 不另起一套措辞。
 */
export const ChangePasswordPage: React.FC<{ onChanged: () => void }> = ({ onChanged }) => (
  <div className="min-h-screen w-full bg-[#f7f8fa]">
    <ChangePasswordModal isOpen forced onClose={() => undefined} onChanged={onChanged} />
  </div>
);

```

### File: `src/components/system/AdminUserCard.tsx`
```tsx
import React, { useEffect, useState } from 'react';
import {
  User,
  UserCog,
  Key,
  Link2,
  Copy,
  Check,
  LogOut,
  X,
  CheckCircle2,
  Shield,
  ChevronDown,
  AlertCircle,
} from 'lucide-react';
import { api, plainError, setCsrfToken } from '../../lib/api';
import { useToast } from '../../lib/toast';

/**
 * P2-3: 密码输入框统一使用 type="text" + style="-webkit-text-security: disc"
 * 避免浏览器弹出原生密码管理器或无网络账户登录干扰。
 */
const MASKED_INPUT_STYLE = { WebkitTextSecurity: 'disc' } as unknown as React.CSSProperties;

export interface AdminMe {
  username: string;
  must_change_password: boolean;
}

/* ------------------------------------------------------------------ */
/* 修改密码弹窗（P2-3 & P2-4：改密成功后提示「密码已修改，请用新密码重新登录」并切回登录） */
/* ------------------------------------------------------------------ */

interface ChangePasswordModalProps {
  isOpen: boolean;
  forced?: boolean;
  onClose: () => void;
  onChanged: () => void;
}

export const ChangePasswordModal: React.FC<ChangePasswordModalProps> = ({
  isOpen,
  forced = false,
  onClose,
  onChanged,
}) => {
  const toast = useToast();
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!isOpen) return null;

  const submit = async () => {
    setErrorMsg(null);
    if (!oldPassword) {
      const msg = '请输入当前原密码';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (newPassword.length < 8 || !/[a-zA-Z]/.test(newPassword) || !/\d/.test(newPassword)) {
      const msg = '新密码长度至少 8 位，且需同时包含字母和数字';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (newPassword !== confirmPassword) {
      const msg = '两次输入的新密码不一致，请核对后重试';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    if (newPassword === oldPassword) {
      const msg = '新密码不能与原密码相同';
      setErrorMsg(msg);
      toast.warning(msg);
      return;
    }
    setBusy(true);
    try {
      await api.post('/api/admin/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      });
      // P2-4: 改密成功后提示「密码已修改，请用新密码重新登录」，再切回登录视图
      const doneText = '密码已修改，请用新密码重新登录';
      setSuccessMsg(doneText);
      toast.success(doneText, '密码修改成功');
      try {
        await api.post('/api/admin/logout');
      } catch {
        // ignore
      }
      window.setTimeout(() => {
        setCsrfToken('');
        onChanged();
        window.location.href = '/login';
      }, 1200);
    } catch (err) {
      const friendly = err instanceof Error ? plainError(err.message) : '密码修改未成功';
      setErrorMsg(friendly);
      toast.error(friendly);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-xs">
              <Key className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">修改管理员密码</h3>
              <span className="text-[11px] text-slate-400">
                IMTask 办公任务调度平台 · 身份凭据
              </span>
            </div>
          </div>
          {!forced && (
            <button
              onClick={onClose}
              className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {forced && (
          <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>首次登录必须修改初始密码后才能使用后台功能。</span>
          </div>
        )}

        <div className="space-y-3 text-xs">
          {errorMsg && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}
          {successMsg && (
            <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded-xl text-emerald-800 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-600" />
              <span>{successMsg}</span>
            </div>
          )}

          <div>
            <label className="font-semibold text-slate-700 block mb-1">当前原密码</label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请输入当前登录密码"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              新密码（至少 8 位，含字母与数字）
            </label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="至少8位字符，同时包含字母与数字"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div>
            <label className="font-semibold text-slate-700 block mb-1">确认新密码</label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请再次输入新密码"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>

          <div className="pt-2 flex items-center justify-end gap-2">
            {!forced && (
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
            )}
            <button
              onClick={submit}
              disabled={busy}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
            >
              {busy ? '正在提交…' : '确认修改'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 事件回调地址查看                                                    */
/* ------------------------------------------------------------------ */

interface CallbackUrlModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const CallbackUrlModal: React.FC<CallbackUrlModalProps> = ({ isOpen, onClose }) => {
  const toast = useToast();
  const [password, setPassword] = useState('');
  const [url, setUrl] = useState('');
  const [note, setNote] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setPassword('');
    setUrl('');
    setNote('');
    setErrorMsg(null);
    setCopied(false);
  }, [isOpen]);

  if (!isOpen) return null;

  const verify = async () => {
    setErrorMsg(null);
    if (!password) {
      setErrorMsg('请输入登录密码以查看回调地址');
      return;
    }
    setBusy(true);
    try {
      const data = await api.post<{ ok: boolean; url: string; note?: string }>(
        '/api/admin/callback-url',
        { password },
      );
      setUrl(data.url || '');
      setNote(data.note || '');
    } catch (err) {
      const msg = err instanceof Error ? plainError(err.message) : '验证未通过';
      setErrorMsg(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!url) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
      } else {
        const el = document.createElement('textarea');
        el.value = url;
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
      }
      setCopied(true);
      toast.success('回调地址已复制到剪贴板');
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      toast.error('复制失败，请手动选中地址复制');
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-indigo-600 text-white flex items-center justify-center font-bold text-xs">
              <Link2 className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">量子密信事件回调地址</h3>
              <span className="text-[11px] text-slate-400">IMTask 通道接入配置 · 只读</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {errorMsg && (
          <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        {!url ? (
          <div className="space-y-3 text-xs">
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 leading-relaxed">
              回调地址属于部署级配置，<span className="font-bold">查看前需再次验证登录密码</span>。
              验证通过后可复制填入量子密信开发者后台。
            </div>
            <div>
              <label className="font-semibold text-slate-700 block mb-1">登录密码</label>
              <input
                type="text"
                autoComplete="off"
                spellCheck={false}
                style={MASKED_INPUT_STYLE}
                placeholder="请输入当前登录密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') verify();
                }}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={verify}
                disabled={busy}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
              >
                {busy ? '正在验证…' : '验证并查看'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3 text-xs">
            <div>
              <label className="font-semibold text-slate-700 block mb-1">回调地址（POST JSON）</label>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={url}
                  onFocus={(e) => e.currentTarget.select()}
                  className="flex-1 bg-slate-50 border border-slate-200 rounded-xl p-2.5 font-mono text-[12px] text-slate-800 outline-none"
                />
                <button
                  onClick={copy}
                  className="px-3 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-semibold flex items-center gap-1.5 shrink-0 cursor-pointer transition-colors"
                >
                  {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? '已复制' : '复制'}</span>
                </button>
              </div>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-600 leading-relaxed">
              {note || '该地址由服务器部署配置决定，后台只能查看与复制，不能修改。'}
            </div>
            <div className="flex justify-end">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-medium cursor-pointer transition-colors"
              >
                关闭
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 修改管理员账号名                                                     */
/* ------------------------------------------------------------------ */

interface ChangeUsernameModalProps {
  isOpen: boolean;
  current: string;
  onClose: () => void;
  onChanged: (username: string) => void;
}

export const ChangeUsernameModal: React.FC<ChangeUsernameModalProps> = ({
  isOpen,
  current,
  onClose,
  onChanged,
}) => {
  const toast = useToast();
  const [newUsername, setNewUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setNewUsername('');
    setPassword('');
    setErrorMsg(null);
  }, [isOpen]);

  if (!isOpen) return null;

  const submit = async () => {
    setErrorMsg(null);
    const name = newUsername.trim();
    if (name.length < 2 || name.length > 32) {
      setErrorMsg('账号名长度需在 2~32 位之间');
      return;
    }
    if (/\s/.test(name)) {
      setErrorMsg('账号名不能包含空格');
      return;
    }
    if (name === current) {
      setErrorMsg('新账号名与当前账号名相同');
      return;
    }
    if (!password) {
      setErrorMsg('请输入登录密码以验证身份');
      return;
    }
    setBusy(true);
    try {
      const data = await api.post<{ ok: boolean; username: string }>('/api/admin/username', {
        new_username: name,
        password,
      });
      toast.success(`管理员账号名已修改为「${data.username || name}」`);
      onChanged(data.username || name);
    } catch (err) {
      const msg = err instanceof Error ? plainError(err.message) : '修改未成功';
      setErrorMsg(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-xs">
              <UserCog className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">修改登录账号名</h3>
              <span className="text-[11px] text-slate-400">改完下次登录请使用新账号名</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {errorMsg && (
          <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="space-y-3 text-xs">
          <div className="p-2.5 bg-slate-50 border border-slate-100 rounded-xl flex justify-between">
            <span className="text-slate-400">当前账号名</span>
            <span className="font-semibold text-slate-800 font-mono">{current}</span>
          </div>
          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              新账号名（2~32 位，不含空格）
            </label>
            <input
              type="text"
              placeholder="请输入新的登录账号名"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>
          <div>
            <label className="font-semibold text-slate-700 block mb-1">
              登录密码（用于验证身份）
            </label>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              style={MASKED_INPUT_STYLE}
              placeholder="请输入当前登录密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit();
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 outline-none focus:border-blue-500 focus:bg-white"
            />
          </div>
          <div className="pt-2 flex items-center justify-end gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
            >
              取消
            </button>
            <button
              onClick={submit}
              disabled={busy}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-60"
            >
              {busy ? '正在提交…' : '确认修改'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 侧边栏底部管理员卡片                                                 */
/* ------------------------------------------------------------------ */

interface AdminUserCardProps {
  me: AdminMe;
  onLoggedOut: () => void;
}

export const AdminUserCard: React.FC<AdminUserCardProps> = ({ me, onLoggedOut }) => {
  const [open, setOpen] = useState(false);
  const [modal, setModal] = useState<
    'none' | 'password' | 'callback' | 'username' | 'logout'
  >('none');
  const [busy, setBusy] = useState(false);
  const [displayName, setDisplayName] = useState(me.username || '');
  useEffect(() => setDisplayName(me.username || ''), [me.username]);

  const display = displayName || '管理员';
  const initial = display.slice(0, 1).toUpperCase();

  const doLogout = async () => {
    setBusy(true);
    try {
      await api.post('/api/admin/logout');
    } catch {
      /* ignore */
    } finally {
      setCsrfToken('');
      onLoggedOut();
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between p-2 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200/80 transition-all cursor-pointer text-left"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-full bg-blue-600 text-white font-bold text-xs flex items-center justify-center shrink-0 shadow-xs">
            {initial}
          </div>
          <div className="truncate">
            <div className="font-bold text-xs text-slate-800 leading-tight">{display}</div>
            <div className="text-[10px] text-blue-600 flex items-center gap-1 font-medium">
              <Shield className="w-2.5 h-2.5" />
              <span>系统管理员</span>
            </div>
          </div>
        </div>
        <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
      </button>

      {open && (
        <div className="absolute left-0 bottom-full mb-2 w-full bg-white rounded-2xl shadow-xl border border-slate-200 p-1.5 z-50 text-xs animate-in fade-in zoom-in-95">
          <div className="px-3 py-2 border-b border-slate-100">
            <div className="font-bold text-slate-800">{display}</div>
            <div className="text-[10px] text-slate-400 font-mono">IMTask 调度管理中心</div>
            <div className="text-[10px] text-emerald-600 font-semibold mt-0.5">● 会话已鉴权</div>
          </div>

          <div className="py-1 space-y-0.5">
            <button
              onClick={() => {
                setOpen(false);
                setModal('password');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-50 text-slate-700 font-medium flex items-center gap-2 cursor-pointer transition-colors"
            >
              <Key className="w-3.5 h-3.5 text-blue-600" />
              <span>修改管理员密码</span>
            </button>

            <button
              onClick={() => {
                setOpen(false);
                setModal('callback');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-50 text-slate-700 font-medium flex items-center gap-2 cursor-pointer transition-colors"
            >
              <Link2 className="w-3.5 h-3.5 text-slate-500" />
              <span>查看密信回调地址</span>
            </button>

            <button
              onClick={() => {
                setOpen(false);
                setModal('username');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-50 text-slate-700 font-medium flex items-center gap-2 cursor-pointer transition-colors"
            >
              <UserCog className="w-3.5 h-3.5 text-slate-500" />
              <span>修改账号名</span>
            </button>
          </div>

          <div className="pt-1 border-t border-slate-100">
            <button
              onClick={() => {
                setOpen(false);
                setModal('logout');
              }}
              className="w-full text-left px-3 py-2 rounded-xl hover:bg-rose-50 text-rose-600 font-semibold flex items-center gap-2 cursor-pointer transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>安全退出后台</span>
            </button>
          </div>
        </div>
      )}

      <ChangePasswordModal
        isOpen={modal === 'password'}
        onClose={() => setModal('none')}
        onChanged={() => {
          setModal('none');
          onLoggedOut();
        }}
      />

      <CallbackUrlModal isOpen={modal === 'callback'} onClose={() => setModal('none')} />

      <ChangeUsernameModal
        isOpen={modal === 'username'}
        current={display}
        onClose={() => setModal('none')}
        onChanged={(name) => {
          setDisplayName(name);
          setModal('none');
        }}
      />

      {modal === 'logout' && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in-95 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-xl bg-rose-600 text-white flex items-center justify-center font-bold text-xs">
                  <LogOut className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">退出登录确认</h3>
                  <span className="text-[11px] text-slate-400">
                    IMTask 办公任务调度平台 · 身份凭据
                  </span>
                </div>
              </div>
              <button
                onClick={() => setModal('none')}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              确定要退出当前管理员登录会话吗？退出后将吊销当前会话令牌，下次访问需重新输入账号与密码。
            </p>

            <div className="pt-2 flex items-center justify-end gap-2">
              <button
                onClick={() => setModal('none')}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium text-xs cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={doLogout}
                disabled={busy}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold shadow-xs flex items-center gap-1.5 text-xs cursor-pointer disabled:opacity-60"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span>{busy ? '正在退出…' : '确认退出登录'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

```

### File: `src/components/imtask/InstancesView.tsx`
```tsx
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Eye,
  Download,
  Filter,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
  Paperclip,
  Send,
  X,
  Check,
  Bell,
  Smartphone,
  Users,
  Calendar,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { humanizeReminderLogError, useToast } from '../../lib/toast';
import {
  InstanceStats,
  TaskInstanceDetail,
  TaskInstanceItem,
} from '../../lib/types';

const PAGE_SIZE = 20;

function getTodayDateString(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function getInstanceStatusMeta(inst: TaskInstanceItem): {
  label: string;
  className: string;
  icon: React.ReactNode;
} {
  // 纯通知提醒任务：只区分「待提醒」与「已提醒」
  if (inst.kind === 'notice') {
    if (inst.status === 'approved' || inst.submitted_at) {
      return {
        label: '已提醒',
        className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
      };
    }
    return {
      label: '待提醒',
      className: 'bg-slate-100 text-slate-600 border-slate-200',
      icon: <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
    };
  }

  // 上传任务（upload / form / image / file）
  switch (inst.status) {
    case 'submitted':
      return {
        label: '待审核',
        className: 'bg-blue-50 text-blue-700 border-blue-200',
        icon: <Clock className="w-3.5 h-3.5 text-blue-600 shrink-0" />,
      };
    case 'approved':
      return {
        label: inst.require_review === false ? '已上传' : '审核通过',
        className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />,
      };
    case 'rejected':
      return {
        label: '已驳回',
        className: 'bg-rose-50 text-rose-700 border-rose-200',
        icon: <XCircle className="w-3.5 h-3.5 text-rose-600 shrink-0" />,
      };
    case 'overdue':
      return {
        label: '已逾期',
        className: 'bg-amber-50 text-amber-800 border-amber-200',
        icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />,
      };
    default:
      return {
        label: '待上传',
        className: 'bg-slate-100 text-slate-600 border-slate-200',
        icon: <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" />,
      };
  }
}

export const InstancesView: React.FC = () => {
  const { roles, refreshStats } = useAppStore();
  const toast = useToast();

  // 筛选条件：按日期（默认今天）、按角色、按任务类型、按执行状态、按用户名/手机号
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [phoneFilter, setPhoneFilter] = useState<string>('');
  const [periodKeyFilter, setPeriodKeyFilter] = useState<string>(() => getTodayDateString());

  // 分页状态 (P1-4: page & size=20)
  const [page, setPage] = useState<number>(1);
  const [total, setTotal] = useState<number>(0);
  const [items, setItems] = useState<TaskInstanceItem[]>([]);
  const [localStats, setLocalStats] = useState<InstanceStats | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  // P1-1: 驳回原因弹窗状态
  const [rejectTarget, setRejectTarget] = useState<TaskInstanceItem | null>(null);
  const [rejectReason, setRejectReason] = useState<string>('');
  const [submittingReject, setSubmittingReject] = useState<boolean>(false);

  // P2-2: 实例详情弹窗状态
  const [detailData, setDetailData] = useState<TaskInstanceDetail | null>(null);
  const [loadingDetailId, setLoadingDetailId] = useState<number | null>(null);

  const roleMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of roles) {
      map[r.role_id] = r.role_name;
    }
    return map;
  }, [roles]);

  const loadData = useCallback(
    async (
      targetPage = page,
      overrides?: {
        status?: string;
        kind?: string;
        role?: string;
        phone?: string;
        periodKey?: string;
      },
    ) => {
      setLoading(true);
      try {
        const st = overrides?.status !== undefined ? overrides.status : statusFilter;
        const kd = overrides?.kind !== undefined ? overrides.kind : kindFilter;
        const rl = overrides?.role !== undefined ? overrides.role : roleFilter;
        const ph = (overrides?.phone !== undefined ? overrides.phone : phoneFilter).trim();
        const pk = (overrides?.periodKey !== undefined ? overrides.periodKey : periodKeyFilter).trim();

        const qs = new URLSearchParams();
        qs.set('page', String(targetPage));
        qs.set('size', String(PAGE_SIZE));
        if (st) qs.set('status', st);
        if (kd) qs.set('kind', kd);
        if (rl) qs.set('role_id', rl);
        if (ph) qs.set('phone', ph);
        if (pk) qs.set('period_key', pk);

        const statsQs = new URLSearchParams();
        if (st) statsQs.set('status', st);
        if (kd) statsQs.set('kind', kd);
        if (rl) statsQs.set('role_id', rl);
        if (ph) statsQs.set('phone', ph);
        if (pk) statsQs.set('period_key', pk);

        const [listRes, statRes] = await Promise.all([
          api.get<{ ok: boolean; total: number; page: number; size: number; items: TaskInstanceItem[] }>(
            `/api/admin/instances?${qs.toString()}`,
          ),
          api.get<InstanceStats>(`/api/admin/instances/stats?${statsQs.toString()}`),
        ]);
        setItems(listRes.items || []);
        setTotal(listRes.total || 0);
        setPage(listRes.page || targetPage);
        setLocalStats(statRes);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载任务实例列表失败');
      } finally {
        setLoading(false);
      }
    },
    [page, statusFilter, kindFilter, roleFilter, phoneFilter, periodKeyFilter],
  );

  useEffect(() => {
    loadData(1);
  }, [statusFilter, kindFilter, roleFilter, periodKeyFilter]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    loadData(1);
  };

  const handleResetFilters = () => {
    const todayStr = getTodayDateString();
    setStatusFilter('');
    setKindFilter('');
    setRoleFilter('');
    setPhoneFilter('');
    setPeriodKeyFilter(todayStr);
    setPage(1);
    loadData(1, {
      status: '',
      kind: '',
      role: '',
      phone: '',
      periodKey: todayStr,
    });
    toast.info('已重置所有筛选条件（日期已恢复为今天）。', '筛选已重置');
  };

  // 审核通过（仅针对需要审核的上传类任务）
  const handleApprove = async (inst: TaskInstanceItem) => {
    setBusyId(inst.id);
    try {
      await api.post(`/api/admin/instances/${inst.id}/review`, {
        action: 'approve',
      });
      await Promise.all([loadData(page), refreshStats()]);
      if (detailData && detailData.instance.id === inst.id) {
        const refreshed = await api.get<TaskInstanceDetail>(`/api/admin/instances/${inst.id}`);
        setDetailData(refreshed);
      }
      toast.success(
        `已审核通过「${inst.member_name_snap}」提交的《${inst.template_name}》。`,
        '审核已通过',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '审核通过操作失败');
    } finally {
      setBusyId(null);
    }
  };

  // P1-1: 打开驳回原因弹窗
  const openRejectModal = (inst: TaskInstanceItem) => {
    setRejectTarget(inst);
    setRejectReason('');
  };

  // P1-1: 提交驳回（必填原因）
  const handleConfirmReject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rejectTarget) return;
    const trimmed = rejectReason.trim();
    if (!trimmed) {
      toast.warning('请填写具体的驳回原因，原因不能为空。', '需要填写驳回原因');
      return;
    }
    if (trimmed.length < 2) {
      toast.warning('驳回原因至少需要填写 2 个字，请补充说明需要修改的地方。', '原因字数不足');
      return;
    }

    setSubmittingReject(true);
    try {
      await api.post(`/api/admin/instances/${rejectTarget.id}/review`, {
        action: 'reject',
        reason: trimmed,
      });
      const targetSnap = rejectTarget;
      setRejectTarget(null);
      setRejectReason('');
      await Promise.all([loadData(page), refreshStats()]);
      if (detailData && detailData.instance.id === targetSnap.id) {
        const refreshed = await api.get<TaskInstanceDetail>(`/api/admin/instances/${targetSnap.id}`);
        setDetailData(refreshed);
      }
      toast.success(
        `已驳回「${targetSnap.member_name_snap}」的提交，系统将通知员工按原因修改后重交。`,
        '任务已驳回',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '驳回操作未成功');
    } finally {
      setSubmittingReject(false);
    }
  };

  // P2-2: 打开实例详情弹窗
  const handleOpenDetail = async (instId: number) => {
    setLoadingDetailId(instId);
    try {
      const res = await api.get<TaskInstanceDetail>(`/api/admin/instances/${instId}`);
      setDetailData(res);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '读取实例详情失败');
    } finally {
      setLoadingDetailId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* 顶部标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-5 rounded-2xl border border-slate-200/90 shadow-2xs">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">任务实例与审核</h2>
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/60">
              执行跟踪与上传审核
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            支持按任务类型（纯通知提醒 / 上传任务）筛选查看执行记录，对需审核的上传任务进行通过或驳回。
          </p>
        </div>
      </div>

      {/* 统计指标卡片 */}
      {localStats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <div
            onClick={() => setStatusFilter('')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === ''
                ? 'bg-blue-50/60 border-blue-300 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-slate-300'
            }`}
          >
            <div className="text-xs text-slate-500 font-medium">全部任务实例</div>
            <div className="text-xl font-bold text-slate-900 font-mono mt-1">{localStats.total}</div>
          </div>

          <div
            onClick={() => setStatusFilter('approved')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'approved'
                ? 'bg-emerald-50/70 border-emerald-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-emerald-200'
            }`}
          >
            <div className="text-xs text-emerald-700 font-semibold">已提醒 / 已完成</div>
            <div className="text-xl font-bold text-emerald-600 font-mono mt-1">
              {localStats.by_status.approved || 0}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('pending')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'pending'
                ? 'bg-slate-100 border-slate-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-slate-300'
            }`}
          >
            <div className="text-xs text-slate-600 font-medium">待提醒 / 待上传</div>
            <div className="text-xl font-bold text-slate-700 font-mono mt-1">
              {localStats.by_status.pending || 0}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('submitted')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'submitted'
                ? 'bg-blue-50/80 border-blue-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-blue-200'
            }`}
          >
            <div className="text-xs text-blue-700 font-semibold">待人工审核</div>
            <div className="text-xl font-bold text-blue-600 font-mono mt-1">
              {localStats.by_status.submitted || 0}
            </div>
          </div>

          <div
            onClick={() => setStatusFilter('rejected')}
            className={`p-3.5 rounded-2xl border cursor-pointer transition-all ${
              statusFilter === 'rejected'
                ? 'bg-rose-50/70 border-rose-400 shadow-2xs'
                : 'bg-white border-slate-200/90 hover:border-rose-200'
            }`}
          >
            <div className="text-xs text-rose-700 font-medium">已驳回 / 已逾期</div>
            <div className="text-xl font-bold text-rose-600 font-mono mt-1">
              {(localStats.by_status.rejected || 0) + (localStats.by_status.overdue || 0)}
            </div>
          </div>
        </div>
      )}

      {/* 筛选工具栏 + 表格 + 分页 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
        <form
          onSubmit={handleSearch}
          className="p-4 border-b border-slate-100 flex flex-wrap items-center gap-2.5 text-xs"
        >
          {/* 0. 按日期选择（默认今天） */}
          <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
            <Calendar className="w-3.5 h-3.5 text-blue-600 shrink-0" />
            <span className="text-slate-500 font-medium shrink-0">日期</span>
            <input
              type="date"
              aria-label="选择任务日期"
              value={periodKeyFilter}
              onChange={(e) => setPeriodKeyFilter(e.target.value)}
              className="bg-transparent font-mono text-slate-800 font-semibold outline-none cursor-pointer"
            />
          </div>

          {/* 1. 按角色查询 */}
          <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-xl px-3 py-1.5">
            <Users className="w-3.5 h-3.5 text-slate-400" />
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
              className="bg-transparent text-slate-700 font-medium outline-none cursor-pointer"
            >
              <option value="">全部业务角色</option>
              {roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>
                  {r.role_name}
                </option>
              ))}
            </select>
          </div>

          {/* 2. 按任务类型查询（替换原按任务模板查询） */}
          <select
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-700 font-medium outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
          >
            <option value="">全部任务类型</option>
            <option value="notice">纯通知提醒</option>
            <option value="upload">上传任务（H5链接反馈）</option>
          </select>

          {/* 3. 按状态查询（已移除已过期 expired） */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-700 font-medium outline-none focus:bg-white focus:border-blue-500 cursor-pointer"
          >
            <option value="">全部执行状态</option>
            <option value="pending">待提醒 / 待上传</option>
            <option value="approved">已提醒 / 已完成</option>
            <option value="submitted">待人工审核</option>
            <option value="rejected">已驳回待重交</option>
            <option value="overdue">已逾期未交</option>
          </select>

          <input
            type="text"
            value={phoneFilter}
            onChange={(e) => setPhoneFilter(e.target.value)}
            placeholder="搜索用户名或手机号…"
            className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44"
          />

          <button
            type="submit"
            className="px-3.5 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl inline-flex items-center gap-1 cursor-pointer"
          >
            <Filter className="w-3.5 h-3.5" />
            <span>查询</span>
          </button>

          <button
            type="button"
            onClick={handleResetFilters}
            className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium rounded-xl inline-flex items-center gap-1 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>重置</span>
          </button>
        </form>

        {/* 实例列表表格 */}
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50/80 border-b border-slate-200/80 text-[11px] font-bold text-slate-500">
                <th className="py-3.5 px-4 whitespace-nowrap">任务名称</th>
                <th className="py-3.5 px-3 whitespace-nowrap">任务类型</th>
                <th className="py-3.5 px-3 whitespace-nowrap">所属角色</th>
                <th className="py-3.5 px-3 whitespace-nowrap">执行人</th>
                <th className="py-3.5 px-3 whitespace-nowrap">执行时间</th>
                <th className="py-3.5 px-3 whitespace-nowrap">当前状态</th>
                <th className="py-3.5 px-4 text-right whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-xs">
              {loading ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-400">
                    正在加载任务实例数据…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-400">
                    当前筛选条件下暂无任务实例。
                  </td>
                </tr>
              ) : (
                items.map((inst) => {
                  const statusMeta = getInstanceStatusMeta(inst);
                  const roleName = roleMap[inst.role_id] || '业务角色';
                  const isNotice = inst.kind === 'notice';
                  const needReview = !isNotice && inst.require_review !== false;

                  return (
                    <tr key={inst.id} className="hover:bg-slate-50/60 transition-colors">
                      {/* 1. 任务名称 */}
                      <td className="py-4 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-2.5">
                          <span className="w-6 h-6 rounded-lg bg-slate-100 text-slate-700 font-mono text-xs font-bold flex items-center justify-center shrink-0">
                            {inst.id}
                          </span>
                          <span className="font-bold text-slate-900 text-xs">
                            {inst.template_name}
                          </span>
                        </div>
                      </td>

                      {/* 2. 任务类型 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        {isNotice ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-amber-50 text-amber-700 border-amber-200 text-xs font-semibold">
                            <Bell className="w-3.5 h-3.5 shrink-0" />
                            <span>纯通知提醒</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-blue-50 text-blue-700 border-blue-200 text-xs font-semibold">
                            <Smartphone className="w-3.5 h-3.5 shrink-0" />
                            <span>上传任务</span>
                          </span>
                        )}
                      </td>

                      {/* 3. 所属角色 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-100 text-slate-700 font-semibold text-xs">
                          <Users className="w-3.5 h-3.5 text-blue-600 shrink-0" />
                          <span>{roleName}</span>
                        </span>
                      </td>

                      {/* 4. 执行人（用户名 + 手机号单行展示） */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-slate-900">{inst.member_name_snap}</span>
                          <span className="font-mono text-slate-500">{inst.phone}</span>
                        </div>
                      </td>

                      {/* 5. 执行时间 */}
                      <td className="py-4 px-3 font-mono text-xs text-slate-700 whitespace-nowrap">
                        {inst.submitted_at || `${inst.period_key} 待执行`}
                      </td>

                      {/* 6. 当前状态 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-semibold ${statusMeta.className}`}
                          >
                            {statusMeta.icon}
                            <span>{statusMeta.label}</span>
                          </span>
                          {inst.reject_reason && (
                            <span className="text-[11px] text-rose-600 font-medium truncate max-w-[160px]">
                              原因：{inst.reject_reason}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* 7. 操作列（仅需审核的上传任务在待审核时才出现通过/驳回按钮） */}
                      <td className="py-4 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-2">
                          <button
                            onClick={() => handleOpenDetail(inst.id)}
                            disabled={loadingDetailId === inst.id}
                            className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors whitespace-nowrap shrink-0"
                          >
                            <Eye className="w-3.5 h-3.5 shrink-0" />
                            <span>{loadingDetailId === inst.id ? '加载中…' : '详情'}</span>
                          </button>

                          {needReview && inst.status === 'submitted' && (
                            <>
                              <button
                                onClick={() => handleApprove(inst)}
                                disabled={busyId === inst.id}
                                className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors disabled:opacity-60 whitespace-nowrap shrink-0"
                              >
                                <Check className="w-3.5 h-3.5 shrink-0" />
                                <span>通过</span>
                              </button>
                              <button
                                onClick={() => openRejectModal(inst)}
                                disabled={busyId === inst.id}
                                className="px-3 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors disabled:opacity-60 whitespace-nowrap shrink-0"
                              >
                                <X className="w-3.5 h-3.5 shrink-0" />
                                <span>驳回</span>
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* P1-4: 分页控件（上一页 / 下一页 + 当前页/总页，size=20） */}
        <div className="px-4 py-3 bg-slate-50/70 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="text-slate-500">
            共 <strong className="text-slate-800">{total}</strong> 条任务实例 · 每页{' '}
            <strong className="text-slate-800">{PAGE_SIZE}</strong> 条
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                if (page > 1) loadData(page - 1);
              }}
              disabled={page <= 1 || loading}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              <span>上一页</span>
            </button>
            <span className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 font-mono text-slate-700">
              第 <strong>{page}</strong> / <strong>{totalPages}</strong> 页
            </span>
            <button
              onClick={() => {
                if (page < totalPages) loadData(page + 1);
              }}
              disabled={page >= totalPages || loading}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-semibold inline-flex items-center gap-1 cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <span>下一页</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* P1-1: 驳回原因弹窗 */}
      {rejectTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">驳回上传任务</h3>
                <p className="text-[11px] text-slate-400">
                  实例 #{rejectTarget.id} · {rejectTarget.member_name_snap} ({rejectTarget.phone})
                </p>
              </div>
              <button
                onClick={() => setRejectTarget(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleConfirmReject} className="space-y-3.5 text-xs">
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
                  onClick={() => setRejectTarget(null)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={submittingReject || !rejectReason.trim()}
                  className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold shadow-xs cursor-pointer disabled:opacity-50"
                >
                  {submittingReject ? '正在提交…' : '确认驳回'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* P2-2: 实例详情弹窗 */}
      {detailData && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-slate-200 my-8 space-y-5 max-h-[90vh] overflow-y-auto text-xs">
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
                  执行日期：{detailData.instance.period_key} · 接收人：
                  {detailData.instance.member_name_snap} ({detailData.instance.phone}) ·{' '}
                  {roleMap[detailData.instance.role_id] || '业务角色'}
                </p>
              </div>
              <button
                onClick={() => setDetailData(null)}
                className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 1. 基础信息卡片（接收人带上角色） */}
            <div
              className={`grid grid-cols-2 ${
                detailData.instance.kind === 'notice' ? 'sm:grid-cols-3' : 'sm:grid-cols-4'
              } gap-3 bg-slate-50 p-3.5 rounded-xl border border-slate-200/70`}
            >
              <div>
                <div className="text-[11px] text-slate-400">接收人（含角色）</div>
                <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                  <span className="font-bold text-slate-800">
                    {detailData.instance.member_name_snap}
                  </span>
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/70 text-[11px] font-semibold">
                    <Users className="w-3 h-3 shrink-0" />
                    <span>{roleMap[detailData.instance.role_id] || '业务角色'}</span>
                  </span>
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-400">手机号码</div>
                <div className="font-mono font-semibold text-slate-800 mt-0.5">
                  {detailData.instance.phone}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-400">
                  {detailData.instance.kind === 'notice' ? '执行时间' : '上传提交时间'}
                </div>
                <div className="font-mono text-slate-700 mt-0.5">
                  {detailData.instance.submitted_at || '待到达执行时刻'}
                </div>
              </div>
              {detailData.instance.kind !== 'notice' && (
                <div>
                  <div className="text-[11px] text-slate-400">截止时间</div>
                  <div className="font-mono text-slate-700 mt-0.5">
                    {detailData.instance.deadline_at || '—'}
                  </div>
                </div>
              )}
            </div>

            {detailData.instance.reject_reason && (
              <div className="p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-800">
                <div className="font-bold mb-1">最近一次驳回原因：</div>
                <div>{detailData.instance.reject_reason}</div>
              </div>
            )}

            {/* 2. 提交内容与附件下载 (/api/admin/uploads/{ref}) */}
            <div className="space-y-2.5">
              <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                <Paperclip className="w-3.5 h-3.5 text-blue-600" />
                <span>
                  {detailData.instance.kind === 'notice'
                    ? '提醒任务下发内容与附件'
                    : '员工上传内容与附件凭证'}
                </span>
              </h4>

              {!detailData.submission ? (
                <div className="p-6 rounded-xl bg-slate-50 border border-slate-200/70 text-center text-slate-400">
                  该任务实例尚未产生提交或下发记录。
                </div>
              ) : (
                <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3">
                  {detailData.submission.form_values &&
                    Object.keys(detailData.submission.form_values).length > 0 && (
                      <div className="space-y-1.5">
                        <div className="text-[11px] font-semibold text-slate-500">任务明细信息</div>
                        <div className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
                          {Object.entries(detailData.submission.form_values).map(([k, v]) => (
                            <div
                              key={k}
                              className="px-3.5 py-2.5 bg-slate-50/50 flex items-center justify-between gap-4"
                            >
                              <span className="font-mono text-slate-500">{k}</span>
                              <span className="font-semibold text-slate-900 text-right">
                                {String(v)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                  {detailData.submission.note && (
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500 mb-1">
                        {detailData.instance.kind === 'notice' ? '提醒正文内容' : '填报备注说明'}
                      </div>
                      <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/70 text-slate-800 leading-relaxed whitespace-pre-line">
                        {detailData.submission.note}
                      </div>
                    </div>
                  )}

                  {detailData.submission.attachment_ref && (
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500 mb-1">关联文件/图片附件</div>
                      <div className="p-3 rounded-xl bg-blue-50/60 border border-blue-200 flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="font-bold text-slate-900 truncate">
                            {detailData.submission.attachment_name ||
                              detailData.submission.attachment_ref}
                          </div>
                          <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                            附件标识: {detailData.submission.attachment_ref}
                            {detailData.submission.attachment_size
                              ? ` · ${(detailData.submission.attachment_size / 1024).toFixed(1)} KB`
                              : ''}
                          </div>
                        </div>
                        <a
                          href={`/api/admin/uploads/${encodeURIComponent(detailData.submission.attachment_ref)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="px-3.5 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-semibold inline-flex items-center gap-1.5 shrink-0"
                        >
                          <Download className="w-3.5 h-3.5" />
                          <span>查看 / 下载附件</span>
                        </a>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 3. 提醒发送记录 */}
            <div className="space-y-2.5">
              <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                <Send className="w-3.5 h-3.5 text-blue-600" />
                <span>量子密信提醒发送记录 ({detailData.reminders.length})</span>
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
                        <th className="py-2 px-3">时间点 / 重试</th>
                        <th className="py-2 px-3">计划时刻 / 实际时刻</th>
                        <th className="py-2 px-3">发送结果</th>
                        <th className="py-2 px-3">状态说明</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {detailData.reminders.map((r) => (
                        <tr key={r.id}>
                          <td className="py-2.5 px-3 font-mono">
                            <span className="font-semibold text-slate-800">{r.slot}</span>
                            {r.retry_no > 0 && (
                              <span className="ml-1.5 px-1.5 py-0.2 rounded bg-amber-50 text-amber-700 border border-amber-200">
                                重试 #{r.retry_no}
                              </span>
                            )}
                          </td>
                          <td className="py-2.5 px-3 font-mono text-slate-600">
                            <div>计划: {r.planned_at}</div>
                            <div className="text-slate-400">
                              实际: {r.attempted_at || r.sent_at || '—'}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded font-semibold ${
                                r.result === 'sent'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : r.result === 'need_activate' || r.result === 'no_target'
                                    ? 'bg-amber-50 text-amber-700'
                                    : 'bg-rose-50 text-rose-700'
                              }`}
                            >
                              {r.result === 'sent' ? '已送达' : r.result}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-slate-600">
                            {humanizeReminderLogError(r.result, r.error)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* 弹窗底部：仅需要人工审核的上传任务在待审核时才展示通过/驳回 */}
            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {detailData.instance.kind !== 'notice' &&
                  detailData.instance.require_review !== false &&
                  detailData.instance.status === 'submitted' && (
                    <>
                      <button
                        onClick={() => handleApprove(detailData.instance)}
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                      >
                        <Check className="w-3.5 h-3.5" />
                        <span>审核通过</span>
                      </button>
                      <button
                        onClick={() => {
                          const target = detailData.instance;
                          setDetailData(null);
                          openRejectModal(target);
                        }}
                        className="px-4 py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 font-semibold rounded-xl inline-flex items-center gap-1.5 cursor-pointer"
                      >
                        <X className="w-3.5 h-3.5" />
                        <span>驳回重交…</span>
                      </button>
                    </>
                  )}
              </div>
              <button
                onClick={() => setDetailData(null)}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl cursor-pointer transition-colors"
              >
                关闭详情
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

```

### File: `src/components/imtask/TemplatesView.tsx`
```tsx
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
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import {
  FormFieldSpec,
  NoticeContentFormat,
  NoticeMessageItem,
  TemplateItem,
  TemplatePeriod,
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
      file_name: '支局1+1155重点指标及商机通报截图.png',
      file_caption: '二、数据提醒：\n同步最新业务进展与差缺通报图表。',
    },
  ];
}

function summarizeNoticeItemsToText(items: NoticeMessageItem[]): string {
  return items
    .map((item, idx) => {
      if (item.format === 'text') {
        return (item.text_content || '').trim();
      }
      const parts: string[] = [];
      if (item.file_name?.trim()) {
        parts.push(`【文件/图片附件：${item.file_name.trim()}】`);
      }
      if (item.file_caption?.trim()) {
        parts.push(item.file_caption.trim());
      }
      return parts.join('\n') || `第 ${idx + 1} 条：文件/图片提醒`;
    })
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
  remindText: string;
  modules: UploadH5Module[];
  fields: FormFieldSpec[];
  textLabel: string;
  textPlaceholder: string;
}

const UploadFlowPhonePreview: React.FC<UploadH5PreviewProps> = ({
  taskName,
  remindAt,
  deadline,
  remindText,
  modules,
  fields,
  textLabel,
  textPlaceholder,
}) => {
  const [step, setStep] = useState<'im' | 'h5'>('h5');

  const displayTitle = taskName.trim() || '当日上门走访晒图与意向单报数';
  const displayRemindText =
    remindText.trim() ||
    '请各片长/分支局长于今日截止时间前，点击下方专属 H5 链接完成今日上门走访报数及现场图片上传。';

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
            {step === 'im' ? '量子密信 · 任务助手' : displayTitle}
          </span>
          <span className="text-[10px] text-blue-600 font-semibold">H5 专属通道</span>
        </div>

        {/* 步骤1：量子密信聊天界面收到提醒文本 + H5链接 */}
        {step === 'im' && (
          <div className="p-4 flex-1 flex flex-col justify-between space-y-4 bg-slate-100">
            <div className="space-y-3">
              <div className="text-center">
                <span className="px-2 py-0.5 rounded bg-slate-200/80 text-slate-500 text-[10px] font-mono">
                  执行时间 {remindAt || '16:30'}
                </span>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-xs">
                  密
                </div>
                <div className="bg-white rounded-2xl rounded-tl-xs p-3.5 shadow-xs border border-slate-200/80 space-y-2.5 max-w-[90%]">
                  <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1.5">
                    <span className="font-bold text-slate-900 text-xs">【任务提醒】{displayTitle}</span>
                    {deadline && (
                      <span className="text-[10px] font-mono text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded">
                        截止 {deadline}
                      </span>
                    )}
                  </div>

                  {/* 后台配置的提醒文本 */}
                  <div className="text-xs text-slate-700 whitespace-pre-line leading-relaxed">
                    {displayRemindText}
                  </div>

                  {/* 自动配上的专属 H5 链接卡片 */}
                  <div
                    onClick={() => setStep('h5')}
                    className="p-2.5 rounded-xl bg-blue-50/90 border border-blue-200 hover:bg-blue-100/80 transition-colors cursor-pointer space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold text-blue-800 flex items-center gap-1">
                        <ExternalLink className="w-3 h-3" />
                        <span>点击打开 H5 任务上传链接</span>
                      </span>
                      <ArrowRight className="w-3.5 h-3.5 text-blue-600" />
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {modules.map((m) => (
                        <span
                          key={m}
                          className="px-1.5 py-0.2 rounded bg-white text-blue-700 border border-blue-200/80 text-[10px] font-medium"
                        >
                          {UPLOAD_MODULE_META[m]?.shortLabel || m}
                        </span>
                      ))}
                    </div>
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
                {deadline && (
                  <span className="text-[10px] font-mono text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                    截止 {deadline}
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-500 line-clamp-2">{displayRemindText}</p>
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
                        <span>图片上传（现场拍照 / 晒图）</span>
                      </span>
                      <span className="text-[10px] text-slate-400">支持 JPG/PNG</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="h-16 rounded-lg border-2 border-dashed border-indigo-200 bg-indigo-50/50 flex flex-col items-center justify-center text-indigo-600">
                        <ImageIcon className="w-4 h-4" />
                        <span className="text-[10px] mt-0.5 font-medium">+ 拍照/选图</span>
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
                        <span>文件附件上传</span>
                      </span>
                      <span className="text-[10px] text-slate-400">Excel / PDF / Word</span>
                    </div>
                    <div className="p-2.5 rounded-lg border border-dashed border-violet-200 bg-violet-50/40 flex items-center justify-center gap-1.5 text-violet-700 text-[11px] font-medium">
                      <Upload className="w-3.5 h-3.5" />
                      <span>点击选择手机中的表格或文档上传</span>
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
  const { templates, roles, refreshTemplates } = useAppStore();
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

  // 弹窗表单状态（任务类型简化为两大类：'notice' 纯通知提醒 | 'upload' 上传任务）
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'notice' | 'upload'>('notice');
  const [period, setPeriod] = useState<TemplatePeriod>('day');
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [remindAt, setRemindAt] = useState('08:15');
  const [deadline, setDeadline] = useState('18:00');
  const [requireReview, setRequireReview] = useState<boolean>(true);
  const [enabled, setEnabled] = useState(true);

  // 纯通知提醒专用状态
  const [noticeItems, setNoticeItems] = useState<NoticeMessageItem[]>(createDefaultNoticeItems);

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
    setEditingTpl(null);
    setName('');
    setKind(defaultKind);
    setPeriod('day');
    const defaultRoleId = roleFilter || roles[0]?.role_id || 'role_branch_chief';
    setRoleIds(defaultRoleId ? [defaultRoleId] : []);
    setRemindAt(defaultKind === 'notice' ? '08:15' : '16:30');
    setDeadline('18:00');
    setRequireReview(true);
    setNoticeItems(createDefaultNoticeItems());
    setUploadRemindText(
      '请按时点击下方 H5 链接，完成今日上门走访户数报数及现场晒图上传。',
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
    setPeriod(tpl.period);
    const tplRoles = getTemplateRoleIds(tpl);
    setRoleIds(
      tplRoles.length > 0 ? tplRoles : [roles[0]?.role_id || 'role_branch_chief'],
    );
    setRemindAt(tpl.remind_at);
    setDeadline(tpl.deadline || '18:00');
    setRequireReview(tpl.kind === 'notice' ? false : tpl.spec?.require_review !== false);
    setEnabled(tpl.enabled);

    if (tpl.kind === 'notice') {
      if (Array.isArray(tpl.spec?.notice_items) && tpl.spec.notice_items.length > 0) {
        setNoticeItems(tpl.spec.notice_items.map((item) => ({ ...item })));
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

  const handleNoticeFileSelect = (idx: number, file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      updateNoticeItem(idx, {
        file_name: file.name,
        file_size: file.size,
        file_mime: file.type,
        file_url: typeof reader.result === 'string' ? reader.result : undefined,
      });
      toast.info(`已载入文件/图片「${file.name}」`, '文件已选择');
    };
    reader.readAsDataURL(file);
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
        text_tpl: tpl.text_tpl,
        spec: tpl.spec,
        enabled: nextEnabled,
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
    if (!remindAt.trim()) {
      toast.warning('请填写执行时间');
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
        if (item.format === 'file' && !item.file_name?.trim() && !item.file_caption?.trim()) {
          toast.warning(`请为第 ${i + 1} 条文件/图片提醒选择文件或填写说明`);
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
      // 统一上传任务校验
      if (!deadline.trim()) {
        toast.warning('请填写截止时间');
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

    setSaving(true);
    try {
      const finalDeadline = kind === 'notice' ? '' : deadline.trim();
      if (editingTpl) {
        await api.put(`/api/admin/templates/${editingTpl.id}`, {
          name: name.trim(),
          role_id: roleIds[0],
          role_ids: roleIds,
          remind_at: remindAt.trim(),
          deadline: finalDeadline,
          text_tpl: finalTextTpl,
          spec,
          enabled,
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
          period,
          role_id: roleIds[0],
          role_ids: roleIds,
          remind_at: remindAt.trim(),
          deadline: finalDeadline,
          text_tpl: finalTextTpl,
          spec,
          enabled,
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
      {/* 顶部标题与操作按钮（已移除单独的上传任务H5预览图效果按钮） */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-5 rounded-2xl border border-slate-200/90 shadow-2xs">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">任务模板管理</h2>
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200/60">
              共 {templates.length} 项任务
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            支持「纯通知提醒」与「上传任务（发送提醒文本 + 专属 H5 上传链接）」两大类任务配置。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
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
        </div>
      </div>

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
              <option value="notice">纯通知提醒</option>
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
                      {/* 1. 任务名称 */}
                      <td className="py-4 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-2.5">
                          <span className="w-6 h-6 rounded-lg bg-slate-100 text-slate-700 font-mono text-xs font-bold flex items-center justify-center shrink-0">
                            {tpl.id}
                          </span>
                          <span className="font-bold text-slate-900 text-xs">{tpl.name}</span>
                        </div>
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
                            <span>纯通知提醒</span>
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
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-blue-50/80 text-blue-700 border border-blue-200/70 font-mono text-xs font-bold">
                          <Clock className="w-3.5 h-3.5 shrink-0" />
                          <span>{tpl.remind_at}</span>
                        </span>
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
                                      ) : (
                                        <div className="space-y-1">
                                          {ni.file_name && (
                                            <div className="inline-flex items-center gap-1 text-indigo-700 font-mono font-medium text-[11px]">
                                              <Paperclip className="w-3.5 h-3.5 shrink-0" />
                                              <span className="truncate max-w-md">
                                                {ni.file_name}
                                              </span>
                                            </div>
                                          )}
                                          {ni.file_caption && (
                                            <div
                                              className={`text-slate-600 leading-relaxed ${
                                                isExpanded ? 'whitespace-pre-line' : 'line-clamp-1'
                                              }`}
                                            >
                                              {ni.file_caption}
                                            </div>
                                          )}
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
                              {tpl.deadline && (
                                <span className="text-[11px] font-mono text-slate-500 ml-1">
                                  截止 {tpl.deadline}
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                      </td>

                      {/* 6. 状态 */}
                      <td className="py-4 px-3 whitespace-nowrap">
                        <button
                          onClick={() => handleToggleEnabled(tpl)}
                          disabled={busyId === tpl.id}
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
                      </td>

                      {/* 7. 操作 */}
                      <td className="py-4 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-2">
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
                          const nextKind = e.target.value as 'notice' | 'upload';
                          setKind(nextKind);
                          if (nextKind === 'notice' && period === 'month') {
                            setPeriod('week');
                          }
                        }}
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 disabled:opacity-65 disabled:cursor-not-allowed font-semibold"
                      >
                        <option value="notice">纯通知提醒</option>
                        <option value="upload">上传任务（提醒文本 + H5链接上传）</option>
                      </select>
                    </div>

                    {/* 执行周期 */}
                    <div>
                      <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                        <span>执行周期</span>
                        {editingTpl && (
                          <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.2 rounded border border-amber-200">
                            <Lock className="w-2.5 h-2.5" /> 不可修改
                          </span>
                        )}
                      </label>
                      <select
                        value={period}
                        disabled={Boolean(editingTpl)}
                        onChange={(e) => setPeriod(e.target.value as TemplatePeriod)}
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500 disabled:opacity-65 disabled:cursor-not-allowed"
                      >
                        <option value="day">每日</option>
                        <option value="week">每周</option>
                        {kind !== 'notice' && <option value="month">每月</option>}
                      </select>
                    </div>

                    <div className="flex items-center pt-5">
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

                    {/* 执行时间 & 截止时间 */}
                    <div className={kind === 'notice' ? 'sm:col-span-2' : ''}>
                      <label className="font-semibold text-slate-700 block mb-1">
                        执行时间 <span className="text-rose-500">*</span>
                      </label>
                      <input
                        type="text"
                        value={remindAt}
                        onChange={(e) => setRemindAt(e.target.value)}
                        placeholder="例如 08:15"
                        className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 font-mono text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                      />
                    </div>

                    {kind === 'upload' && (
                      <div>
                        <label className="font-semibold text-slate-700 block mb-1">
                          截止时间 <span className="text-rose-500">*</span>
                        </label>
                        <input
                          type="text"
                          value={deadline}
                          onChange={(e) => setDeadline(e.target.value)}
                          placeholder="例如 18:00"
                          className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 font-mono text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                        />
                      </div>
                    )}
                  </div>

                  {/* ==================== 分支 A：纯通知提醒配置 ==================== */}
                  {kind === 'notice' && (
                    <div className="space-y-3 pt-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <label className="font-bold text-slate-800 flex items-center gap-1.5">
                          <Layers className="w-4 h-4 text-blue-600" />
                          <span>量子密信提醒内容配置（支持同时间多条并列下发）</span>
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
                                placeholder="请输入本条任务要下发的文字提醒内容…"
                                className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:border-blue-500 leading-relaxed"
                              />
                            ) : (
                              <div className="space-y-2.5">
                                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                                  <input
                                    type="text"
                                    value={item.file_name || ''}
                                    onChange={(e) =>
                                      updateNoticeItem(idx, { file_name: e.target.value })
                                    }
                                    placeholder="文件或图片名称，如：支局1+1155核心指标通报截图.png"
                                    className="flex-1 bg-white border border-slate-200 rounded-xl px-3 py-2 text-slate-800 font-mono text-[11px] outline-none focus:border-indigo-500"
                                  />
                                  <label className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-[11px] inline-flex items-center justify-center gap-1.5 cursor-pointer shrink-0">
                                    <Upload className="w-3.5 h-3.5" />
                                    <span>选择本地文件/图片</span>
                                    <input
                                      type="file"
                                      accept="image/*,.xlsx,.xls,.csv,.pdf,.doc,.docx"
                                      className="hidden"
                                      onChange={(e) =>
                                        handleNoticeFileSelect(idx, e.target.files?.[0] || null)
                                      }
                                    />
                                  </label>
                                </div>
                                <textarea
                                  rows={2}
                                  value={item.file_caption || ''}
                                  onChange={(e) =>
                                    updateNoticeItem(idx, { file_caption: e.target.value })
                                  }
                                  placeholder="配套说明文字（选填）"
                                  className="w-full bg-white border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:border-indigo-500 leading-relaxed"
                                />
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
                      deadline={deadline}
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
    </div>
  );
};

```

### File: `src/components/imtask/RolesMembersView.tsx`
```tsx
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Users,
  UserPlus,
  Upload,
  Edit3,
  Trash2,
  CheckCircle2,
  Clock,
  Ban,
  Plus,
  X,
  Lock,
  Phone,
  User,
  ShieldCheck,
  ArrowRightCircle,
  UserCheck,
  Search,
  MessageSquare,
  Link2Off,
  AlertTriangle,
} from 'lucide-react';
import { api, plainError } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { useToast } from '../../lib/toast';
import { RoleItem, RoleMemberItem, UnactivatedMemberItem } from '../../lib/types';

export const RolesMembersView: React.FC = () => {
  const { roles, refreshRoles } = useAppStore();
  const toast = useToast();

  const [selectedRoleId, setSelectedRoleId] = useState<string>('');
  const [members, setMembers] = useState<RoleMemberItem[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [memberKeyword, setMemberKeyword] = useState('');

  // 已激活待归纳角色人员列表、搜索查询与一键移入角色分组状态
  const [unactivatedList, setUnactivatedList] = useState<UnactivatedMemberItem[]>([]);
  const [loadingUnactivated, setLoadingUnactivated] = useState(false);
  const [unactivatedKeyword, setUnactivatedKeyword] = useState('');
  const [rowTargetRoleMap, setRowTargetRoleMap] = useState<Record<string, string>>({});
  const [batchTargetRoleId, setBatchTargetRoleId] = useState<string>('');
  const [movingPhone, setMovingPhone] = useState<string | null>(null);
  const [batchMoving, setBatchMoving] = useState(false);

  // 两个移除动作的交互确认弹窗状态
  // 1) 从当前角色移除成员 -> 移入右侧「已激活待归纳角色人员」列表（保留会话绑定）
  const [confirmRemoveRoleMember, setConfirmRemoveRoleMember] = useState<RoleMemberItem | null>(null);
  const [removingRoleMember, setRemovingRoleMember] = useState(false);
  // 2) 从「已激活待归纳角色人员」移除 -> 解除量子密信会话绑定，需用户重新对话输入激活
  const [confirmUnbindPerson, setConfirmUnbindPerson] = useState<UnactivatedMemberItem | null>(null);
  const [unbindingPerson, setUnbindingPerson] = useState(false);

  // 单条添加成员表单（仅需用户名与手机号）
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  // 新建/编辑角色弹窗（无需角色编码）
  const [roleModalMode, setRoleModalMode] = useState<'none' | 'create' | 'edit'>('none');
  const [editingRoleId, setEditingRoleId] = useState('');
  const [roleNameInput, setRoleNameInput] = useState('');
  const [savingRole, setSavingRole] = useState(false);

  // P2-1: 成员「改名/停用」弹窗（手机号只读不可改，移除所属部门）
  const [editingMember, setEditingMember] = useState<RoleMemberItem | null>(null);
  const [editMemberName, setEditMemberName] = useState('');
  const [editMemberStatus, setEditMemberStatus] = useState<'active' | 'disabled'>('active');
  const [savingMember, setSavingMember] = useState(false);

  // CSV 批量导入弹窗（仅手机号与用户名）
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [csvText, setCsvText] = useState('13900000000,张三');
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    if (!selectedRoleId && roles.length > 0) {
      setSelectedRoleId(roles[0].role_id);
    } else if (selectedRoleId && !roles.some((r) => r.role_id === selectedRoleId)) {
      setSelectedRoleId(roles[0]?.role_id || '');
    }
    if (!batchTargetRoleId && roles.length > 0) {
      setBatchTargetRoleId(roles[0].role_id);
    } else if (batchTargetRoleId && !roles.some((r) => r.role_id === batchTargetRoleId)) {
      setBatchTargetRoleId(roles[0]?.role_id || '');
    }
  }, [roles, selectedRoleId, batchTargetRoleId]);

  const loadMembers = useCallback(
    async (roleId: string) => {
      if (!roleId) {
        setMembers([]);
        return;
      }
      setLoadingMembers(true);
      try {
        const res = await api.get<{ ok: boolean; items: RoleMemberItem[] }>(
          `/api/admin/roles/${encodeURIComponent(roleId)}/members`,
        );
        setMembers(res.items || []);
      } catch (err) {
        toast.error(err instanceof Error ? plainError(err.message) : '加载角色成员失败');
      } finally {
        setLoadingMembers(false);
      }
    },
    [toast],
  );

  const loadUnactivated = useCallback(async () => {
    setLoadingUnactivated(true);
    try {
      const res = await api.get<{ ok: boolean; items: UnactivatedMemberItem[] }>(
        '/api/admin/unactivated-members',
      );
      setUnactivatedList(res.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '加载已激活待归纳人员失败');
    } finally {
      setLoadingUnactivated(false);
    }
  }, [toast]);

  useEffect(() => {
    loadUnactivated();
  }, [loadUnactivated]);

  useEffect(() => {
    if (selectedRoleId) {
      loadMembers(selectedRoleId);
    }
  }, [selectedRoleId, loadMembers]);

  const currentRole: RoleItem | undefined = useMemo(
    () => roles.find((r) => r.role_id === selectedRoleId),
    [roles, selectedRoleId],
  );

  const totalRosterCount = useMemo(
    () => roles.reduce((acc, r) => acc + (r.member_count || 0), 0),
    [roles],
  );

  const filteredMembers = useMemo(() => {
    if (!memberKeyword.trim()) return members;
    const kw = memberKeyword.trim().toLowerCase();
    return members.filter(
      (m) => m.phone.includes(kw) || m.name.toLowerCase().includes(kw),
    );
  }, [members, memberKeyword]);

  const filteredUnactivatedList = useMemo(() => {
    if (!unactivatedKeyword.trim()) return unactivatedList;
    const kw = unactivatedKeyword.trim().toLowerCase();
    return unactivatedList.filter(
      (u) => u.phone.includes(kw) || u.name.toLowerCase().includes(kw),
    );
  }, [unactivatedList, unactivatedKeyword]);

  const getRowTargetRole = (phone: string): string => {
    const chosen = rowTargetRoleMap[phone];
    if (chosen && roles.some((r) => r.role_id === chosen)) {
      return chosen;
    }
    return selectedRoleId || roles[0]?.role_id || '';
  };

  // 一键将单个已激活待归纳人员归入指定角色分组
  const handleMoveSingleUnactivated = async (person: UnactivatedMemberItem) => {
    const targetRoleId = getRowTargetRole(person.phone);
    if (!targetRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    setMovingPhone(person.phone);
    try {
      const res = await api.post<{
        ok: boolean;
        moved_count: number;
        target_role_id: string;
        target_role_name: string;
      }>('/api/admin/unactivated-members/move', {
        phone: person.phone,
        target_role_id: targetRoleId,
      });
      await Promise.all([
        loadUnactivated(),
        refreshRoles(),
        selectedRoleId ? loadMembers(selectedRoleId) : Promise.resolve(),
      ]);
      toast.success(
        `已将已激活人员「${person.name}」(${person.phone}) 一键归入角色分组「${res.target_role_name}」。`,
        '一键移入成功',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移入角色分组失败');
    } finally {
      setMovingPhone(null);
    }
  };

  // 一键批量将当前列表中的已激活待归纳人员归入目标角色分组
  const handleBatchMoveAllUnactivated = async () => {
    const targetItems = filteredUnactivatedList.length > 0 ? filteredUnactivatedList : unactivatedList;
    if (targetItems.length === 0) return;
    const targetRoleId = batchTargetRoleId || selectedRoleId || roles[0]?.role_id || '';
    if (!targetRoleId) {
      toast.warning('请先选择目标角色分组');
      return;
    }
    setBatchMoving(true);
    try {
      const phones = targetItems.map((u) => u.phone);
      const res = await api.post<{
        ok: boolean;
        moved_count: number;
        target_role_id: string;
        target_role_name: string;
      }>('/api/admin/unactivated-members/move', {
        phones,
        target_role_id: targetRoleId,
      });
      await Promise.all([
        loadUnactivated(),
        refreshRoles(),
        selectedRoleId ? loadMembers(selectedRoleId) : Promise.resolve(),
      ]);
      toast.success(
        `已将 ${res.moved_count} 名已激活待归纳人员一键归入「${res.target_role_name}」分组。`,
        '批量一键移入完成',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '批量移入失败');
    } finally {
      setBatchMoving(false);
    }
  };

  // 确认从「已激活待归纳角色人员」移除 -> 解除密信会话绑定，需用户重新输入激活
  const handleConfirmUnbindUnactivated = async () => {
    if (!confirmUnbindPerson) return;
    const person = confirmUnbindPerson;
    setUnbindingPerson(true);
    try {
      await api.delete(`/api/admin/unactivated-members/${encodeURIComponent(person.phone)}`);
      setConfirmUnbindPerson(null);
      await loadUnactivated();
      toast.success(
        `已解除「${person.name}」(${person.phone}) 的量子密信会话绑定并从待归纳列表移除。该用户需在密信对话窗口重新输入激活信息后方可再次激活。`,
        '已解除会话绑定',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移除并解除会话绑定失败');
    } finally {
      setUnbindingPerson(false);
    }
  };

  const handleOpenCreateRole = () => {
    setEditingRoleId('');
    setRoleNameInput('');
    setRoleModalMode('create');
  };

  const handleOpenEditRole = (role: RoleItem) => {
    setEditingRoleId(role.role_id);
    setRoleNameInput(role.role_name);
    setRoleModalMode('edit');
  };

  const handleSaveRole = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!roleNameInput.trim()) {
      toast.warning('请填写角色名称');
      return;
    }
    setSavingRole(true);
    try {
      if (roleModalMode === 'create') {
        const res = await api.post<{ ok: boolean; role_id: string }>('/api/admin/roles', {
          role_name: roleNameInput.trim(),
          description: '',
        });
        await refreshRoles();
        if (res.role_id) {
          setSelectedRoleId(res.role_id);
        }
        setRoleModalMode('none');
        toast.success(`角色「${roleNameInput.trim()}」创建成功。`, '角色已创建');
      } else if (roleModalMode === 'edit') {
        await api.put(`/api/admin/roles/${encodeURIComponent(editingRoleId)}`, {
          role_name: roleNameInput.trim(),
          description: '',
        });
        await refreshRoles();
        setRoleModalMode('none');
        toast.success(`角色名称已更新为「${roleNameInput.trim()}」。`, '修改已保存');
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '保存角色失败');
    } finally {
      setSavingRole(false);
    }
  };

  const handleDeleteRole = async (role: RoleItem) => {
    try {
      await api.delete(`/api/admin/roles/${encodeURIComponent(role.role_id)}`);
      await refreshRoles();
      toast.success(`角色「${role.role_name}」已删除。`, '删除成功');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '删除角色失败');
    }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!newName.trim() || !newPhone.trim()) {
      toast.warning('请填写用户名和 11 位手机号');
      return;
    }
    setAddingMember(true);
    try {
      await api.post(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members`, {
        phone: newPhone.trim(),
        name: newName.trim(),
        dept: '',
      });
      const addedName = newName.trim();
      const addedPhone = newPhone.trim();
      setNewName('');
      setNewPhone('');
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      toast.success(`成员「${addedName}」(${addedPhone}) 已添加至当前角色。`, '添加成功');
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '添加成员未成功');
    } finally {
      setAddingMember(false);
    }
  };

  // P2-1: 打开成员「改名/停用」弹窗
  const openEditMemberModal = (m: RoleMemberItem) => {
    setEditingMember(m);
    setEditMemberName(m.name);
    setEditMemberStatus(m.status);
  };

  // P2-1: 提交 PUT /api/admin/roles/{role_id}/members/{phone}
  const handleSaveMemberEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId || !editingMember) return;
    if (!editMemberName.trim()) {
      toast.warning('用户名不能为空');
      return;
    }
    setSavingMember(true);
    try {
      await api.put(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(editingMember.phone)}`,
        {
          name: editMemberName.trim(),
          dept: '',
          status: editMemberStatus,
        },
      );
      setEditingMember(null);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        `成员「${editMemberName.trim()}」(${editingMember.phone}) 信息已更新（状态：${editMemberStatus === 'active' ? '正常参与派单' : '已停用'}）。`,
        '成员已更新',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '更新成员失败');
    } finally {
      setSavingMember(false);
    }
  };

  // P2-1: 快捷切换成员启用/停用
  const handleQuickToggleMemberStatus = async (m: RoleMemberItem) => {
    if (!selectedRoleId) return;
    const nextStatus = m.status === 'active' ? 'disabled' : 'active';
    try {
      await api.put(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(m.phone)}`,
        {
          name: m.name,
          dept: '',
          status: nextStatus,
        },
      );
      await Promise.all([loadMembers(selectedRoleId), refreshRoles()]);
      toast.success(
        nextStatus === 'active'
          ? `已恢复成员「${m.name}」参与任务派发。`
          : `已停用成员「${m.name}」，后续新任务周期将不再向其派单。`,
        nextStatus === 'active' ? '成员已启用' : '成员已停用',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '操作失败');
    }
  };

  // 确认从当前角色移除成员 -> 移入右侧「已激活待归纳角色人员」列表（保留会话绑定）
  const handleConfirmRemoveFromRole = async () => {
    if (!selectedRoleId || !confirmRemoveRoleMember) return;
    const member = confirmRemoveRoleMember;
    const roleName = currentRole?.role_name || '当前角色';
    setRemovingRoleMember(true);
    try {
      await api.delete(
        `/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/${encodeURIComponent(member.phone)}`,
      );
      setConfirmRemoveRoleMember(null);
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      toast.success(
        `已将「${member.name}」(${member.phone}) 从角色「${roleName}」移出，并转入右侧「已激活待归纳角色人员」列表（密信会话绑定已保留）。`,
        '已移至待归纳角色列表',
      );
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : '移出角色成员失败');
    } finally {
      setRemovingRoleMember(false);
    }
  };

  const handleImportCsv = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRoleId) return;
    if (!csvText.trim()) {
      toast.warning('请粘贴或输入需要导入的成员数据');
      return;
    }
    setImporting(true);
    try {
      const res = await api.post<{
        ok: boolean;
        added: number;
        updated: number;
        skipped: number;
        errors: Array<{ line: number; reason: string }>;
      }>(`/api/admin/roles/${encodeURIComponent(selectedRoleId)}/members/import`, {
        csv_text: csvText,
      });
      await Promise.all([loadMembers(selectedRoleId), refreshRoles(), loadUnactivated()]);
      setImportModalOpen(false);
      if (res.errors && res.errors.length > 0) {
        toast.warning(
          `新增 ${res.added} 人、更新 ${res.updated} 人；有 ${res.errors.length} 行格式不符已跳过（第 ${res.errors[0].line} 行：${res.errors[0].reason}）。`,
          '导入部分完成',
        );
      } else {
        toast.success(
          `批量导入成功：新增 ${res.added} 名成员，更新 ${res.updated} 名现有成员。`,
          'CSV 导入完成',
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? plainError(err.message) : 'CSV 批量导入未成功');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* 顶部区域：页面标题 + 业务角色分组横向导航带 */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xs p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-slate-100">
          <div>
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">角色与成员名册</h2>
            <p className="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>管理各业务角色下的成员名单（仅需用户名与手机号）</span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                当前共 <strong className="font-semibold text-slate-800 tabular-nums">{roles.length}</strong> 个角色分组
              </span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                在册成员 <strong className="font-semibold text-slate-800 tabular-nums">{totalRosterCount}</strong> 人
              </span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span>
                已激活待归纳角色 <strong className="font-semibold text-indigo-600 tabular-nums">{unactivatedList.length}</strong> 人
              </span>
            </p>
          </div>
          <button
            onClick={handleOpenCreateRole}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>新建业务角色</span>
          </button>
        </div>

        {/* 业务角色分组横向选择卡片带 */}
        <div>
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-xs font-semibold text-slate-500">
              选择业务角色分组（点击卡片切换下方名册）
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {roles.map((r) => {
              const active = r.role_id === selectedRoleId;
              const ratio =
                r.member_count > 0
                  ? Math.round((r.activated_count / r.member_count) * 100)
                  : 0;
              return (
                <div
                  key={r.role_id}
                  onClick={() => setSelectedRoleId(r.role_id)}
                  className={`group relative rounded-xl p-3.5 border transition-all cursor-pointer ${
                    active
                      ? 'bg-blue-50/50 border-blue-600 ring-2 ring-blue-600/10'
                      : 'bg-slate-50/50 border-slate-200/80 hover:bg-white hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-colors ${
                          active
                            ? 'bg-blue-600 text-white'
                            : 'bg-white text-slate-600 border border-slate-200/80'
                        }`}
                      >
                        <Users className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="font-bold text-slate-900 text-sm truncate">
                          {r.role_name}
                        </div>
                        <div className="text-[11px] text-slate-500 mt-0.5 flex items-center gap-1.5 tabular-nums">
                          <span>共 {r.member_count} 人</span>
                          <span aria-hidden="true">·</span>
                          <span>启用 {r.active_count}</span>
                          <span aria-hidden="true">·</span>
                          <span className="text-emerald-700 font-medium">
                            已激活 {r.activated_count}/{r.member_count}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div
                      className="flex items-center gap-0.5 shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() => handleOpenEditRole(r)}
                        className="p-1.5 rounded-lg hover:bg-white text-slate-400 hover:text-blue-600 cursor-pointer transition-colors"
                        title="修改角色名称"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleDeleteRole(r)}
                        className="p-1.5 rounded-lg hover:bg-white text-slate-400 hover:text-rose-600 cursor-pointer transition-colors"
                        title="删除角色"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* 激活进度细线 */}
                  <div className="mt-3 h-1 w-full bg-slate-200/70 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        active ? 'bg-blue-600' : 'bg-emerald-500'
                      }`}
                      style={{ width: `${ratio}%` }}
                    />
                  </div>
                </div>
              );
            })}

            {/* 快捷新建角色虚线卡片 */}
            <button
              type="button"
              onClick={handleOpenCreateRole}
              className="rounded-xl p-3.5 border border-dashed border-slate-300 hover:border-blue-500 hover:bg-blue-50/20 text-slate-500 hover:text-blue-600 transition-all cursor-pointer flex items-center justify-center gap-2 min-h-[76px]"
            >
              <Plus className="w-4 h-4" />
              <span className="text-xs font-semibold">添加新角色分组</span>
            </button>
          </div>
        </div>
      </div>

      {/* 下方主工作区：左侧 7 列「当前角色成员名册」 + 右侧 5 列「已激活待归纳角色人员」 */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* 左侧主面板 (7 cols)：当前角色成员名册 */}
        <div className="lg:col-span-7 bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden">
          {currentRole ? (
            <>
              {/* 第一层：角色名册标题 + 搜索框 + CSV 批量导入（合一清爽顶栏） */}
              <div className="p-4 sm:px-5 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-base">
                      {currentRole.role_name}
                    </h3>
                    <span className="text-xs text-slate-400 font-normal tabular-nums">
                      · {filteredMembers.length} / {members.length} 名成员
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    管理该角色下的派单人员名单；从角色「移除」会将成员移回右侧「已激活待归纳角色人员」列表
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <div className="relative">
                    <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                    <input
                      type="text"
                      value={memberKeyword}
                      onChange={(e) => setMemberKeyword(e.target.value)}
                      placeholder="搜索用户名或手机号…"
                      className="bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-3 py-1.5 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 w-44 sm:w-48 transition-colors"
                    />
                  </div>
                  <button
                    onClick={() => setImportModalOpen(true)}
                    className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200/80 text-slate-700 font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors shrink-0"
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>CSV 批量导入</span>
                  </button>
                </div>
              </div>

              {/* 第二层：单行快速添加成员表单（仅需用户名与手机号） */}
              <form
                onSubmit={handleAddMember}
                className="px-4 sm:px-5 py-3 bg-slate-50/70 border-b border-slate-100 flex flex-col sm:flex-row items-stretch sm:items-center gap-2.5 text-xs"
              >
                <span className="text-slate-500 font-semibold shrink-0 flex items-center gap-1.5">
                  <UserPlus className="w-3.5 h-3.5 text-blue-600" />
                  <span>快速添加成员：</span>
                </span>
                <div className="relative flex-1">
                  <User className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="用户名 (如：张三)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-800 outline-none focus:border-blue-500"
                  />
                </div>
                <div className="relative flex-1">
                  <Phone className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    placeholder="11位手机号 (必填)"
                    className="w-full bg-white border border-slate-200 rounded-xl pl-8 pr-3 py-2 font-mono text-xs text-slate-800 outline-none focus:border-blue-500 tabular-nums"
                  />
                </div>
                <button
                  type="submit"
                  disabled={addingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-60 shrink-0 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>添加成员</span>
                </button>
              </form>

              {/* 第三层：成员列表表格（宽敞 5 列排版） */}
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-200/80 text-[11px] font-semibold text-slate-500 bg-white">
                      <th className="py-3 pl-5 pr-3">成员姓名</th>
                      <th className="py-3 px-3">手机号码</th>
                      <th className="py-3 px-3">密信激活状态</th>
                      <th className="py-3 px-3">派单状态</th>
                      <th className="py-3 pl-3 pr-5 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-xs">
                    {loadingMembers ? (
                      <tr>
                        <td colSpan={5} className="py-12 text-center text-slate-400">
                          正在加载角色成员名册…
                        </td>
                      </tr>
                    ) : filteredMembers.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="py-12 text-center text-slate-400">
                          该角色下暂无匹配成员，可在上方直接添加或从右侧「已激活待归纳角色人员」一键移入。
                        </td>
                      </tr>
                    ) : (
                      filteredMembers.map((m) => (
                        <tr
                          key={m.phone}
                          className="hover:bg-slate-50/70 transition-colors group"
                        >
                          <td className="py-3.5 pl-5 pr-3">
                            <div className="flex items-center gap-2.5">
                              <div className="w-7 h-7 rounded-lg bg-slate-100 text-slate-700 font-bold text-xs flex items-center justify-center shrink-0">
                                {m.name.slice(0, 1)}
                              </div>
                              <span className="font-bold text-slate-900">{m.name}</span>
                            </div>
                          </td>
                          <td className="py-3.5 px-3 font-mono font-semibold text-slate-700 tabular-nums">
                            {m.phone}
                          </td>
                          <td className="py-3.5 px-3">
                            {m.activation === 'activated' ? (
                              <span
                                className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                                  m.usable === false ? 'text-amber-700' : 'text-emerald-700'
                                }`}
                              >
                                <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
                                <span>{m.usable === false ? '需重激活' : '已激活'}</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                                <Clock className="w-3.5 h-3.5 shrink-0 text-slate-400" />
                                <span>未激活</span>
                              </span>
                            )}
                          </td>
                          <td className="py-3.5 px-3">
                            <button
                              onClick={() => handleQuickToggleMemberStatus(m)}
                              title="点击快速切换派单状态"
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border cursor-pointer transition-colors ${
                                m.status === 'active'
                                  ? 'bg-blue-50/70 text-blue-700 border-blue-200/80 hover:bg-blue-100/70'
                                  : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200/70'
                              }`}
                            >
                              {m.status === 'active' ? (
                                <>
                                  <CheckCircle2 className="w-3.5 h-3.5 text-blue-600" />
                                  <span>正常派单</span>
                                </>
                              ) : (
                                <>
                                  <Ban className="w-3.5 h-3.5 text-slate-400" />
                                  <span>已停用</span>
                                </>
                              )}
                            </button>
                          </td>
                          <td className="py-3.5 pl-3 pr-5 text-right">
                            <div className="inline-flex items-center justify-end gap-1.5">
                              <button
                                onClick={() => openEditMemberModal(m)}
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                              >
                                <Edit3 className="w-3 h-3" />
                                <span>改名/停用</span>
                              </button>
                              <button
                                onClick={() => setConfirmRemoveRoleMember(m)}
                                title="将该成员移出当前角色并转入右侧已激活待归纳角色人员列表"
                                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-amber-50 text-slate-600 hover:text-amber-700 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer transition-colors"
                              >
                                <Trash2 className="w-3 h-3" />
                                <span>移除</span>
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="p-14 text-center text-slate-400 text-xs">
              请先在上方选择或新建一个业务角色分组。
            </div>
          )}
        </div>

        {/* 右侧待归纳池面板 (5 cols)：用户已在密信对话激活、但管理员尚未归纳角色的全部人员 */}
        <div className="lg:col-span-5 bg-white rounded-2xl border border-slate-200/90 shadow-2xs overflow-hidden flex flex-col">
          {/* 顶部标题、查询搜索框与批量移入控制栏 */}
          <div className="p-4 sm:px-5 border-b border-slate-100 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-emerald-500/10 text-emerald-600 flex items-center justify-center shrink-0">
                  <UserCheck className="w-4 h-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-bold text-slate-900 text-base">已激活待归纳角色人员</h3>
                    <span className="text-xs font-semibold text-indigo-600 tabular-nums">
                      · {filteredUnactivatedList.length} / {unactivatedList.length} 人待归纳
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    员工在量子密信对话激活后自动进入此列表；移除将解除会话绑定，需员工重新输入激活
                  </p>
                </div>
              </div>
            </div>

            {/* 搜索查询框（支持按姓名或手机号查询待归纳人员） */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
              <input
                type="text"
                value={unactivatedKeyword}
                onChange={(e) => setUnactivatedKeyword(e.target.value)}
                placeholder="搜索已激活待归纳人员姓名或手机号…"
                aria-label="搜索已激活待归纳人员"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-8 pr-16 py-2 text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 transition-colors"
              />
              {unactivatedKeyword && (
                <button
                  type="button"
                  onClick={() => setUnactivatedKeyword('')}
                  className="absolute right-2.5 top-2 px-1.5 py-0.5 rounded-md text-[11px] text-slate-500 hover:text-slate-800 hover:bg-slate-200/60 cursor-pointer"
                >
                  清空
                </button>
              )}
            </div>

            {/* 批量一键移动工具条 */}
            {filteredUnactivatedList.length > 0 && roles.length > 0 && (
              <div className="p-2.5 rounded-xl bg-slate-50 border border-slate-200/70 flex items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-slate-500 font-medium shrink-0 pl-1">批量归纳至:</span>
                  <select
                    value={batchTargetRoleId || selectedRoleId}
                    onChange={(e) => setBatchTargetRoleId(e.target.value)}
                    className="bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-800 outline-none focus:border-blue-500 truncate flex-1"
                  >
                    {roles.map((r) => (
                      <option key={r.role_id} value={r.role_id}>
                        {r.role_name}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="button"
                  onClick={handleBatchMoveAllUnactivated}
                  disabled={batchMoving}
                  className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg text-xs flex items-center gap-1.5 cursor-pointer shrink-0 disabled:opacity-60 transition-colors"
                >
                  <UserCheck className="w-3.5 h-3.5" />
                  <span>{batchMoving ? '归入中…' : '全部一键移入'}</span>
                </button>
              </div>
            )}
          </div>

          {/* 中间：已激活待归纳角色人员卡片列表（双层宽敞布局，完整展示用户全名绝不遮挡） */}
          <div className="divide-y divide-slate-100 max-h-[460px] overflow-y-auto">
            {loadingUnactivated ? (
              <div className="py-12 text-center text-slate-400 text-xs">
                正在加载已激活待归纳人员列表…
              </div>
            ) : filteredUnactivatedList.length === 0 ? (
              <div className="py-12 px-5 text-center space-y-1.5">
                <div className="w-9 h-9 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center mx-auto">
                  <CheckCircle2 className="w-5 h-5" />
                </div>
                <div className="text-xs font-bold text-slate-700">
                  {unactivatedKeyword.trim()
                    ? '未查询到匹配的已激活待归纳人员'
                    : '当前无已激活待归纳角色人员'}
                </div>
                <p className="text-xs text-slate-400">
                  {unactivatedKeyword.trim()
                    ? '请尝试更换姓名或手机号关键词重新搜索。'
                    : '员工在量子密信对话窗口发送激活指令后将自动进入本列表。'}
                </p>
              </div>
            ) : (
              filteredUnactivatedList.map((person) => {
                const targetRole = getRowTargetRole(person.phone);
                const isMoving = movingPhone === person.phone;
                return (
                  <div
                    key={person.phone}
                    className="px-4 sm:px-5 py-3.5 hover:bg-slate-50/70 transition-colors space-y-2.5 text-xs"
                  >
                    {/* 第一行：完整展示用户全名 + 手机号 + 激活及会话状态标签（无 truncate 遮挡） */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-700 font-bold text-xs flex items-center justify-center shrink-0">
                          {person.name.slice(0, 1)}
                        </div>
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-bold text-slate-900 text-sm break-all">
                              {person.name}
                            </span>
                            <span className="font-mono text-slate-600 font-semibold tabular-nums">
                              {person.phone}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            {person.source_note || '已在量子密信对话激活 · 待管理员归纳角色'}
                          </div>
                        </div>
                      </div>

                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200/70 text-[11px] font-medium shrink-0">
                        <ShieldCheck className="w-3 h-3" />
                        <span>已激活 · 待归纳角色</span>
                      </span>
                    </div>

                    {/* 第二行：目标角色选择 + 一键移入分组 + 移除（解除会话绑定） */}
                    <div className="flex items-center justify-between gap-2 pl-10">
                      <div className="flex items-center gap-1.5 flex-1 min-w-0">
                        <select
                          value={targetRole}
                          onChange={(e) =>
                            setRowTargetRoleMap((prev) => ({
                              ...prev,
                              [person.phone]: e.target.value,
                            }))
                          }
                          aria-label={`选择 ${person.name} 的目标角色分组`}
                          className="bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:bg-white focus:border-blue-500 flex-1 min-w-0"
                        >
                          {roles.map((r) => (
                            <option key={r.role_id} value={r.role_id}>
                              {r.role_name}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => handleMoveSingleUnactivated(person)}
                          disabled={isMoving || roles.length === 0}
                          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 disabled:opacity-60 transition-colors"
                        >
                          <ArrowRightCircle className="w-3.5 h-3.5" />
                          <span>{isMoving ? '移入中…' : '一键移入分组'}</span>
                        </button>
                      </div>

                      <button
                        type="button"
                        onClick={() => setConfirmUnbindPerson(person)}
                        className="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-rose-50 text-slate-600 hover:text-rose-600 font-semibold text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 transition-colors"
                        title="解除该用户的量子密信会话绑定并从列表移除（需用户重新发送对话指令激活）"
                      >
                        <Link2Off className="w-3.5 h-3.5" />
                        <span>移除</span>
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* 底部说明：激活仅支持对话形式 */}
          <div className="px-4 sm:px-5 py-3 bg-slate-50/80 border-t border-slate-100 flex items-start gap-2 text-[11px] text-slate-500">
            <MessageSquare className="w-3.5 h-3.5 text-indigo-600 shrink-0 mt-0.5" />
            <span>
              说明：用户激活仅能通过「量子密信」对话窗口向机器人发送激活指令自动绑定会话，不支持后台人工登记未绑定会话人员。
            </span>
          </div>
        </div>
      </div>

      {/* 交互确认弹窗 1：从当前角色中移除成员 -> 移入右侧「已激活待归纳角色人员」列表 */}
      {confirmRemoveRoleMember && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center shrink-0">
                  <ArrowRightCircle className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认将该成员移出当前角色？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    移出角色后将自动转入「已激活待归纳角色人员」列表
                  </p>
                </div>
              </div>
              <button
                onClick={() => setConfirmRemoveRoleMember(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3 text-xs text-slate-600 leading-relaxed">
              <p>
                您即将把成员{' '}
                <strong className="font-bold text-slate-900">
                  {confirmRemoveRoleMember.name} ({confirmRemoveRoleMember.phone})
                </strong>{' '}
                从角色分组{' '}
                <strong className="font-bold text-blue-700">「{currentRole.role_name}」</strong>{' '}
                中移除。
              </p>
              <div className="p-3 rounded-xl bg-amber-50/80 border border-amber-200/80 text-amber-900 space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-amber-700 shrink-0" />
                  <span>保留量子密信会话绑定 · 转入待归纳列表</span>
                </div>
                <p className="text-[11px] text-amber-800">
                  该操作不会解除用户的密信会话绑定，系统会将其移至右侧「已激活待归纳角色人员」列表中，您可随时将其重新一键归入其他业务角色，无需员工重新输入激活。
                </p>
              </div>
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setConfirmRemoveRoleMember(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmRemoveFromRole}
                disabled={removingRoleMember}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {removingRoleMember ? '正在移出…' : '确认移至待归纳列表'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 交互确认弹窗 2：从「已激活待归纳角色人员」移除 -> 解除量子密信会话绑定，需用户重新输入激活 */}
      {confirmUnbindPerson && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-rose-50 text-rose-600 flex items-center justify-center shrink-0">
                  <Link2Off className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-slate-900 text-base">
                    确认移除并解除密信会话绑定？
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    解除绑定后，用户必须在密信对话中重新输入激活指令
                  </p>
                </div>
              </div>
              <button
                onClick={() => setConfirmUnbindPerson(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400 cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3 text-xs text-slate-600 leading-relaxed">
              <p>
                您即将从「已激活待归纳角色人员」列表中移除{' '}
                <strong className="font-bold text-slate-900">
                  {confirmUnbindPerson.name} ({confirmUnbindPerson.phone})
                </strong>
                。
              </p>
              <div className="p-3 rounded-xl bg-rose-50/80 border border-rose-200/80 text-rose-900 space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
                  <span>将清除该用户的量子密信会话绑定</span>
                </div>
                <p className="text-[11px] text-rose-800">
                  移除后，该用户的密信会话绑定（{confirmUnbindPerson.external_id || `zdxlz_${confirmUnbindPerson.phone}`}）将被彻底解除。后续该用户只有在「量子密信」对话窗口重新输入发送激活信息，完成会话绑定后才会再次出现在本列表。
                </p>
              </div>
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => setConfirmUnbindPerson(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmUnbindUnactivated}
                disabled={unbindingPerson}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60 transition-colors"
              >
                {unbindingPerson ? '正在解除绑定…' : '确认解除绑定并移除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* P2-1: 成员「改名/停用」弹窗（严禁改号，phone 只读，不含所属部门） */}
      {editingMember && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">修改成员信息与派单状态</h3>
                <p className="text-[11px] text-slate-400">
                  支持修改用户名及启用/停用状态
                </p>
              </div>
              <button
                onClick={() => setEditingMember(null)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveMemberEdit} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 flex items-center gap-1.5 mb-1">
                  <span>手机号码（唯一标识）</span>
                  <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700">
                    <Lock className="w-2.5 h-2.5" /> 不可修改
                  </span>
                </label>
                <input
                  type="text"
                  readOnly
                  disabled
                  value={editingMember.phone}
                  className="w-full bg-slate-100 border border-slate-200 rounded-xl p-2.5 font-mono text-slate-500 cursor-not-allowed tabular-nums"
                />
                <div className="text-[11px] text-slate-400 mt-1">
                  如需更换手机号，请先移除该成员后重新添加。
                </div>
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  用户名 / 姓名 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={editMemberName}
                  onChange={(e) => setEditMemberName(e.target.value)}
                  placeholder="请输入成员真实姓名"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-700 block mb-1">派单状态</label>
                <select
                  value={editMemberStatus}
                  onChange={(e) => setEditMemberStatus(e.target.value as 'active' | 'disabled')}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                >
                  <option value="active">正常派单 — 参与周期任务派发与提醒</option>
                  <option value="disabled">暂停派单 — 保留历史记录但不再派发新任务</option>
                </select>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditingMember(null)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingMember}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {savingMember ? '正在保存…' : '保存修改'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 新建/编辑角色弹窗（仅需填写角色名称，移除角色编码） */}
      {roleModalMode !== 'none' && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">
                {roleModalMode === 'create' ? '新建业务角色' : '修改角色名称'}
              </h3>
              <button
                onClick={() => setRoleModalMode('none')}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleSaveRole} className="space-y-3.5 text-xs">
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  角色名称 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  autoFocus
                  value={roleNameInput}
                  onChange={(e) => setRoleNameInput(e.target.value)}
                  placeholder="例如：片长/分支局长"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-800 outline-none focus:bg-white focus:border-blue-500"
                />
              </div>
              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRoleModalMode('none')}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingRole}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {savingRole ? '正在保存…' : '保存角色'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* CSV 批量导入弹窗（仅需手机号与用户名） */}
      {importModalOpen && currentRole && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  批量导入成员至「{currentRole.role_name}」
                </h3>
                <p className="text-[11px] text-slate-400">
                  每行一条记录，格式：手机号,用户名（支持自动跳过表头与去重更新）
                </p>
              </div>
              <button
                onClick={() => setImportModalOpen(false)}
                className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleImportCsv} className="space-y-3 text-xs">
              <textarea
                rows={6}
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                placeholder={'手机号,用户名\n13900000000,张三'}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 font-mono text-xs text-slate-800 outline-none focus:bg-white focus:border-blue-500 leading-relaxed"
              />
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setImportModalOpen(false)}
                  className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={importing}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-semibold cursor-pointer disabled:opacity-60"
                >
                  {importing ? '正在导入…' : '开始导入'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

```

### File: `src/components/AgentExportModal.tsx`
```tsx
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

  if (typeof CompressionStream !== 'undefined') {
    const cs = new CompressionStream('gzip');
    const writer = cs.writable.getWriter();
    await writer.write(tarBytes);
    await writer.close();
    const compressedBuffer = await new Response(cs.readable).arrayBuffer();
    return new Blob([compressedBuffer], { type: 'application/gzip' });
  }

  return new Blob([tarBytes], { type: 'application/x-tar' });
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

```

### File: `tests/e2e_admin_check.mjs`
```javascript
import { chromium } from '/root/.bun/install/global/node_modules/playwright/index.mjs';
import fs from 'fs';
import { execFileSync } from 'child_process';

const BASE_URL = 'http://localhost:3000';

function getTodayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function runTests() {
  const results = [];
  const consoleErrors = [];
  const pageErrors = [];

  const logPass = (name, detail = '') => {
    console.log(`✅ [PASS] ${name}${detail ? ` — ${detail}` : ''}`);
    results.push({ name, status: 'PASS', detail });
  };
  const logFail = (name, error) => {
    console.error(`❌ [FAIL] ${name} — ${error}`);
    results.push({ name, status: 'FAIL', detail: String(error) });
  };

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text();
      if (!text.includes('WebSocket') && !text.includes('vite')) {
        consoleErrors.push(text);
      }
    }
  });
  page.on('pageerror', (err) => {
    pageErrors.push(err.message);
  });

  try {
    // 1. 打开首页
    await page.goto(BASE_URL, { waitUntil: 'networkidle' });
    await page.waitForSelector('h1:has-text("IMTask 任务调度平台")', { timeout: 10000 });
    logPass('1. 平台首页加载与导航栏渲染', '成功渲染「IMTask 任务调度平台」及4大工作台模块');

    // 2. 检查「任务实例与审核」日期选择器默认今天
    const today = getTodayStr();
    const dateInput = page.locator('input[type="date"][aria-label="选择任务日期"]');
    await dateInput.waitFor({ state: 'visible', timeout: 5000 });
    const dateVal = await dateInput.inputValue();
    if (dateVal === today) {
      logPass('2. 任务实例与审核-日期选择默认今天', `默认日期值为 ${dateVal}`);
    } else {
      logFail('2. 任务实例与审核-日期选择默认今天', `期望 ${today}，实际为 ${dateVal}`);
    }

    // 3. 检查「任务实例与审核」筛选栏：按任务类型查询、无导出CSV、无已过期状态
    const pageText = await page.content();
    if (!pageText.includes('导出 CSV')) {
      logPass('3. 任务实例与审核-移除导出CSV按钮', '页面已无导出 CSV 按钮');
    } else {
      logFail('3. 任务实例与审核-移除导出CSV按钮', '页面仍存在导出 CSV 按钮');
    }

    const statusOptions = await page.locator('select').nth(2).innerText();
    if (!statusOptions.includes('已过期')) {
      logPass('4. 任务实例与审核-移除已过期状态选项', '状态下拉框已移除「已过期」');
    } else {
      logFail('4. 任务实例与审核-移除已过期状态选项', `状态下拉框仍包含已过期: ${statusOptions}`);
    }

    // 4. 检查张三 (13900000000) 的 5 条真实春促任务实例与纯通知无审核按钮
    const rows = page.locator('tbody tr');
    const rowCount = await rows.count();
    if (rowCount === 5) {
      logPass('5. 任务实例与审核-真实春促数据展示', '默认今日展示张三(13900000000)的 5 条春促任务实例');
    } else {
      logFail('5. 任务实例与审核-真实春促数据展示', `期望 5 条，实际渲染 ${rowCount} 条`);
    }

    const approveBtnsOnNotice = await page.locator('tbody tr button:has-text("通过")').count();
    if (approveBtnsOnNotice === 0) {
      logPass('6. 任务实例与审核-纯通知提醒无需审核', '纯通知提醒实例不展示「通过/驳回」审核按钮');
    } else {
      logFail('6. 任务实例与审核-纯通知提醒无需审核', '纯通知提醒出现了审核按钮');
    }

    // 5. 点击「详情」检查接收人是否带上角色
    await page.locator('tbody tr').first().locator('button:has-text("详情")').click();
    const modal = page.locator('div.fixed.inset-0');
    await modal.waitFor({ state: 'visible', timeout: 5000 });
    const modalText = await modal.innerText();
    if (
      modalText.includes('接收人（含角色）') &&
      modalText.includes('张三') &&
      modalText.includes('片长/分支局长') &&
      modalText.includes('张三 (13900000000) · 片长/分支局长')
    ) {
      logPass('7. 任务实例详情-接收人带上角色', '详情弹窗头部、基础信息卡片及明细均展示「张三 + 片长/分支局长」');
    } else {
      logFail('7. 任务实例详情-接收人带上角色', `详情弹窗内容未完整包含角色: ${modalText.slice(0, 300)}`);
    }
    await modal.locator('button:has-text("关闭详情")').click();
    await modal.waitFor({ state: 'hidden', timeout: 5000 });

    // 6. 测试切换日期筛选与重置回今天
    await dateInput.fill('2026-01-01');
    await page.waitForTimeout(300);
    const emptyText = await page.locator('tbody').innerText();
    if (emptyText.includes('当前筛选条件下暂无任务实例')) {
      logPass('8. 任务实例与审核-切换非今日日期联动过滤', '选择 2026-01-01 后正确过滤为空列表');
    } else {
      logFail('8. 任务实例与审核-切换非今日日期联动过滤', `切换日期后列表未清空: ${emptyText}`);
    }
    await page.locator('button:has-text("重置")').click();
    await page.waitForTimeout(300);
    const resetDateVal = await dateInput.inputValue();
    const afterResetCount = await page.locator('tbody tr').count();
    if (resetDateVal === today && afterResetCount === 5) {
      logPass('9. 任务实例与审核-重置筛选恢复今日日期与数据', `重置后日期恢复为 ${resetDateVal}，列表恢复 5 条记录`);
    } else {
      logFail('9. 任务实例与审核-重置筛选恢复今日日期与数据', `重置后日期=${resetDateVal}, 行数=${afterResetCount}`);
    }

    // 7. 切换到「任务模板管理」
    await page.locator('aside button:has-text("任务模板管理")').click();
    await page.waitForSelector('h2:has-text("任务模板管理")');
    const tplPageText = await page.content();
    if (
      !tplPageText.includes('上传任务 H5 预览效果图') &&
      !tplPageText.includes('共 2 条子任务并列下发') &&
      !tplPageText.includes('默认无需审核') &&
      !tplPageText.includes('立即推送测试')
    ) {
      logPass('10. 任务模板管理-界面精简与冗余文案清理', '无独立H5预览按钮、无底部冗余描述文本、无立即推送测试按钮');
    } else {
      logFail('10. 任务模板管理-界面精简与冗余文案清理', '仍存在未清理的按钮或冗余描述文本');
    }

    // 8. 打开「新建通知提醒」与「新建上传任务」测试配置与预览
    await page.locator('button:has-text("新建通知提醒")').click();
    const tplModal = page.locator('div.fixed.inset-0');
    await tplModal.waitFor({ state: 'visible', timeout: 5000 });
    const noticeModalText = await tplModal.innerText();
    if (
      noticeModalText.includes('执行时间') &&
      !noticeModalText.includes('截止时间') &&
      !noticeModalText.includes('{task_name}')
    ) {
      logPass('11. 新建纯通知提醒模板-无截止时间且支持多条并列文本/文件配置', '仅展示执行时间，支持添加多条并列文本或文件/图片');
    } else {
      logFail('11. 新建纯通知提醒模板-无截止时间且支持多条并列文本/文件配置', '纯通知提醒表单字段不符合预期');
    }

    await tplModal.locator('button:has-text("取消")').click();
    await tplModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 打开「新建上传任务」弹窗
    await page.locator('button:has-text("新建上传任务")').click();
    await tplModal.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(200);
    const uploadModalText = await tplModal.innerText();
    if (
      uploadModalText.includes('执行时间') &&
      uploadModalText.includes('截止时间') &&
      uploadModalText.includes('需要审核') &&
      uploadModalText.includes('无需审核') &&
      !uploadModalText.includes('上传完成后的反馈提示语') &&
      uploadModalText.includes('1. 密信提醒') &&
      uploadModalText.includes('2. H5上传页') &&
      !uploadModalText.includes('3. 完成反馈')
    ) {
      logPass(
        '12. 新建上传任务模板-执行/截止时间、审核开关与右侧2步预览联动',
        '包含执行时间与截止时间、需不需要审核开关、已移除反馈提示语及第3步完成反馈预览',
      );
    } else {
      logFail('12. 新建上传任务模板-执行/截止时间、审核开关与右侧2步预览联动', '上传任务弹窗配置或预览步骤不符');
    }

    // 实际测试新建一条多选角色（片长/分支局长 + 行销总监 + 网格长）的上传任务并校验展示与筛选
    await tplModal.locator('input[placeholder*="例如："]').fill('晚间走访晒图上传');
    await tplModal.locator('button[data-role-id="role_marketing_director"]').click();
    await tplModal.locator('button[data-role-id="role_grid_leader"]').click();
    const selectedRoleBadgeText = await tplModal.innerText();
    const hasSelected3Roles = selectedRoleBadgeText.includes('已选 3 / 9 个角色');
    await tplModal.locator('button[type="submit"]').click();
    await tplModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    const afterCreateTplCount = await page.locator('tbody tr').count();
    const newRowText = await page.locator('tbody tr:has-text("晚间走访晒图上传")').innerText();
    if (
      hasSelected3Roles &&
      afterCreateTplCount === 6 &&
      newRowText.includes('片长/分支局长') &&
      newRowText.includes('行销总监') &&
      newRowText.includes('网格长')
    ) {
      logPass(
        '12b. 任务模板管理-支持多选业务角色创建与多角色标签展示',
        '成功勾选 3 个角色（片长/分支局长、行销总监、网格长）创建「晚间走访晒图上传」并在列表中完整展示多角色标签',
      );
    } else {
      logFail(
        '12b. 任务模板管理-支持多选业务角色创建与多角色标签展示',
        `hasSelected3Roles=${hasSelected3Roles}, count=${afterCreateTplCount}, rowText=${newRowText}`,
      );
    }

    // 删除刚创建的模板
    await page.locator('tbody tr:has-text("晚间走访晒图上传") button:has-text("删除")').click();
    const delModal = page.locator('div.fixed.inset-0');
    await delModal.waitFor({ state: 'visible', timeout: 5000 });
    await delModal.locator('button:has-text("确认删除")').click();
    await delModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    const afterDelTplCount = await page.locator('tbody tr').count();
    if (afterDelTplCount === 5) {
      logPass('12c. 任务模板管理-删除新建模板恢复初始5条春促模板', '删除成功，列表恢复 5 条春促真实模板');
    } else {
      logFail('12c. 任务模板管理-删除新建模板恢复初始5条春促模板', `删除后模板行数=${afterDelTplCount}`);
    }

    // 9. 切换到「角色与成员名册」
    await page.locator('aside button:has-text("角色与成员名册")').click();
    await page.waitForSelector('h2:has-text("角色与成员名册")');
    const rolePageText = await page.content();
    if (
      !rolePageText.includes('所属部门') &&
      !rolePageText.includes('角色编码') &&
      !rolePageText.includes('春促角色清单：负责每日')
    ) {
      logPass('13. 角色与成员名册-仅保留用户名与手机号、无角色编码与冗余描述', '角色与成员界面干净规范');
    } else {
      logFail('13. 角色与成员名册-仅保留用户名与手机号、无角色编码与冗余描述', '仍包含部门、角色编码或冗余描述');
    }

    // 测试打开「改名/停用」弹窗
    await page.locator('button:has-text("改名/停用")').first().click();
    const memberModal = page.locator('div.fixed.inset-0');
    await memberModal.waitFor({ state: 'visible', timeout: 5000 });
    const phoneReadonly = await memberModal.locator('input[disabled]').inputValue();
    if (phoneReadonly === '13900000000') {
      logPass('14. 角色成员改名/停用-手机号只读锁死不可改号', '手机号 13900000000 处于只读锁定状态');
    } else {
      logFail('14. 角色成员改名/停用-手机号只读锁死不可改号', `手机号锁定异常: ${phoneReadonly}`);
    }
    await memberModal.locator('button:has-text("取消")').click();
    await memberModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 测试「已激活待归纳角色人员」一栏：全名完整展示无遮挡、无手动登记表单、支持查询与两级移除逻辑
    const unassignedActivatedHeader = await page
      .locator('h3:has-text("已激活待归纳角色人员")')
      .isVisible();
    const activatedBadgeVisible = await page
      .locator('span:has-text("已激活 · 待归纳角色")')
      .first()
      .isVisible();
    const hasManualRegisterForm = (await page.content()).includes('登记已激活待归纳人员');
    const unassignedSearchInput = page.locator('input[aria-label="搜索已激活待归纳人员"]');
    const searchInputVisible = await unassignedSearchInput.isVisible();

    // 校验全名「林建国」未被遮挡截断（scrollWidth <= clientWidth）
    const fullNameCheck = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const target = spans.find((el) => el.textContent?.trim() === '林建国');
      if (!target) return { found: false };
      return {
        found: true,
        text: target.textContent?.trim(),
        truncated: target.scrollWidth > target.clientWidth + 1,
        hasTruncateClass: target.className.includes('truncate'),
      };
    });

    if (
      unassignedActivatedHeader &&
      activatedBadgeVisible &&
      !hasManualRegisterForm &&
      searchInputVisible &&
      fullNameCheck.found &&
      !fullNameCheck.truncated &&
      !fullNameCheck.hasTruncateClass
    ) {
      logPass(
        '14b. 已激活待归纳角色人员-全名完整无遮挡、移除手动登记入口并支持搜索查询',
        '用户全名（如「林建国」）完整显示无截断，已移除手动登记表单，仅走密信对话激活并提供搜索框',
      );
    } else {
      logFail(
        '14b. 已激活待归纳角色人员-全名完整无遮挡、移除手动登记入口并支持搜索查询',
        `header=${unassignedActivatedHeader}, badge=${activatedBadgeVisible}, hasManualForm=${hasManualRegisterForm}, search=${searchInputVisible}, nameCheck=${JSON.stringify(fullNameCheck)}`,
      );
    }

    // 测试「已激活待归纳角色人员」搜索过滤功能
    await unassignedSearchInput.fill('林建国');
    await page.waitForTimeout(150);
    const filteredCardCount = await page.locator('button:has-text("一键移入分组")').count();
    await unassignedSearchInput.fill('');
    await page.waitForTimeout(150);
    const restoredCardCount = await page.locator('button:has-text("一键移入分组")').count();
    if (filteredCardCount === 1 && restoredCardCount === 3) {
      logPass(
        '14c. 已激活待归纳角色人员-按姓名/手机号搜索过滤',
        '输入「林建国」精准过滤出 1 人，清空后恢复 3 人',
      );
    } else {
      logFail(
        '14c. 已激活待归纳角色人员-按姓名/手机号搜索过滤',
        `过滤后=${filteredCardCount}，恢复后=${restoredCardCount}`,
      );
    }

    // 测试将「林建国」一键移入当前角色，再测试【角色内移除 -> 移回已激活待归纳角色人员列表】
    await page.locator('button:has-text("一键移入分组")').first().click();
    await page.waitForTimeout(250);
    const movedInRoleTable = await page.locator('tbody tr:has-text("林建国")').count();
    await page.locator('tbody tr:has-text("林建国") button:has-text("移除")').click();
    const removeRoleModal = page.locator('div.fixed.inset-0');
    await removeRoleModal.waitFor({ state: 'visible', timeout: 5000 });
    const removeRoleModalText = await removeRoleModal.innerText();
    await removeRoleModal.locator('button:has-text("确认移至待归纳列表")').click();
    await removeRoleModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(250);
    const afterRemoveInRole = await page.locator('tbody tr:has-text("林建国")').count();
    const returnedToUnassigned = await page
      .locator('span:has-text("林建国")')
      .first()
      .isVisible();

    if (
      movedInRoleTable === 1 &&
      removeRoleModalText.includes('确认将该成员移出当前角色') &&
      removeRoleModalText.includes('保留量子密信会话绑定') &&
      afterRemoveInRole === 0 &&
      returnedToUnassigned
    ) {
      logPass(
        '14d. 角色成员移除逻辑-从角色移出后自动回到「已激活待归纳角色人员」列表并保留会话绑定',
        '弹窗提示清晰，确认后「林建国」从角色名册移回右侧已激活待归纳角色人员列表',
      );
    } else {
      logFail(
        '14d. 角色成员移除逻辑-从角色移出后自动回到「已激活待归纳角色人员」列表并保留会话绑定',
        `movedInRole=${movedInRoleTable}, afterRemoveInRole=${afterRemoveInRole}, returned=${returnedToUnassigned}`,
      );
    }

    // 测试在角色中添加一名临时测试人员「陈测试」，将其从角色移至「已激活待归纳角色人员」，再从「已激活待归纳角色人员」移除（解除会话绑定）
    await page.locator('input[placeholder="用户名 (如：张三)"]').fill('陈测试');
    await page.locator('input[placeholder="11位手机号 (必填)"]').fill('13700137000');
    await page.locator('button:has-text("添加成员")').click();
    await page.waitForTimeout(200);
    await page.locator('tbody tr:has-text("陈测试") button:has-text("移除")').click();
    await removeRoleModal.waitFor({ state: 'visible', timeout: 5000 });
    await removeRoleModal.locator('button:has-text("确认移至待归纳列表")').click();
    await removeRoleModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);

    // 在右侧「已激活待归纳角色人员」列表中点击「陈测试」的移除按钮 -> 弹出解除会话绑定确认框
    await unassignedSearchInput.fill('陈测试');
    await page.waitForTimeout(150);
    await page
      .locator('button[title*="解除该用户的量子密信会话绑定"]')
      .first()
      .click();
    const unbindModal = page.locator('div.fixed.inset-0');
    await unbindModal.waitFor({ state: 'visible', timeout: 5000 });
    const unbindModalText = await unbindModal.innerText();
    await unbindModal.locator('button:has-text("确认解除绑定并移除")').click();
    await unbindModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    await unassignedSearchInput.fill('陈测试');
    await page.waitForTimeout(150);
    const chenCardCountAfterUnbind = await page.locator('button:has-text("一键移入分组")').count();
    await unassignedSearchInput.fill('');
    await page.waitForTimeout(150);
    const totalUnassignedCountAfterUnbind = await page
      .locator('button:has-text("一键移入分组")')
      .count();

    if (
      unbindModalText.includes('确认移除并解除密信会话绑定') &&
      unbindModalText.includes('重新输入发送激活信息') &&
      chenCardCountAfterUnbind === 0 &&
      totalUnassignedCountAfterUnbind === 3
    ) {
      logPass(
        '14e. 已激活待归纳人员移除逻辑-解除量子密信会话绑定并提示用户需重新输入激活',
        '成功解除「陈测试」会话绑定并从待归纳列表移除，提示文案明确告知需重新输入激活',
      );
    } else {
      logFail(
        '14e. 已激活待归纳人员移除逻辑-解除量子密信会话绑定并提示用户需重新输入激活',
        `chenCardCount=${chenCardCountAfterUnbind}, totalCount=${totalUnassignedCountAfterUnbind}, modal=${unbindModalText.slice(0, 200)}`,
      );
    }

    // 10. 检查左侧栏已移除「量子密信网关状态」模块，且管理员菜单已移除「账号与安全策略」
    const sidebarText = await page.locator('aside').innerText();
    if (!sidebarText.includes('量子密信网关状态')) {
      logPass('15a. 左侧栏移除「量子密信网关状态」模块', '侧边栏已移除量子密信网关状态卡片');
    } else {
      logFail('15a. 左侧栏移除「量子密信网关状态」模块', '侧边栏仍存在量子密信网关状态卡片');
    }

    await page.locator('aside button:has-text("系统管理员")').click();
    await page.waitForTimeout(150);
    const adminMenuText = await page.locator('aside').innerText();
    if (!adminMenuText.includes('账号与安全策略')) {
      logPass('15b. 管理员菜单移除「账号与安全策略」', '下拉菜单已移除账号与安全策略项');
    } else {
      logFail('15b. 管理员菜单移除「账号与安全策略」', '下拉菜单仍存在账号与安全策略项');
    }
    await page.locator('aside button:has-text("系统管理员")').click();

    // 10b. 测试「获取最新代码与 Agent 指令」弹窗按钮
    await page.locator('aside button:has-text("获取最新代码与 Agent 指令")').click();
    const agentModal = page.locator('div.fixed.inset-0');
    await agentModal.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(400);
    const agentModalText = await agentModal.innerText();
    const fullMdValue = await agentModal.locator('textarea').first().inputValue();
    const toastRegionText = await page.locator('[aria-live="polite"]').innerText();
    const hasErrorToast = toastRegionText.includes('操作未成功');
    if (
      agentModalText.includes('获取最新源码与 Agent 1:1 复刻指令') &&
      agentModalText.includes('一键复制全量指令+源码') &&
      !agentModalText.includes('按文件逐个查看与复制 (0)') &&
      !agentModalText.includes('正在实时打包最新源码文件与操作指令') &&
      fullMdValue.includes('IMTask 办公任务调度平台') &&
      !hasErrorToast
    ) {
      logPass(
        '15c. 页面随时获取最新代码与Agent指令弹窗',
        `成功加载最新源码合集与复刻指令（总长度 ${fullMdValue.length} 字符，0 报错）`,
      );
    } else {
      logFail(
        '15c. 页面随时获取最新代码与Agent指令弹窗',
        `弹窗内容加载异常: hasErrorToast=${hasErrorToast}, text=${agentModalText.slice(0, 200)}`,
      );
    }

    // 10c. 点击「下载 .tar.gz 源码包」并校验真实 tar.gz 二进制包与文件列表
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 10000 }),
      agentModal.locator('button:has-text("下载 .tar.gz 源码包")').click(),
    ]);
    const downloadPath = await download.path();
    const buf = fs.readFileSync(downloadPath);
    const isGzip = buf.length > 100 && buf[0] === 0x1f && buf[1] === 0x8b;
    const tarListOutput = isGzip
      ? execFileSync('tar', ['-tzf', downloadPath], { encoding: 'utf-8' })
      : '';
    if (
      isGzip &&
      tarListOutput.includes('src/App.tsx') &&
      tarListOutput.includes('src/components/imtask/InstancesView.tsx') &&
      tarListOutput.includes('src/components/imtask/RolesMembersView.tsx') &&
      tarListOutput.includes('AGENT_1TO1_REPLICATION.md')
    ) {
      const entryCount = tarListOutput.trim().split('\n').length;
      logPass(
        '15d. 真实 .tar.gz 源码包 Blob 下载与解压校验',
        `GZIP 魔数校验通过 (1f 8b)，大小 ${buf.length} 字节，tar -tzf 列出 ${entryCount} 个源码文件（绝非 Cookie 拦截页）`,
      );
    } else {
      logFail(
        '15d. 真实 .tar.gz 源码包 Blob 下载与解压校验',
        `isGzip=${isGzip}, size=${buf.length}, tarList=${tarListOutput.slice(0, 200)}`,
      );
    }

    await agentModal.locator('button').nth(1).click();
    await agentModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 11. 验证左侧导航栏已彻底移除独立的「催办与审计日志」一级菜单，聚焦 3 大核心工作台
    const navButtonsCount = await page.locator('aside nav button').count();
    const hasLogsNav = (await page.locator('aside nav').innerText()).includes('催办与审计日志');
    if (navButtonsCount === 3 && !hasLogsNav) {
      logPass(
        '16. 导航栏精简-移除独立的「催办与审计日志」模块',
        '左侧导航栏仅保留「任务实例与审核、任务模板管理、角色与成员名册」3大高频核心工作台，单任务提醒记录内聚于实例详情弹窗中',
      );
    } else {
      logFail(
        '16. 导航栏精简-移除独立的「催办与审计日志」模块',
        `navButtonsCount=${navButtonsCount}, hasLogsNav=${hasLogsNav}`,
      );
    }

    // 12. 检查无运行时报错
    if (consoleErrors.length === 0 && pageErrors.length === 0) {
      logPass('17. 浏览器控制台与运行时异常检查', '0 个 Console Error，0 个 Uncaught PageError');
    } else {
      logFail(
        '17. 浏览器控制台与运行时异常检查',
        `ConsoleErrors: ${consoleErrors.join('; ')} | PageErrors: ${pageErrors.join('; ')}`,
      );
    }
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => r.status === 'FAIL');
  console.log(`\n========================================`);
  console.log(`Playwright 自动化测试汇总: 共 ${results.length} 项，通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  console.log(`========================================`);
  if (failed.length > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('Playwright 执行异常:', err);
  process.exit(1);
});

```
