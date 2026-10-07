/**
 * Sidebar restructure check.
 *
 * The product rule being verified: the user should never have to read the word
 * "RAG" or "vector" or "runtime". Those surfaces still exist and every old route
 * still resolves — what changed is where they sit and what they are called.
 *
 * The specific claim this round added: **four entries, then everything else.** The
 * user's complaint about the previous arrangement was that it still read like a
 * control panel — eight entries under 工作, each a different way of looking at the
 * same work. So the top of the list is what someone opens the product to do, and
 * the rest is under 高级, collapsed.
 */

import { chromium } from './_playwright.mjs'

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage())

try {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
  await page.waitForTimeout(1500)

  const nav = await page.locator('nav').first().innerText()
  const flat = nav.replace(/\n/g, ' | ')

  // The four main entries, in the order a person needs them.
  for (const label of ['对话', '我的', '资料', '设置']) {
    check(`主菜单包含「${label}」`, nav.includes(label))
  }
  check('侧栏包含「高级」', /高级/.test(nav))
  check('不再出现技术词 RAG', !/RAG/.test(nav), flat.slice(0, 90))
  check('不再出现技术词 知识库', !/知识库/.test(nav))

  // The count that matters: what is visible before anything is expanded.
  const mainEntries = await page.locator('nav a').count()
  check('未展开时可见的入口不超过 6 个', mainEntries <= 6, `${mainEntries} 个`)

  // The 高级 section starts closed.
  check('「高级」默认折叠（执行记录不可见）', !/执行记录/.test(nav))

  const advanced = page.getByRole('button', { name: /高级/ })
  if ((await advanced.count()) > 0) {
    await advanced.click()
    await page.waitForTimeout(700)
    const expanded = await page.locator('nav').first().innerText()
    check('点击后展开显示执行记录', /执行记录/.test(expanded))
    check('展开后显示权限与工作台', /权限/.test(expanded) && /工作台/.test(expanded))
    await page.screenshot({ path: 'screenshots/ui-verification/41-sidebar-expanded.png' })
    await advanced.click()
    await page.waitForTimeout(500)
  } else {
    check('点击后展开显示执行记录', false, '未找到可点击的「高级」标题')
  }

  // Every old route must still resolve — renaming a section is presentation,
  // deleting a route would not be.
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

  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await page.screenshot({ path: 'screenshots/ui-verification/40-sidebar.png' })
} catch (exc) {
  check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 140))
}

await browser.close()
const failed = results.filter((r) => !r.ok)
console.log(`\n  ${results.length - failed.length}/${results.length} 通过`)
for (const f of failed) console.log(`    - ${f.name}`)
process.exit(failed.length ? 1 : 0)
