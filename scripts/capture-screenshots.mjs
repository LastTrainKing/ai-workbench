/**
 * Capture real screenshots of the running application.
 *
 * Everything here talks to the live stack: the Vite dev server on :5173 and the
 * API on :8000. Nothing is mocked, and the data visible in the images comes from
 * the seeded sample documents in `samples/`.
 *
 * Uses the system Microsoft Edge via `channel: 'msedge'` so no browser download
 * is required (the dev machine cannot reach the Playwright CDN reliably).
 *
 *   node scripts/capture-screenshots.mjs
 */

import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'screenshots');

// playwright-core is a frontend devDependency; resolve it from there so this
// script can live under scripts/ without duplicating the dependency.
const require = createRequire(path.join(ROOT, 'frontend', 'package.json'));
const { chromium } = require('playwright-core');

const WEB = process.env.WEB_URL ?? 'http://127.0.0.1:5173';
const API = process.env.API_URL ?? 'http://127.0.0.1:8000/api/v1';
const EDGE =
  process.env.EDGE_PATH ??
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

const VIEWPORT = { width: 1600, height: 1000 };
const CREDENTIALS = { identifier: 'demo', password: 'demo1234' };

const log = (msg) => process.stdout.write(`  ${msg}\n`);

/** Minimal API client used to create the data the screenshots will show. */
async function api(pathname, { method = 'GET', body, token } = {}) {
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.success === false) {
    throw new Error(
      `${method} ${pathname} -> ${response.status} ${JSON.stringify(payload.error ?? payload)}`,
    );
  }
  return payload.data;
}

async function seed(token) {
  log('seeding demo content through the real API');

  const existing = await api('/knowledge-bases?page_size=50', { token });
  let kb = existing.items.find((item) => item.name === '示例知识库');

  if (!kb) {
    kb = await api('/knowledge-bases', {
      method: 'POST',
      token,
      body: {
        name: '示例知识库',
        description: 'Java 后端 / RAG 检索 / 软件工程实践的示例资料',
        icon: 'book',
        color: '#8b5cf6',
      },
    });
  }

  const projectList = await api('/projects?page_size=50', { token });
  let project = projectList.items.find((item) => item.name === '毕业设计');
  if (!project) {
    project = await api('/projects', {
      method: 'POST',
      token,
      body: {
        name: '毕业设计',
        description: 'AI Workbench — AI 原生个人知识与任务执行平台',
        icon: 'graduation-cap',
        color: '#6366f1',
      },
    });
  }

  // Upload the sample documents if this knowledge base is still empty.
  const docs = await api(`/documents?knowledge_base_id=${kb.id}&page_size=50`, { token });
  if (docs.meta.total === 0) {
    const { readFile } = await import('node:fs/promises');
    const files = [
      ['java-backend-notes.md', 'text/markdown'],
      ['rag-notes.md', 'text/markdown'],
      ['software-engineering-checklist.txt', 'text/plain'],
      ['distributed-systems.pdf', 'application/pdf'],
    ];
    for (const [name, type] of files) {
      const buffer = await readFile(path.join(ROOT, 'samples', name));
      const form = new FormData();
      form.append('file', new Blob([buffer], { type }), name);
      form.append('knowledge_base_id', kb.id);
      const response = await fetch(`${API}/documents/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        throw new Error(`upload ${name} failed: ${JSON.stringify(payload.error ?? payload)}`);
      }
      log(`uploaded ${name}${payload.data.duplicate ? ' (already present)' : ''}`);
    }

    // Wait for the ingestion pipeline to finish.
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const list = await api(`/documents?knowledge_base_id=${kb.id}&page_size=50`, { token });
      const states = list.items.map((item) => item.status);
      if (states.length && states.every((s) => s === 'COMPLETED' || s === 'FAILED')) break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  // Make sure there is a task tree and a note to show.
  const tasks = await api(`/tasks?project_id=${project.id}&page_size=200`, { token });
  if (tasks.meta.total === 0) {
    await api('/planner/plan', {
      method: 'POST',
      token,
      body: {
        goal: '在一个月内掌握 Java 后端基础：JVM、集合与并发',
        knowledge_base_ids: [kb.id],
        project_id: project.id,
        horizon_days: 30,
        create_tasks: true,
      },
    });
  }

  const notes = await api('/notes?page_size=50', { token });
  if (notes.meta.total === 0) {
    await api('/notes', {
      method: 'POST',
      token,
      body: {
        title: 'JVM 垃圾回收速查',
        project_id: project.id,
        knowledge_base_id: kb.id,
        content_md: [
          '# JVM 垃圾回收速查',
          '',
          '## 收集器对比',
          '',
          '| 收集器 | 目标 | 特点 |',
          '| --- | --- | --- |',
          '| Serial | 客户端 | 单线程，简单 |',
          '| Parallel Scavenge | 吞吐量 | 可控吞吐量 |',
          '| CMS | 低停顿 | 标记-清除，有碎片 |',
          '| G1 | 可预测停顿 | Region 划分，JDK 9+ 默认 |',
          '| ZGC | 极低停顿 | 停顿与堆大小基本无关 |',
          '',
          '## 待办',
          '',
          '- [ ] 整理 -Xms / -Xmx / -XX:MetaspaceSize 的调优经验',
          '- [ ] 复现一次 OOM 并写出排查流程',
          '',
          '> 本笔记由 AI Workbench 的文档分析功能生成后手工整理。',
        ].join('\n'),
      },
    });
  }

  // Produce at least one agent trajectory and a few AI runs to display.
  const runs = await api('/ai-runs?page_size=50', { token });
  const hasAgent = runs.items.some((run) => run.run_type === 'AGENT');
  if (!hasAgent) {
    log('running the agent once to populate the trajectory view');
    await api('/agents/run', {
      method: 'POST',
      token,
      body: {
        goal: '根据知识库里的 RAG 资料，制定一个 7 天学习计划并创建对应任务',
        project_id: project.id,
        knowledge_base_ids: [kb.id],
        max_steps: 8,
      },
    });
  }

  await api('/chat/rag', {
    method: 'POST',
    token,
    body: {
      question: 'JVM 有哪些垃圾收集器？分别适合什么场景？',
      knowledge_base_ids: [kb.id],
      mode: 'hybrid',
    },
  });

  return { kb, project };
}

async function capture() {
  await mkdir(OUT, { recursive: true });

  const token = (await api('/auth/login', { method: 'POST', body: CREDENTIALS })).access_token;
  const { kb, project } = await seed(token);

  // `SEED_ONLY=1` makes this script usable as a demo-data seeder without
  // launching a browser — handy after a fresh database reset.
  if (process.env.SEED_ONLY === '1') {
    log('SEED_ONLY=1 — demo data seeded, skipping screenshots');
    return;
  }

  log(`launching Edge (${path.basename(EDGE)})`);
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    locale: 'zh-CN',
    colorScheme: 'dark',
  });

  const page = await context.newPage();
  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  page.on('pageerror', (err) => errors.push(String(err)));

  const shot = async (name, url, { wait = 1600, full = false } = {}) => {
    await page.goto(`${WEB}${url}`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    await page.waitForTimeout(wait);
    const file = path.join(OUT, `${name}.png`);
    await page.screenshot({ path: file, fullPage: full });
    const { size } = await import('node:fs/promises').then((fs) => fs.stat(file));
    log(`${name}.png  ${(size / 1024).toFixed(0)} KB  <- ${url}`);
  };

  /** Fill the first field matching a placeholder and click the button with the given text. */
  const act = async (placeholder, text, buttonText, { settle = 12_000 } = {}) => {
    const field = page.getByPlaceholder(placeholder).first();
    await field.waitFor({ state: 'visible', timeout: 15_000 });
    await field.fill(text);
    const button = buttonText
      ? page.getByRole('button', { name: buttonText }).first()
      : page.locator('form button[type="submit"]').first();
    await button.click({ timeout: 15_000 });
    await page.waitForTimeout(settle);
  };

  // ---------------------------------------------------------------- login ----
  await page.goto(`${WEB}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, '00-login.png') });
  log('00-login.png  <- /login');

  // Authenticate through the real form (this also verifies the login flow),
  // then persist the token the same way the app does so protected routes render.
  const inputs = await page.locator('form input').all();
  if (inputs.length >= 2) {
    await inputs[0].fill(CREDENTIALS.identifier);
    await inputs[1].fill(CREDENTIALS.password);
    await page.locator('form button[type="submit"]').click();
    await page.waitForTimeout(2500);
  }
  const loggedIn = !page.url().includes('/login');
  log(loggedIn ? 'form login succeeded' : 'form login did not navigate');

  if (!loggedIn) {
    log('falling back to direct session injection');
    // Mirrors `frontend/src/store/auth.ts` (TOKEN_KEY / USER_KEY).
    const user = (await api('/auth/me', { token })) ?? null;
    await page.evaluate(
      ([t, u]) => {
        localStorage.setItem('aiw.token', t);
        localStorage.setItem('aiw.user', JSON.stringify(u));
      },
      [token, user],
    );
    await page.goto(`${WEB}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
  }

  await shot('01-dashboard', '/', { wait: 2600 });
  await shot('02-knowledge', '/knowledge', { wait: 2000 });
  await shot('03-documents', `/knowledge/${kb.id}/documents`, { wait: 3000 });

  // --- RAG chat: ask a real question so the answer and its citations show ----
  await page.goto(`${WEB}/chat`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1800);
  try {
    await act('问点什么', 'JVM 有哪些垃圾收集器？分别适合什么场景？', '发送', { settle: 14_000 });
  } catch (error) {
    log(`chat interaction skipped: ${error.message.split('\n')[0]}`);
  }
  // Scroll so the generated answer sits at the top of the frame, with its
  // citation cards visible underneath.
  await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2);
  await page.mouse.wheel(0, -900);
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, '04-rag-chat.png') });
  log('04-rag-chat.png  <- /chat (with answer + citations)');

  // --- Planner: generate a real plan ----------------------------------------
  await page.goto(`${WEB}/planner`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  try {
    await act('两周内完成毕业设计', '在一个月内掌握 Java 后端基础：JVM、集合与并发', '生成', {
      settle: 16_000,
    });
  } catch (error) {
    log(`planner interaction skipped: ${error.message.split('\n')[0]}`);
  }
  await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2);
  await page.mouse.wheel(0, 480);
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, '05-task-planner.png') });
  log('05-task-planner.png  <- /planner (with generated plan)');

  // --- Agent: run it and show the trajectory --------------------------------
  await page.goto(`${WEB}/agent`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  try {
    await act(
      '阅读分布式系统资料',
      '根据知识库里的 RAG 资料，整理出 3 个复习任务并写一条总结笔记',
      '执行',
      { settle: 20_000 },
    );
  } catch (error) {
    log(`agent interaction skipped: ${error.message.split('\n')[0]}`);
  }
  await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2);
  await page.mouse.wheel(0, 620);
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, '06-agent.png') });
  log('06-agent.png  <- /agent (with tool trajectory)');

  await shot('07-ai-runs', '/ai-runs', { wait: 2400 });

  // --- AI run detail: the full execution trace ------------------------------
  await page.goto(`${WEB}/ai-runs`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  try {
    await page.locator('a[href^="/ai-runs/run_"]').first().click({ timeout: 15_000 });
    await page.waitForTimeout(2600);
  } catch (error) {
    log(`ai-run detail navigation skipped: ${error.message.split('\n')[0]}`);
  }
  await page.screenshot({ path: path.join(OUT, '07b-ai-run-trace.png') });
  log('07b-ai-run-trace.png  <- /ai-runs/{run_id} (prompt version, sources, tools, tokens)');

  // --- Tasks: switch to the tree view ---------------------------------------
  await page.goto(`${WEB}/tasks`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  try {
    // The view switcher is a segmented control; its items carry an explicit
    // `role="radio"`, so a CSS locator is used instead of getByRole('button').
    await page.locator('[role="radiogroup"] button').nth(1).click({ force: true, timeout: 20_000 });
    await page.waitForTimeout(2800);
  } catch (error) {
    log(`task tree toggle skipped: ${error.message.split('\n')[0]}`);
  }
  await page.screenshot({ path: path.join(OUT, '08-tasks.png') });
  log('08-tasks.png  <- /tasks (tree view)');

  await shot('09-graph', '/graph', { wait: 3600 });

  // --- Search: run a hybrid query -------------------------------------------
  await page.goto(`${WEB}/search`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  try {
    await act('输入检索问题', '垃圾回收 收集器 G1', null, { settle: 8000 });
  } catch (error) {
    log(`search interaction skipped: ${error.message.split('\n')[0]}`);
  }
  await page.screenshot({ path: path.join(OUT, '11-search.png') });
  log('11-search.png  <- /search (hybrid results with score breakdown)');

  // --- AI Provider settings: the model actually answering -----------------
  await shot('10-settings', '/settings', { wait: 3000 });
  await shot('15-ai-provider', '/settings', { wait: 3000 });
  await shot('12-projects', '/projects', { wait: 1800 });
  await shot('13-notes', '/notes', { wait: 1800 });

  await browser.close();

  const report = {
    capturedAt: new Date().toISOString(),
    webUrl: WEB,
    apiUrl: API,
    viewport: VIEWPORT,
    project: project.name,
    knowledgeBase: kb.name,
    consoleErrors: errors,
  };
  await writeFile(
    path.join(OUT, 'capture-report.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );

  if (errors.length) {
    log(`\n  ${errors.length} console error(s) captured — see capture-report.json`);
  } else {
    log('\n  no console errors');
  }
}

capture().catch((error) => {
  console.error('\n[x] screenshot capture failed:', error.message);
  process.exit(1);
});
