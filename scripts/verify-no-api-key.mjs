/**
 * 没有 API Key 时，应用仍然起得来、界面仍然可用。
 *
 * The user deleted their key on purpose:
 *
 *     用户把 API Key 删了 —— 这一轮禁止跑真实模型调用。
 *     **那不是故障，是他故意的。** 别去找、别去修、别建议他填回去。
 *     `llm.degraded` 现在为真是**预期状态**，不是 regression。
 *
 *     顺手加一条能重复跑的断言：没有 Key 时应用仍然起得来、界面仍然可用。
 *     这条以后一直留着，就是防"没 Key 就崩"。
 *
 * So this file exists to keep that promise checkable. It is not a one-off: run it before
 * every build, and run it whenever the configuration layer is touched.
 *
 * **It never sends a message.** Clicking 发送 would be a real model call, which this round
 * forbids; the point here is that the screen is *usable* without a key, and "the send
 * button is disabled with a reason" is the honest form of that. `verify-send-and-details`
 * covers sending, and is run only when a key exists.
 *
 *   node scripts/verify-no-api-key.mjs
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
const errors = []
page.on('pageerror', (error) => errors.push(String(error)))
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text())
})

try {
  /* ------------------------------------------------- 1. the API is up anyway --- */
  console.log('\n=== 没有 Key，接口照样活着 ===')
  const health = await page.request.get(`${BASE}/api/v1/health`)
  check('GET /health 返回 200', health.status() === 200, `status=${health.status()}`)

  const deps = await page.request.get(`${BASE}/api/v1/health/deps`)
  const depsBody = await deps.json().catch(() => null)
  const data = depsBody?.data ?? {}
  check('GET /health/deps 返回 200', deps.status() === 200, `status=${deps.status()}`)
  // This is the expected state this round, not a failure. Asserting on it is what makes
  // "the app works without a key" a test rather than an observation.
  check(
    '它明确说自己没有模型（ai_degraded=true）',
    data.ai_degraded === true,
    `ai_degraded=${data.ai_degraded}`,
  )
  check('数据库 / 缓存 / 向量库都还是好的', data.status === 'ok', `status=${data.status}`)

  /* --------------------------------------------------- 2. the UI still works --- */
  console.log('\n=== 界面仍然可用 ===')
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
  await page.waitForTimeout(1500)

  const notice = page.locator('[data-testid="model-setup-notice"]')
  check('登录之后进得去', page.url().includes('127.0.0.1'), page.url())
  check('屏幕上有「先给它一个模型」这句人话', (await notice.count()) > 0)
  if ((await notice.count()) > 0) {
    const text = (await notice.innerText()).replace(/\s+/g, ' ')
    check('说的是人话，不是错误码', /模型|API Key/.test(text), text.slice(0, 90))
    check('给了直达设置的入口', (await page.locator('[data-testid="goto-model-settings"]').count()) > 0)
  }

  // The composer must be disabled **with a reason**, not silently dead. A greyed button
  // with no explanation is the thing the user complained about elsewhere.
  const send = page.locator('[data-testid="start-build"]')
  check('发送按钮是灰的', await send.isDisabled())
  const hint = await page.locator('[data-testid="goal-input"]').getAttribute('placeholder')
  check('灰掉的同时说了为什么', Boolean(hint && /模型/.test(hint)), hint ?? '(没有说明)')

  // Typing must still work: the user can draft while they go and find a key.
  const input = page.locator('[data-testid="goal-input"]')
  check('输入框本身没被禁用（能打字）', await input.isEnabled())
  await input.fill('这句话应该能打进去')
  check('打得进去', (await input.inputValue()) === '这句话应该能打进去')
  await page.screenshot({ path: `${SHOTS}/A0-no-api-key.png`, fullPage: true })

  /* --------------------------------------- 3. settings is reachable, not a dead end --- */
  console.log('\n=== 点「去配置」能到设置 ===')
  await page.click('[data-testid="goto-model-settings"]')
  await page.waitForTimeout(1200)
  check('跳到了设置页', page.url().includes('/settings'), page.url())
  const settings = await page.locator('body').innerText()
  check('设置页打得开（不是白屏）', settings.trim().length > 50, `${settings.trim().length} 字`)

  check('全程没有 JS 报错', errors.length === 0, errors.slice(0, 2).join(' | ') || '(无)')

  const failed = results.filter((item) => !item.ok)
  console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`)
  for (const item of failed) console.log(`  [FAIL] ${item.name}`)
  await browser.close()
  process.exit(failed.length ? 1 : 0)
} catch (error) {
  console.error('\n验证脚本本身出错：', error)
  await page.screenshot({ path: `${SHOTS}/error-no-api-key.png`, fullPage: true }).catch(() => {})
  await browser.close()
  process.exit(2)
}
