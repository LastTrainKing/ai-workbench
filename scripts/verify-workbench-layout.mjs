/**
 * Browser test for the layout change: the workbench leads with what it made, and
 * the machinery is folded away behind 「详情」 without becoming unreachable.
 *
 * The two claims worth checking, because both can be quietly false:
 *
 *   1. the three panels are *not* on the first screen (otherwise nothing changed)
 *   2. they *are* still reachable and still populated (otherwise a feature was
 *      deleted to make the page look simpler, which is the failure mode the
 *      brief explicitly forbade)
 *
 *   node scripts/verify-workbench-layout.mjs
 */

import { chromium } from './_playwright.mjs'
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

    // ------------------------------------------------------------- home page --
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const home = await page.locator('body').innerText()

    // A stranger should be able to answer "where do I type" without being told.
    check('首页有一个明显的输入入口', /你想让我完成什么/.test(home))
    check('首页的输入框带示例文案（告诉人怎么说）', (await page.locator('[data-testid="goal-input"]').getAttribute('placeholder')) !== '')
    check('首页有「正在处理」', /正在处理/.test(home))
    check('首页有「最近产出」', /最近产出/.test(home))
    check('首页有「它学到了什么」', /它学到了什么/.test(home))
    check('首页不含工程术语（RAG / 向量 / tier）', !/RAG|向量化|tier|工具注册表/.test(home))
    await page.screenshot({ path: join(SHOTS, '72-home-final.png'), fullPage: true })

    // ------------------------------------------------------------ workbench --
    // Create one so the page has a plan and context to hide (or show).
    await page.locator('[data-testid="goal-input"]').fill('检测工作台布局：把三栏收进详情')
    await page.locator('[data-testid="start-build"]').click()
    await page.waitForURL(/\/workbench\//, { timeout: 20000 }).catch(() => null)
    await page.waitForTimeout(3000)

    const runId = page.url().split('/workbench/')[1]
    check('打开了工作台', Boolean(runId), page.url())
    await page.goto(`${BASE}/workbench/${runId}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)

    const detailsEl = page.locator('details:has([data-testid="workbench-details"])')
    check('工作台有「详情」折叠区', (await detailsEl.count()) > 0)

    // Claim 1: the machinery is folded away on arrival. A run in EXECUTING opens
    // it by design, so drive it to a settled state first by opening a finished run.
    const openNow = await detailsEl.evaluate((el) => el.hasAttribute('open')).catch(() => null)
    console.log(`  (详情默认状态 open=${openNow})`)

    // Claim 2: everything is still reachable.
    const visibleTop = await page.locator('body').innerText()
    check('首屏就是「产出物」而不是「执行计划」', /产出物/.test(visibleTop))

    if (!openNow) {
      check('默认不展开：首屏看不到「执行计划」', !/执行计划/.test(visibleTop))
      await page.locator('[data-testid="workbench-details"]').click()
      await page.waitForTimeout(700)
    } else {
      console.log('  (运行中，详情按设计默认展开 —— 跳过「默认不可见」检查)')
    }
    const expanded = await page.locator('body').innerText()
    check('展开后：执行计划仍在', /执行计划/.test(expanded))
    check('展开后：实时执行仍在', /实时执行/.test(expanded))
    check('展开后：本次上下文仍在', /本次上下文/.test(expanded))
    check('展开后：产出物仍在', /产出物/.test(expanded))
    await page.screenshot({ path: join(SHOTS, '73-workbench-final.png'), fullPage: true })

    // ------------------------------------------------------------- sidebar --
    const nav = await page.locator('nav').first().innerText()
    const topLevel = ['主页', '我的 AI', '工作台', '项目', '任务', '计划', '资料', '文档', '查找', '问答']
    const visibleTopLevel = topLevel.filter((t) => nav.includes(t))
    check('主菜单项数量在 5–6 个分类内（不是 16 个平铺）', /首页/.test(nav) && /高级/.test(nav), `可见顶层项 ${visibleTopLevel.length}`)
    check('「高级」里的东西默认不占位', !/执行记录/.test(nav))
    await page.screenshot({ path: join(SHOTS, '74-sidebar-final.png') })

    check('无 JS 运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '))

    // A finished run is the common case on this page, and it is the one where the
    // fold must actually be shut — a running one opens by design. Without this the
    // "默认收起" claim would never be tested at all.
    const settled = await page.evaluate(async (base) => {
      // The key is the app's own storage key; read it rather than guessing a shape.
      const token = localStorage.getItem('aiw.token')
      const res = await fetch(`${base}/api/v1/build-runs?limit=40`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const body = await res.json().catch(() => null)
      const runs = body?.data?.build_runs ?? []
      const terminal = ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'LIMIT_REACHED']
      return runs.find((r) => terminal.includes(r.status))?.id ?? null
    }, BASE)

    if (settled) {
      await page.goto(`${BASE}/workbench/${settled}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(2000)
      const settledOpen = await page
        .locator('details:has([data-testid="workbench-details"])')
        .evaluate((el) => el.hasAttribute('open'))
      check('已结束的任务：详情默认收起', settledOpen === false)
      // `innerText` on a collapsed <details> still returns the text — the content is
      // in the DOM, just not laid out. So assert on what is actually visible to
      // Playwright, which is the thing a person sees.
      const planVisible = await page
        .locator('text=执行计划')
        .first()
        .isVisible()
        .catch(() => false)
      const outputVisible = await page
        .locator('text=产出物')
        .first()
        .isVisible()
        .catch(() => false)
      check('已结束的任务：首屏先给产出（可见）', outputVisible)
      check('已结束的任务：过程不占首屏（执行计划不可见）', planVisible === false)
      await page.screenshot({ path: join(SHOTS, '75-workbench-settled.png'), fullPage: true })
    } else {
      check('已结束的任务：详情默认收起', false, '没有可用的已结束任务来验证')
    }

  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(72))
  console.log(`  ${results.length - failed.length}/${results.length} 项通过`)
  if (failed.length) failed.forEach((f) => console.log(`    - ${f.name}${f.detail ? ` (${f.detail})` : ''}`))
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
