/**
 * Does the project view connect to the delivery layer?
 *
 * The product's three layers are meant to be one product. The project page is where
 * that shows: a project holds knowledge, tasks, and now the artifacts it produced.
 * Before this it stopped one step short — the files existed, nothing in the project
 * view knew about them.
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

  await page.goto(`${BASE}/projects`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)

  const links = await page.locator('a[href^="/projects/"]').count()
  check('项目列表有项目', links > 0, `${links} 个`)

  if (links > 0) {
    await page.locator('a[href^="/projects/"]').first().click()
    await page.waitForTimeout(3000)
    const body = await page.locator('body').innerText()
    check('项目详情页出现「产出物」标签', /产出物/.test(body))

    // Located by text: the tab button contains an icon, so its accessible name is
    // not exactly the label.
    const tab = page.locator('button', { hasText: '产出物' }).first()
    if ((await tab.count()) > 0) {
      await tab.click()
      await page.waitForTimeout(3000)
      const after = await page.locator('body').innerText()
      // Either real deliverables or the guided empty state — both mean the tab works.
      const rendered = /已验收|待验收|已退回|还没有产出/.test(after)
      check('产出物标签渲染内容', rendered, after.replace(/\n/g, ' ').slice(0, 70))
      await page.screenshot({ path: 'screenshots/ui-verification/70-project-deliverables.png' })
    } else {
      check('产出物标签渲染内容', false, '未找到可点击的标签')
    }
  }
} catch (exc) {
  check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 140))
}

await browser.close()
const failed = results.filter((r) => !r.ok)
console.log(`\n  ${results.length - failed.length}/${results.length} 通过`)
for (const f of failed) console.log(`    - ${f.name}`)
process.exit(failed.length ? 1 : 0)
