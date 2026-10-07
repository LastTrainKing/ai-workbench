/**
 * Browser test for the workbench — one run, in detail.
 *
 * The two claims worth checking, because both can be quietly false:
 *
 *   1. the three panels are *not* on the first screen (otherwise nothing changed)
 *   2. they *are* still reachable and still populated (otherwise a feature was
 *      deleted to make the page look simpler, which the brief explicitly forbade)
 *
 * The conversation screen's own version of this lives in
 * `verify-conversation-ui.mjs`. This one is about the deep view: it does **not**
 * create a run, because creating one costs a real model call and an existing
 * settled run tests the same thing.
 *
 *   node scripts/verify-workbench-layout.mjs
 */

import { chromium } from './_playwright.mjs'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'ui-verification')
const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newContext({ viewport: { width: 1440, height: 900 } }).then((c) => c.newPage())
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))

    // Pick a settled run from the API rather than creating one.
    const settled = await page.evaluate(async (base) => {
      const token = localStorage.getItem('aiw.token')
      const res = await fetch(`${base}/api/v1/build-runs?limit=40`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const body = await res.json().catch(() => null)
      const runs = body?.data?.build_runs ?? []
      const terminal = ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'LIMIT_REACHED']
      return runs.find((r) => terminal.includes(r.status))?.id ?? null
    }, BASE)

    check('有一个已结束的任务可以检查', Boolean(settled), settled ?? '(无)')
    if (!settled) throw new Error('没有已结束的任务')

    await page.goto(`${BASE}/workbench/${settled}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)

    const fold = page.locator('details:has([data-testid="run-details"])')
    check('工作台有「详情」折叠区', (await fold.count()) > 0)

    const openNow = await fold.evaluate((el) => el.hasAttribute('open')).catch(() => null)
    check('已结束的任务：详情默认收起', openNow === false, `open=${openNow}`)

    // `innerText` on a collapsed <details> still returns the text — the content is
    // in the DOM, just not laid out. So assert on what Playwright says is visible.
    const outputVisible = await page.locator('text=产出物').first().isVisible().catch(() => false)
    const planVisible = await page.locator('text=执行计划').first().isVisible().catch(() => false)
    check('首屏先给产出（可见）', outputVisible)
    check('过程不占首屏（执行计划不可见）', planVisible === false)

    await page.screenshot({ path: join(SHOTS, '73-workbench-settled.png'), fullPage: true })

    // Claim 2: everything is still reachable.
    await page.locator('[data-testid="run-details"]').first().click()
    await page.waitForTimeout(900)
    check('展开后：执行计划仍在', await page.locator('text=执行计划').first().isVisible().catch(() => false))
    check('展开后：实时执行仍在', await page.locator('text=实时执行').first().isVisible().catch(() => false))
    check('展开后：本次上下文仍在', await page.locator('text=本次上下文').first().isVisible().catch(() => false))
    await page.screenshot({ path: join(SHOTS, '74-workbench-details.png'), fullPage: true })

    // A running run opens the fold by design — that is the moment somebody is
    // watching, and a fold that hid live progress would be hiding the point.
    const active = await page.evaluate(async (base) => {
      const token = localStorage.getItem('aiw.token')
      const res = await fetch(`${base}/api/v1/build-runs?limit=40`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const body = await res.json().catch(() => null)
      const runs = body?.data?.build_runs ?? []
      return runs.find((r) => r.status === 'EXECUTING')?.id ?? null
    }, BASE)
    if (active) {
      await page.goto(`${BASE}/workbench/${active}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      const runningOpen = await page
        .locator('details:has([data-testid="run-details"])')
        .evaluate((el) => el.hasAttribute('open'))
      check('运行中的任务：详情按设计默认展开', runningOpen === true)
    } else {
      console.log('  (当前没有正在运行的任务 —— 跳过「运行中默认展开」检查)')
    }

    check('无 JS 运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error-workbench-layout.png') }).catch(() => {})
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(72))
  console.log(`  ${results.length - failed.length}/${results.length} 项通过`)
  if (failed.length) failed.forEach((f) => console.log(`    - ${f.name}${f.detail ? ` (${f.detail})` : ''}`))
  console.log('='.repeat(72))
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
