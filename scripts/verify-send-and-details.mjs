/**
 * Sending a message, and what 详情 shows.
 *
 * The user's report, in two halves:
 *
 *     **我按了发送，我的话应该立刻出现，不该等服务器。**
 *     这 11.7 秒里界面完全没动，因为你把「用户消息」和「回复」放在同一批渲染里。
 *
 *     「详情」里那一大堆，用户不需要看到。
 *     memory=未命中(7ms) · skill=未命中(6ms) · knowledge=命中(44ms)
 *     约 69 tokens
 *
 * So there are two things to prove, and neither is provable by reading the diff:
 *
 *   1. **The user's own sentence is on screen before the server answers.** Measured as
 *      the time from the click to the bubble appearing, against a turn that takes
 *      seconds. Plus: something is visibly moving while it waits, a failure keeps the
 *      message on screen with a retry, and a reload shows the same order and content
 *      without the local copy doubling the server's.
 *   2. **The performance numbers are gone from 详情**, and the thing that replaced them
 *      — 「这次用了什么」 — is on the closed line rather than behind the click.
 *
 *   node scripts/verify-send-and-details.mjs
 */

import { chromium } from './_playwright.mjs'

import { requireModelOrSkip } from './_model_guard.mjs'

// 要真跑一次对话的脚本：没有模型就跳过（exit 3），不许假装通过。
await requireModelOrSkip('verify-send-and-details.mjs')

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'
const SHOTS = 'screenshots/ui-verification'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newContext({ viewport: { width: 1440, height: 950 } }).then((c) => c.newPage())

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

  /* ============================================ 1. my words appear immediately === */
  console.log('\n=== 乐观更新 ===')
  const conversation = await newConversation()
  check('能新建会话', Boolean(conversation), conversation ?? '(没有拿到 id)')
  await page.goto(`${BASE}/c/${conversation}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)

  // Chosen because it reliably produces `start` (measured in
  // `scripts/measure_turn_timings.py`), so the same conversation also has a run and
  // a 详情 fold for the second half of this script to check.
  const sentence = '写个 300 字雨后小故事，做成 Word 文档给我'
  await page.fill('[data-testid="goal-input"]', sentence)

  const clickedAt = Date.now()
  await page.click('[data-testid="start-build"]')
  let appeared = false
  try {
    await page.waitForSelector('[data-testid="message-pending"]', { timeout: 4000 })
    appeared = true
  } catch {
    appeared = false
  }
  const appearedIn = Date.now() - clickedAt
  check('按下发送后它立刻出现', appeared, `${appearedIn} ms`)
  // A server turn on this machine is 1.5–12 s. Anything under 500 ms cannot have waited
  // for it, which is the property being claimed.
  check('是「立刻」而不是「等服务器回来」', appeared && appearedIn < 500, `${appearedIn} ms`)

  const pendingText = await page.locator('[data-testid="message-pending"]').first().innerText()
  check('显示的就是我打的那句话', pendingText.includes('雨后小故事'), pendingText.slice(0, 40))
  await page.screenshot({ path: `${SHOTS}/95-sent-immediately.png`, fullPage: true })

  /* ------------------------------------------------- something is actually moving */
  const thinking = page.locator('[data-testid="thinking"]')
  const thinkingVisible = (await thinking.count()) > 0
  check('等待时有「正在想」的指示', thinkingVisible)
  const animating = await page.evaluate(() => {
    const dots = Array.from(document.querySelectorAll('[data-testid="thinking"] span'))
    return dots.some((dot) => dot.getAnimations().some((a) => a.playState === 'running'))
  })
  check('那个指示是持续动的，不是静止的', animating)
  await page.screenshot({ path: `${SHOTS}/96-thinking.png`, fullPage: true })

  /* ------------------------------------------------ the reply replaces the wait */
  await page.waitForSelector('[data-testid="message-pending"]', { state: 'detached', timeout: 90000 })
  await page.waitForTimeout(600)
  const afterReply = await page.locator('[data-testid="message-stream"]').innerText()
  const userBubbles = await page.locator('[data-testid="message-user"]').count()
  check('回复到了，占位被换掉（本地临时消息不再占位）', (await thinking.count()) === 0)
  check('我说的那句话只出现一次（本地副本没有和服务端重复）', userBubbles === 1, `${userBubbles} 条`)
  check('时间线上有它的回复', afterReply.length > sentence.length, `${afterReply.length} 字`)
  await page.screenshot({ path: `${SHOTS}/97-reply-arrived.png`, fullPage: true })

  /* ------------------------------------------------------ refresh: same order */
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const afterReload = await page.locator('[data-testid="message-stream"]').innerText()
  const reloadedUserBubbles = await page.locator('[data-testid="message-user"]').count()
  check('刷新后我的话还在，且仍然只有一次', reloadedUserBubbles === 1, `${reloadedUserBubbles} 条`)
  check('刷新后内容一致', afterReload.includes('雨后小故事'))
  check('刷新后没有残留的本地占位', (await page.locator('[data-testid="message-pending"]').count()) === 0)

  /* ============================================ 2. a failure is not silent ====== */
  console.log('\n=== 发失败时不许静默消失 ===')
  const failing = await newConversation()
  await page.goto(`${BASE}/c/${failing}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await page.route('**/api/v1/conversations/*/messages', (route) => route.abort('failed'))
  const doomed = '这句发不出去'
  await page.fill('[data-testid="goal-input"]', doomed)
  await page.click('[data-testid="start-build"]')
  let failedShown = false
  try {
    await page.waitForSelector('[data-testid="pending-failed"]', { timeout: 8000 })
    failedShown = true
  } catch {
    failedShown = false
  }
  check('接口失败时那条消息标成发送失败', failedShown)
  check('并且还在屏幕上（没有静默消失）', (await page.locator('[data-testid="message-pending"]').count()) > 0)
  check('给了重发的入口', (await page.locator('[data-testid="pending-retry"]').count()) > 0)
  await page.screenshot({ path: `${SHOTS}/98-send-failed.png`, fullPage: true })

  await page.unroute('**/api/v1/conversations/*/messages')
  await page.click('[data-testid="pending-retry"]')
  let recovered = false
  try {
    await page.waitForSelector('[data-testid="pending-failed"]', { state: 'detached', timeout: 90000 })
    recovered = true
  } catch {
    recovered = false
  }
  check('重发之后成功了', recovered)

  /* ============================================ 3. 详情 says what, not how fast = */
  console.log('\n=== 详情 ===')
  // Back to the conversation that produced a run. The failure test above left the
  // browser on a conversation whose last turn was an answer, and an answer has no run —
  // and therefore no fold. Checking there would be checking a screen that never has one.
  await page.goto(`${BASE}/c/${conversation}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)

  const runId = await page.evaluate(async () => {
    const token = localStorage.getItem('aiw.token')
    const response = await fetch('/api/v1/build-runs?limit=5', {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await response.json().catch(() => null)
    return body?.data?.build_runs?.[0]?.id ?? null
  })
  check('有可看的 run', Boolean(runId), runId ?? '(没有)')

  const fold = page.locator('[data-testid="run-details"]').first()
  if ((await fold.count()) > 0) {
    const summaryLine = (await fold.innerText()).replace(/\s+/g, ' ')
    check('关闭状态下那一行就说清了这次用了什么', /由「.+」回答|没用到你的东西/.test(summaryLine), summaryLine.slice(0, 80))
    check('关闭状态下没有毫秒数', !/\d+\s*ms/.test(summaryLine), summaryLine.slice(0, 80))
    check('关闭状态下没有 token 数', !/token/i.test(summaryLine))

    await fold.click()
    await page.waitForTimeout(800)
    const opened = await page.locator('[data-testid="message-stream"]').innerText()
    check('展开后也没有毫秒数', !/\d+\s*ms/.test(opened), (opened.match(/\d+\s*ms[^\n]*/) ?? ['(无)'])[0])
    check('展开后也没有 token 数', !/\d+\s*tokens?/i.test(opened), (opened.match(/[\d.]+\s*tokens?/i) ?? ['(无)'])[0])
    check('没有「还没有生成计划」这种空占位', !opened.includes('还没有生成计划'))
    await page.screenshot({ path: `${SHOTS}/99-details-simple.png`, fullPage: true })
  } else {
    check('详情折叠存在', false, '页面上没有 run-details')
  }

  /* --------------------------------------------------------------------- done -- */
  const failed = results.filter((r) => !r.ok)
  console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`)
  for (const item of failed) console.log(`  [FAIL] ${item.name}`)
  await browser.close()
  process.exit(failed.length ? 1 : 0)
} catch (error) {
  console.error('\n验证脚本本身出错：', error)
  await page.screenshot({ path: `${SHOTS}/error-send-and-details.png`, fullPage: true }).catch(() => {})
  await browser.close()
  process.exit(2)
}
