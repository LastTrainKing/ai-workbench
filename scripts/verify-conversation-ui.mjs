/**
 * Browser test for the conversation screen — the new home.
 *
 * The acceptance this exists for, in the user's own words: a person who has never
 * seen the product should be able to say, unprompted, where they are supposed to
 * type. That is not a thing typecheck or build can tell you, and it is not a thing
 * that can be asserted from the DOM being merely *present* — a card of inputs in the
 * middle of a dashboard is present too. So the checks are positional and verbal:
 *
 *   * the input is in the lower part of the viewport, on arrival, without scrolling
 *   * the page says what to do in it
 *   * the timeline holds what was said, what happened, and what came out
 *   * the machinery is folded, and still there when unfolded
 *   * nothing on the screen is a status code or an engine-room word
 *
 *   node scripts/verify-conversation-ui.mjs
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

//: Words that mean nothing to the person this screen is for. Any of them appearing
//: is the old interface leaking back in.
const JARGON = /RAG|向量化|\btier\b|工具注册表|DRAFT|PLANNING|EXECUTING|VERIFYING|PARTIAL|LIMIT_REACHED/

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()

  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  const consoleErrors = []
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))

    // ------------------------------------------------------- where do I type --
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)

    const input = page.locator('[data-testid="goal-input"]')
    check('首页有一个输入框', (await input.count()) === 1, `${await input.count()} 个`)

    const box = await input.boundingBox()
    const viewport = page.viewportSize()
    // Bottom third of the screen, and on screen without scrolling. This is the whole
    // claim: a chat box you have to go looking for is not a chat box.
    const inLowerPart = box ? box.y + box.height > viewport.height * 0.6 : false
    check(
      '输入框在屏幕下方（像聊天工具，不用找）',
      inLowerPart,
      box ? `y=${Math.round(box.y)} h=${Math.round(box.height)} viewport=${viewport.height}` : '未找到',
    )
    check('输入框在首屏可见', box ? box.y + box.height <= viewport.height : false)

    const placeholder = await input.getAttribute('placeholder')
    check('输入框自己说清了要写什么', Boolean(placeholder && placeholder.length > 5), placeholder)

    const sendLabel = await page.locator('[data-testid="start-build"]').innerText()
    check('有一个明确的发送按钮', /发送|配置/.test(sendLabel), sendLabel)

    // ------------------------------------------------------------- timeline ---
    const turns = await page.locator('[data-testid="conversation-turn"]').count()
    check('时间线上有对话轮次', turns > 0, `${turns} 轮`)

    if (turns > 0) {
      const body = await page.locator('body').innerText()
      check('页面不含状态码与工程术语', !JARGON.test(body), (body.match(JARGON) ?? []).join(','))

      const status = await page.locator('[data-testid="turn-status"]').first().innerText()
      check('状态是一句话，不是一个码', !JARGON.test(status) && status.length > 2, status.slice(0, 40))

      // 产出物长在对话里 —— same turn, not another page.
      const inlineCards = await page
        .locator('[data-testid="conversation-turn"] [data-testid="deliverable-card"]')
        .count()
      check('产出物长在对话里（不是另一个页面）', inlineCards > 0, `${inlineCards} 张`)

      // 接受 / 退回 must stay reachable in the conversation: this is the loop the
      // product exists for, and hiding it behind a click would be the one change
      // that made this redesign a regression.
      const hasAccept = await page.locator('[data-testid="accept-deliverable"]').count()
      const hasReject = await page.locator('[data-testid="start-reject"]').count()
      const alreadyDecided = await page.locator('text=你已验收').count()
      check(
        '验收与退回就在对话里',
        hasAccept > 0 || hasReject > 0 || alreadyDecided > 0,
        `接受 ${hasAccept} · 退回 ${hasReject} · 已决定 ${alreadyDecided}`,
      )

      // 详情折叠
      const fold = page.locator('details:has([data-testid="run-details"])').first()
      check('有「详情」折叠区', (await fold.count()) > 0)
      const openNow = await fold.evaluate((el) => el.hasAttribute('open')).catch(() => null)
      check('详情默认收起（已结束的轮次）', openNow === false, `open=${openNow}`)

      // innerText on a collapsed <details> still returns the content — it is in the
      // DOM, just not laid out. Assert on what Playwright says is visible.
      const planVisibleBefore = await page.locator('text=执行计划').first().isVisible().catch(() => false)
      check('收起时看不到「执行计划」', planVisibleBefore === false)

      await page.screenshot({ path: join(SHOTS, '80-conversation.png'), fullPage: true })

      await page.locator('[data-testid="run-details"]').first().click()
      await page.waitForTimeout(900)
      check('展开后「执行计划」在', await page.locator('text=执行计划').first().isVisible().catch(() => false))
      check('展开后「实时执行」在', await page.locator('text=实时执行').first().isVisible().catch(() => false))
      check('展开后「本次上下文」在', await page.locator('text=本次上下文').first().isVisible().catch(() => false))
      await page.screenshot({ path: join(SHOTS, '81-conversation-details.png'), fullPage: true })
    }

    // -------------------------------------------------------------- sidebar ---
    const nav = await page.locator('nav').first().innerText()
    check('主菜单是四项（对话 / 我的 / 资料 / 设置）',
      /对话/.test(nav) && /我的/.test(nav) && /资料/.test(nav) && /设置/.test(nav),
      nav.replace(/\n/g, ' | ').slice(0, 80),
    )
    check('「高级」默认折叠（执行记录不可见）', !/执行记录/.test(nav))

    const advanced = page.getByRole('button', { name: /高级/ })
    if ((await advanced.count()) > 0) {
      await advanced.click()
      await page.waitForTimeout(700)
      const expanded = await page.locator('nav').first().innerText()
      check('展开「高级」后旧入口都在', /执行记录/.test(expanded) && /权限/.test(expanded) && /工作台/.test(expanded))
      await page.screenshot({ path: join(SHOTS, '82-conversation-advanced.png') })
    } else {
      check('展开「高级」后旧入口都在', false, '未找到「高级」标题')
    }

    // ----------------------------------------------------------- old routes ---
    const routes = [
      '/', '/home', '/me', '/welcome', '/agent', '/projects', '/tasks', '/planner',
      '/knowledge', '/documents', '/search', '/chat', '/notes', '/ai-runs',
      '/graph', '/settings', '/settings/agent', '/workbench',
    ]
    let reachable = 0
    const missing = []
    for (const route of routes) {
      const response = await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded' })
      if (response && response.status() === 200) reachable += 1
      else missing.push(`${route}(${response?.status()})`)
    }
    check(`全部 ${routes.length} 条旧路由仍可达`, reachable === routes.length, missing.join(',') || `${reachable}/${routes.length}`)

    // ------------------------------------------------------ the old home too ---
    await page.goto(`${BASE}/home`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const oldHome = await page.locator('body').innerText()
    check('上一版的四区首页仍然在（没有被删掉）', /最近产出/.test(oldHome) && /它学到了什么/.test(oldHome))

    const real = consoleErrors.filter((e) => !/favicon|DevTools|ResizeObserver/i.test(e))
    check('无 JS 运行时错误', errors.length === 0 && real.length === 0, [...errors, ...real].slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error-conversation.png') }).catch(() => {})
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(72))
  console.log(`  ${results.length - failed.length}/${results.length} 项通过`)
  for (const f of failed) console.log(`    - ${f.name}${f.detail ? ` (${f.detail})` : ''}`)
  console.log('='.repeat(72))
  process.exit(failed.length ? 1 : 0)
}

main()
