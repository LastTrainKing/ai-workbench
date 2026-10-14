/**
 * Real browser verification for the first-run wizard, and for data isolation.
 *
 * Two things are being proved here, and neither can be proved by a typecheck.
 *
 * **The wizard is reachable.** The register and login branches used to share one
 * `navigate(redirectTo)`, so a new account never saw `/welcome` at all. The only way to
 * know that is fixed is to register an account in a browser and look at the URL.
 *
 * **A new account cannot see anyone else's data.** The task calls this out as a defect
 * if it fails, and it is a defect that a screenshot cannot catch — an empty-looking page
 * is exactly what a broken filter and a correct `user_id` clause both produce. So this
 * asks the API directly, with the new account's own token, and asserts on the numbers.
 * The UI is checked too, but the API is the evidence.
 *
 * Uses the system Edge through playwright-core, the same harness as
 * `verify-agent-ui.mjs`: no browser download, and the same engine the Tauri shell embeds.
 *
 *   node scripts/verify-onboarding.mjs
 */

import { chromium } from './_playwright.mjs'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'onboarding')
const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

/** A fresh identity per run, so re-running never collides with the last attempt. */
function freshAccount(tag) {
  const stamp = Date.now().toString(36)
  return {
    email: `onb_${tag}_${stamp}@aiworkbench.dev`,
    username: `onb_${tag}_${stamp}`,
    displayName: `向导验证 ${tag}`,
    password: 'test1234',
  }
}

async function register(page, account) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.getByRole('button', { name: '注册' }).click()
  await page.waitForSelector('#email', { timeout: 15000 })
  await page.fill('#email', account.email)
  await page.fill('#username', account.username)
  await page.fill('#display_name', account.displayName)
  await page.fill('#password', account.password)
  await page.click('button[type="submit"]')
}

/** The token the browser is holding, so the API can be asked the same questions. */
async function tokenOf(page) {
  return page.evaluate(() => window.localStorage.getItem('aiw.token'))
}

async function apiGet(page, path, token) {
  const res = await page.request.get(`${BASE}/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok()) return { error: `HTTP ${res.status()}` }
  return res.json()
}

/** Pull the list out of whatever envelope the endpoint uses. */
function listOf(body) {
  if (!body || typeof body !== 'object') return null
  if (Array.isArray(body)) return body
  for (const key of ['data', 'items', 'results']) {
    const value = body[key]
    if (Array.isArray(value)) return value
    if (value && Array.isArray(value.items)) return value.items
  }
  return null
}

function countOf(body) {
  const list = listOf(body)
  if (list) return list.length
  const data = body?.data
  if (data && typeof data === 'object') {
    for (const key of ['total', 'count']) {
      if (typeof data[key] === 'number') return data[key]
    }
  }
  return null
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })

  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()

  const consoleErrors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

  try {
    // ================================================================ 3.1 ===
    console.log('\n=== 注册 → 自动进入向导 ===')
    const accountB = freshAccount('b')
    await register(page, accountB)
    await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 25000 })
    await page.waitForTimeout(800)
    const afterRegister = new URL(page.url()).pathname
    check('注册后自动跳到 /welcome', afterRegister === '/welcome', `实际 ${afterRegister}`)
    await page.screenshot({ path: join(SHOTS, '01-register-lands-on-welcome.png') })

    // ============================================================ 3.2 path B ===
    console.log('\n=== 路径 B：先随便看看 ===')
    const skipButton = page.getByRole('button', { name: /先随便看看/ })
    check('「先随便看看」按钮存在', (await skipButton.count()) > 0)
    await skipButton.click()
    await page.waitForURL((u) => u.pathname === '/', { timeout: 25000 })
    await page.waitForTimeout(1000)
    check('点「先随便看看」直接进系统，未填任何表', new URL(page.url()).pathname === '/')
    await page.screenshot({ path: join(SHOTS, '02-path-b-home.png') })

    // Reload: a marker must have been written, or the wizard returns every time.
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    check('刷新后不会重复弹向导', new URL(page.url()).pathname === '/', `实际 ${new URL(page.url()).pathname}`)

    // ============================================================ 3.3 restart ===
    console.log('\n=== 设置页：重新看一遍新手教程 ===')
    await page.goto(`${BASE}/settings`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1200)
    // Settings is tabbed and the profile tab is not the default, so the entry has to be
    // reached the way a user reaches it. Asserting on `/settings` alone would have
    // reported the button missing when it was merely on another tab.
    await page.getByRole('tab', { name: '账户' }).click()
    await page.waitForTimeout(600)
    const restart = page.getByRole('button', { name: /重新看一遍新手教程/ })
    check('设置页「账户」标签下有「重新看一遍新手教程」', (await restart.count()) > 0)
    await page.screenshot({ path: join(SHOTS, '03-settings-restart-entry.png') })
    if ((await restart.count()) > 0) {
      await restart.click()
      await page.waitForURL((u) => u.pathname === '/welcome', { timeout: 25000 })
      check('点它之后回到 /welcome', new URL(page.url()).pathname === '/welcome')
      await page.screenshot({ path: join(SHOTS, '04-restarted-wizard.png') })
    } else {
      // Keep going so the rest of the run still produces evidence, but do not pretend
      // the entry worked: the failure is already recorded above.
      await page.goto(`${BASE}/welcome`, { waitUntil: 'networkidle', timeout: 30000 })
    }

    // ============================================================ 3.2 path A ===
    console.log('\n=== 路径 A：从介绍自己开始 ===')
    const describe = page.getByRole('button', { name: /从介绍自己开始/ })
    check('「从介绍自己开始」按钮存在', (await describe.count()) > 0)
    await describe.click()
    const textarea = page.locator('textarea').first()
    await textarea.waitFor({ timeout: 15000 })
    const sentence = '我是计算机大三，成绩一般，在做毕业设计，最近想找实习，也想学点 AI'
    await textarea.fill(sentence)
    await page.screenshot({ path: join(SHOTS, '05-path-a-describe.png') })

    const extractButton = page.getByRole('button', { name: /看看它理解成了什么/ })
    await extractButton.click()

    // Wait for an outcome, not for a duration. A fixed sleep raced the model call: the
    // run before this one passed and this one failed on identical code, which is a test
    // measuring its own timing rather than the product. Whichever of the two panels
    // appears first is the answer, and the timeout is generous enough for a slow model.
    const confirmHeading = page.locator('text=它这样理解你')
    const errorNotice = page.locator('text=暂时无法解析')
    await Promise.race([
      confirmHeading.first().waitFor({ state: 'visible', timeout: 90000 }),
      errorNotice.first().waitFor({ state: 'visible', timeout: 90000 }),
    ]).catch(() => {})

    const extractionAppeared = (await confirmHeading.count()) > 0
    check('展示了它理解成了什么（确认步骤没被跳过）', extractionAppeared)
    if (!extractionAppeared) {
      // Say why, rather than leaving a bare FAIL. The screen shows the reason it could
      // not extract (no provider, timeout, bad response) and that text is the evidence.
      const notice = await page.locator('[role="alert"], .text-danger').first().innerText().catch(() => '')
      console.log(`        屏幕上的提示：${notice.slice(0, 300) || '(没有提示)'}`)
      const body = await page.locator('body').innerText()
      console.log(`        当前 URL：${new URL(page.url()).pathname}`)
      console.log(`        页面片段：${body.slice(0, 200).replace(/\s+/g, ' ')}`)
    }
    await page.screenshot({ path: join(SHOTS, '06-path-a-confirm.png') })

    if (extractionAppeared) {
      // Nothing may be written before this click — that is the whole point of the step.
      const tokenBefore = await tokenOf(page)
      const profileBefore = await apiGet(page, '/profile', tokenBefore)
      const identityBefore = profileBefore?.data?.identity ?? ''

      await page.getByRole('button', { name: /确认，开始使用/ }).click()
      await page.waitForURL((u) => u.pathname === '/', { timeout: 25000 })
      check('确认后进入系统', new URL(page.url()).pathname === '/')

      const profileAfter = await apiGet(page, '/profile', await tokenOf(page))
      const identityAfter = profileAfter?.data?.identity ?? ''
      // The profile digest is written by the model. With no key (round 14: the user
      // deleted theirs on purpose) it is legitimately empty, and asserting otherwise
      // would be asserting that a key exists. The property that still has to hold without
      // a model is that the confirm step **completes** — which the two checks above cover.
      const profileNeedsModel = (await page.request.get(`${BASE}/api/v1/health/deps`).then((r) => r.json()).catch(() => null))
        ?.data?.ai_degraded === true
      check(
        profileNeedsModel ? '确认后档案留空是预期的（本机没有模型）' : '确认后档案真的写入了',
        profileNeedsModel ? identityAfter === '' : Boolean(identityAfter) && identityAfter !== identityBefore,
        `identity="${identityAfter}"${profileNeedsModel ? ' · 没有模型' : ''}`,
      )
      check('确认前档案是空的（判断权在用户手里）', identityBefore === '', `确认前 identity="${identityBefore}"`)
      await page.screenshot({ path: join(SHOTS, '07-path-a-home.png') })
    }

    // ======================================================== 3.4 isolation ===
    console.log('\n=== 数据隔离：新账号看不到 demo 的数据 ===')
    // A fresh context, not just a navigation to /login: `PublicOnlyRoute` bounces a
    // signed-in visitor away from the auth screens, so registering a second account in
    // the same context is not something a user can do either. A new context is also a
    // stronger test — nothing is carried over, which is the point of the assertion.
    const isolationContext = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
    const iso = await isolationContext.newPage()
    const accountC = freshAccount('c')
    await register(iso, accountC)
    await iso.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 25000 })
    await iso.waitForTimeout(1000)
    const token = await tokenOf(iso)
    check('拿到新账号自己的 token', Boolean(token))

    // demo's numbers first, so "the new account is empty" is a comparison and not a
    // coincidence of an empty database.
    const demoLogin = await iso.request.post(`${BASE}/api/v1/auth/login`, {
      data: { identifier: 'demo', password: 'demo1234' },
    })
    let demoCounts = null
    if (demoLogin.ok()) {
      const demoToken = (await demoLogin.json())?.data?.access_token
      demoCounts = {}
      for (const [label, path] of [
        ['项目', '/projects'],
        ['知识库', '/knowledge-bases'],
        ['任务', '/tasks'],
        ['笔记', '/notes'],
      ]) {
        const body = await apiGet(iso, path, demoToken)
        demoCounts[label] = countOf(body)
      }
      console.log(`  demo 账号: ${JSON.stringify(demoCounts)}`)
    } else {
      console.log(`  (demo 登录失败 HTTP ${demoLogin.status()}，无法做对照)`)
    }

    const endpoints = [
      ['项目', '/projects'],
      ['知识库与文档', '/knowledge-bases'],
      ['任务', '/tasks'],
      ['笔记', '/notes'],
      ['BuildRun 与产出物', '/agent/runs'],
      ['记忆与候选', '/memories'],
      ['AI 运行记录', '/ai-runs'],
    ]

    let isolationPassed = 0
    for (const [label, path] of endpoints) {
      const body = await apiGet(iso, path, token)
      if (body.error) {
        check(`新账号 ${label} 为空`, false, body.error)
        continue
      }
      const count = countOf(body)
      const empty = count === 0
      if (empty) isolationPassed += 1
      const demoValue = demoCounts?.[label]
      const comparison = demoValue === undefined ? '' : `（demo 有 ${demoValue}）`
      check(`新账号 ${label} 为 0`, empty, `实际 ${count ?? '无法判定'}${comparison}`)
    }

    // The profile is a separate assertion: it exists, but carries nothing.
    const profile = await apiGet(iso, '/profile', token)
    const profileData = profile?.data ?? {}
    const profileEmpty =
      !profileData.identity && !profileData.stage && !profileData.domain &&
      (profileData.current_goals ?? []).length === 0
    check('新账号用户档案为空', profileEmpty, `identity="${profileData.identity ?? ''}"`)
    check('新账号 onboarded 为 false', profileData.onboarded === false, `onboarded=${profileData.onboarded}`)

    // The guard, checked directly. A not-yet-onboarded account that asks for /projects
    // must be sent to the wizard — that is the same rule that fixes the register
    // redirect, seen from the other side.
    await iso.goto(`${BASE}/projects`, { waitUntil: 'networkidle', timeout: 30000 })
    await iso.waitForTimeout(1500)
    check(
      '未走向导的账号访问 /projects 会被送回 /welcome',
      new URL(iso.url()).pathname === '/welcome',
      `实际 ${new URL(iso.url()).pathname}`,
    )
    await iso.screenshot({ path: join(SHOTS, '08-guard-bounces-to-welcome.png') })

    // Now let it in — 「先随便看看」 is the door for exactly this — and check the page a
    // brand-new account actually sees.
    await iso.getByRole('button', { name: /先随便看看/ }).click()
    await iso.waitForURL((u) => u.pathname === '/', { timeout: 25000 })
    await iso.goto(`${BASE}/projects`, { waitUntil: 'networkidle', timeout: 30000 })
    await iso.waitForTimeout(1800)
    const bodyText = await iso.locator('body').innerText()
    const looksEmpty = /还没有项目|没有匹配的项目|暂无/.test(bodyText)
    check('走完向导后，新账号的项目页是空的且有引导', looksEmpty, bodyText.slice(0, 120).replace(/\s+/g, ' '))
    check('项目页没有出现 demo 的数据', !/demo/i.test(bodyText))
    await iso.screenshot({ path: join(SHOTS, '09-new-account-projects-empty.png') })

    await iso.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 })
    await iso.waitForTimeout(1500)
    await iso.screenshot({ path: join(SHOTS, '10-new-account-home.png') })
    await isolationContext.close()

    check('浏览器控制台没有报错', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
    console.log(`\n  隔离断言通过 ${isolationPassed}/${endpoints.length}`)
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`)
  for (const f of failed) console.log(`  [FAIL] ${f.name} — ${f.detail}`)

  const out = join(HERE, '..', 'build', 'onboarding-verification.json')
  mkdirSync(dirname(out), { recursive: true })
  const { writeFileSync } = await import('node:fs')
  writeFileSync(out, JSON.stringify({ results, passed: results.length - failed.length, total: results.length }, null, 2))
  console.log(`  report: ${out}`)
  process.exit(failed.length ? 1 : 0)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
