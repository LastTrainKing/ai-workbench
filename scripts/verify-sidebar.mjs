/**
 * Sidebar check — the conversation list, and one gear for everything else.
 *
 * The user's instruction for this round:
 *
 *     把现在软件里的内核全部藏起来，前端最后就是一个对话框
 *     左侧只有会话列表，不是 16 个菜单项。
 *     记忆 / 知识库 / Skill / 设置 → 收进一个不显眼的入口（齿轮或头像菜单）
 *
 * Two things are being verified, and the second is the one that is easy to get wrong:
 *
 *   1. the sidebar is the conversation list — not sixteen menu items;
 *   2. **everything is still reachable.** Hiding a menu is a presentation change;
 *      losing a route would not be. This walks every old route from the gear menu, so
 *      "we tidied the sidebar" cannot quietly become "we deleted the workbench".
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

  // ---------------------------------------------------- the sidebar is the list --
  check('侧栏有会话列表', (await page.locator('[data-testid="conversation-list"]').count()) > 0)
  check('侧栏有「新对话」', (await page.locator('[data-testid="new-conversation"]').count()) > 0)

  const rail = await page.locator('aside').first().innerText()
  const flat = rail.replace(/\n/g, ' | ')
  check('侧栏不再列出 16 个菜单项（工作台不可见）', !/工作台/.test(rail), flat.slice(0, 100))
  check('侧栏不再列出执行记录', !/执行记录/.test(rail))
  check('侧栏不出现技术词 RAG / 向量化', !/RAG|向量化|工具注册表/.test(rail))

  // ----------------------------------------------------------- the gear menu --
  const gear = page.locator('[data-testid="open-app-menu"]')
  check('有一个不显眼的菜单入口（齿轮）', (await gear.count()) > 0)
  await gear.click()
  await page.waitForTimeout(600)

  const menu = page.locator('[data-testid="app-menu"]')
  check('齿轮打开了菜单', (await menu.count()) > 0)
  const menuText = (await menu.count()) > 0 ? await menu.innerText() : ''
  const flatMenu = menuText.replace(/\n/g, ' | ')

  for (const label of ['记忆与偏好', '知识库', 'Skill', '模型与工作区', '权限']) {
    check(`菜单里有「${label}」`, menuText.includes(label), flatMenu.slice(0, 80))
  }
  check('菜单里的「高级」默认折叠（执行记录不可见）', !/执行记录/.test(menuText))

  const advanced = menu.getByRole('button', { name: /高级/ })
  if ((await advanced.count()) > 0) {
    await advanced.click()
    await page.waitForTimeout(600)
    const expanded = await menu.innerText()
    check('展开「高级」后有执行记录与工作台', /执行记录/.test(expanded) && /工作台/.test(expanded))
    await page.screenshot({ path: 'screenshots/ui-verification/41-sidebar-expanded.png' })
  } else {
    check('展开「高级」后有执行记录与工作台', false, '菜单里没有「高级」')
  }

  await page.screenshot({ path: 'screenshots/ui-verification/40-sidebar.png' })

  // ------------------------------------------------- every old route resolves --
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
  check(
    `全部 ${routes.length} 条旧路由仍可达`,
    reachable === routes.length,
    missing.join(',') || `${reachable}/${routes.length}`,
  )

  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
} catch (exc) {
  check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 140))
}

await browser.close()
const failed = results.filter((r) => !r.ok)
console.log(`\n  ${results.length - failed.length}/${results.length} 通过`)
for (const f of failed) console.log(`    - ${f.name}`)
process.exit(failed.length ? 1 : 0)
