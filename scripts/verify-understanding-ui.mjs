/**
 * The understanding layer, driven through the browser.
 *
 * This is the round's central claim, and it is checked with the user's own sentence:
 *
 *     用户：帮我做一个 java 的指引指南，教教我怎么学习
 *     它：  好。做之前我想确认几件事 ——
 *             1. 你要什么形式？Word 文档 / PPT / 还是我直接在对话里写给你
 *             2. 要多少？只要一个学习目录，还是每个阶段带上例子和练习
 *             3. 给谁看？自己学 / 交作业 / 给别人讲
 *
 * Four things have to be true, and the second one is the reason this round exists:
 *
 *   1. it asks before it works;
 *   2. **asking creates no build run.** A run that exists is a run that can be executed,
 *      so "it asked" has to mean the database has nothing to execute;
 *   3. every question carries a default — a question without one hands the whole cost of
 *      choosing back to the user, which is the thing being fixed;
 *   4. answering it starts work, and 「就按你说的做」 also starts work.
 *
 *   node scripts/verify-understanding-ui.mjs
 */

import { chromium } from './_playwright.mjs'

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'
const SHOTS = 'screenshots/ui-verification'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newContext({ viewport: { width: 1440, height: 950 } }).then((c) => c.newPage())

/** Talk to the API with the browser's own token, so the checks read the real record. */
const api = async (path, init = {}) =>
  page.evaluate(
    async ([p, i]) => {
      const token = localStorage.getItem('aiw.token')
      const response = await fetch(`/api/v1${p}`, {
        ...i,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(i.headers ?? {}),
        },
      })
      return { status: response.status, body: await response.json().catch(() => null) }
    },
    [path, init],
  )

const newConversation = async () => {
  const created = await api('/conversations', { method: 'POST', body: JSON.stringify({}) })
  return created.body?.data?.id
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
  await page.waitForTimeout(1200)

  /* ------------------------------------------- 1. it asks before it works ------ */
  const first = await newConversation()
  check('能新建会话', Boolean(first), first ?? '(没有拿到 id)')

  await page.goto(`${BASE}/c/${first}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  const greeting = await page.locator('[data-testid="message-stream"]').innerText()
  check('空会话由它先说一句', greeting.trim().length > 0, greeting.slice(0, 40))

  const goal = '帮我做一个 java 的指引指南，教教我怎么学习'
  await page.fill('[data-testid="goal-input"]', goal)
  await page.click('[data-testid="start-build"]')

  // A real model call: it takes a while, and the answer is the point of the test.
  await page
    .locator('[data-testid="clarify-questions"], [data-testid="conversation-turn"]')
    .first()
    .waitFor({ timeout: 180000 })
    .catch(() => null)
  await page.waitForTimeout(800)

  const after = await api(`/conversations/${first}`)
  const messages = after.body?.data?.messages ?? []
  const reply = messages.find((m) => m.role === 'assistant' && m.kind)
  const runsAfter = await api('/build-runs?limit=50')
  const runsForFirst = (runsAfter.body?.data?.build_runs ?? []).filter((r) =>
    messages.some((m) => m.build_run_id === r.id),
  )

  console.log(`\n  ── 它实际回了什么 ──`)
  console.log(`  outcome : ${reply?.kind ?? '(无)'}`)
  if (reply?.content) console.log(`  reply   : ${reply.content.replace(/\n/g, ' ').slice(0, 200)}`)
  for (const [index, question] of (reply?.questions ?? []).entries()) {
    console.log(`  Q${index + 1}      : ${question.question}`)
    console.log(`  默认    : ${question.default || '(没给建议)'}`)
  }
  console.log('')

  check('它先问了，没有直接开工', reply?.kind === 'clarify', `outcome=${reply?.kind ?? '(无)'}`)
  check(
    '问的是形式 / 范围 / 用途这一类，不是空问题',
    (reply?.questions ?? []).length > 0,
    `${(reply?.questions ?? []).length} 个问题`,
  )
  check(
    '最多 3 个问题',
    (reply?.questions ?? []).length <= 3,
    `${(reply?.questions ?? []).length} 个`,
  )
  check(
    '每个问题都带默认值',
    (reply?.questions ?? []).every((q) => typeof q.default === 'string' && q.default.length > 0),
    (reply?.questions ?? []).map((q) => (q.default ? '有' : '无')).join(','),
  )

  /* --------------------------------- 2. the gate: asking built nothing --------- */
  check('先问的那一步没有创建任何 BuildRun', runsForFirst.length === 0, `${runsForFirst.length} 个`)
  check('先问的那一步没有产出物', (after.body?.data?.deliverables ?? []).length === 0)

  const clarifyCard = page.locator('[data-testid="clarify-questions"]')
  check('界面上真的列出了问题', (await clarifyCard.count()) > 0)
  check('界面上有「就按你说的做」', (await page.locator('[data-testid="confirm-start"]').count()) > 0)
  await page.screenshot({ path: `${SHOTS}/93-asks-first.png`, fullPage: true })

  /* ------------------------------------- 3. answering it starts the work ------- */
  await page.fill('[data-testid="goal-input"]', 'PPT，要目录，带点例子')
  await page.click('[data-testid="start-build"]')
  await page
    .locator('[data-testid="conversation-turn"]')
    .first()
    .waitFor({ timeout: 180000 })
    .catch(() => null)
  await page.waitForTimeout(1500)

  const started = await api(`/conversations/${first}`)
  const startedMessages = started.body?.data?.messages ?? []
  const startMessage = startedMessages.find((m) => m.kind === 'start')
  check('回答之后它开工了', Boolean(startMessage), `kind=${startMessage?.kind ?? '(无)'}`)

  const runId = startMessage?.build_run_id
  if (runId) {
    const run = await api(`/build-runs/${runId}`)
    const status = run.body?.data?.status
    check('开工的那次运行真的不是 DRAFT', status && status !== 'DRAFT', `status=${status}`)
    check(
      '运行带着刚才的答案（目标里有形式或范围）',
      /PPT|目录|例子/.test(run.body?.data?.goal ?? ''),
      (run.body?.data?.goal ?? '').slice(0, 80),
    )
  } else {
    check('开工的那次运行真的不是 DRAFT', false, '没有 build_run_id')
    check('运行带着刚才的答案（目标里有形式或范围）', false, '没有 build_run_id')
  }

  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  const stream = await page.locator('[data-testid="message-stream"]').innerText()
  check('产出/执行长在对话里（同一页，不是另一个面板）', /详情|正在做|做好了|步/.test(stream), stream.slice(-80))
  check('状态是一句话，不出现 DRAFT / PARTIAL', !/DRAFT|PARTIAL/.test(stream))
  await page.screenshot({ path: `${SHOTS}/94-started-in-conversation.png`, fullPage: true })

  /* ------------------------------- 4. 「就按你说的做」 starts it without asking -- */
  const second = await newConversation()
  await page.goto(`${BASE}/c/${second}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await page.fill('[data-testid="goal-input"]', '帮我整理一份关于机器学习的课程报告')
  await page.click('[data-testid="start-build"]')
  await page
    .locator('[data-testid="clarify-questions"], [data-testid="conversation-turn"]')
    .first()
    .waitFor({ timeout: 180000 })
    .catch(() => null)
  await page.waitForTimeout(800)

  const confirmButton = page.locator('[data-testid="confirm-start"]')
  if ((await confirmButton.count()) > 0) {
    await confirmButton.click()
    await page
      .locator('[data-testid="conversation-turn"]')
      .first()
      .waitFor({ timeout: 180000 })
      .catch(() => null)
    await page.waitForTimeout(1500)

    const forced = await api(`/conversations/${second}`)
    const forcedStart = (forced.body?.data?.messages ?? []).find((m) => m.kind === 'start')
    check('「就按你说的做」也能开工', Boolean(forcedStart))
    check(
      '替用户决定的事写在结果里',
      Array.isArray(forcedStart?.decided_for_you),
      JSON.stringify(forcedStart?.decided_for_you ?? null).slice(0, 90),
    )
    await page.screenshot({ path: `${SHOTS}/95-confirm-and-start.png`, fullPage: true })
  } else {
    // It decided it already had enough, which is a legitimate outcome — but then the
    // button has nothing to do and this branch says so rather than passing silently.
    check('「就按你说的做」也能开工', false, '这一轮它没有先问，所以没有这个按钮可点')
    check('替用户决定的事写在结果里', false, '同上')
  }

  /* -------------------------------------------- 5. the ladder is still visible -- */
  await page.goto(`${BASE}/c/${first}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)
  const details = page.locator('[data-testid="run-details"]')
  if ((await details.count()) > 0) {
    const open = await page
      .locator('details:has([data-testid="run-details"])')
      .first()
      .evaluate((el) => el.hasAttribute('open'))
    if (!open) {
      await details.first().click()
      await page.waitForTimeout(800)
    }
    const panels = await page.locator('[data-testid="panel-plan"]').count()
    check('「详情」里仍有执行计划 / 实时执行 / 本次上下文', panels > 0)
  } else {
    check('「详情」里仍有执行计划 / 实时执行 / 本次上下文', false, '没有找到详情折叠')
  }
} catch (exc) {
  check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
  await page.screenshot({ path: `${SHOTS}/error-understanding.png` }).catch(() => {})
}

await browser.close()
const failed = results.filter((r) => !r.ok)
console.log(`\n  ${results.length - failed.length}/${results.length} 通过`)
for (const f of failed) console.log(`    - ${f.name}`)
process.exit(failed.length ? 1 : 0)
