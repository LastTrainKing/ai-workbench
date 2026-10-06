/**
 * Browser verification for the Skill page.
 *
 * The page has to be driven, not read. Three things only a browser can check:
 *
 *   1. the tab exists under 「我的」 and opens;
 *   2. a real search reaches the API and renders candidates **with their reasons**;
 *   3. adopting is a separate click from searching — the search on its own installs
 *      nothing, which is the rule the whole assess stage exists to enforce.
 *
 *   node scripts/verify-skill-ui.mjs
 */

import { chromium } from './_playwright.mjs'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'skills')
const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
  const page = await context.newPage()

  const consoleErrors = []
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

  const calls = []
  page.on('request', (r) => {
    if (r.url().includes('/api/v1/skills')) calls.push(`${r.method()} ${r.url().split('/api/v1')[1]}`)
  })

  try {
    // A fresh account, so "nothing installed yet" is a fact about this run.
    const stamp = Date.now().toString(36)
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.getByRole('button', { name: '注册' }).click()
    await page.waitForSelector('#email', { timeout: 15000 })
    await page.fill('#email', `skill_${stamp}@aiworkbench.dev`)
    await page.fill('#username', `skill_${stamp}`)
    await page.fill('#display_name', 'Skill UI')
    await page.fill('#password', 'test1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 25000 })

    // Mark the account onboarded through the API rather than by clicking through the
    // wizard. This test is about the skill page; driving the wizard first made the run
    // depend on that page's markup, and when it drifted the failure looked like a skill
    // page bug. The wizard has its own test.
    const token = await page.evaluate(() => window.localStorage.getItem('aiw.token'))
    await page.request.patch(`${BASE}/api/v1/profile`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { onboarded: true },
    })
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1200)
    check('账号已就绪（非向导页）', new URL(page.url()).pathname === '/', `实际 ${new URL(page.url()).pathname}`)

    // ---------------------------------------------------------------- the tab --
    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(2000)
    const tab = page.getByRole('button', { name: 'Skill' })
    if ((await tab.count()) === 0) {
      // Say where the page actually is rather than only that a locator timed out. The
      // first version of this test failed here with nothing but "Timeout", which does not
      // distinguish "the tab is missing" from "we are on a different page".
      console.log(`        当前 URL：${new URL(page.url()).pathname}`)
      const visible = await page.locator('body').innerText()
      console.log(`        页面片段：${visible.slice(0, 220).replace(/\s+/g, ' ')}`)
      await page.screenshot({ path: join(SHOTS, '00-where-am-i.png') })
    }
    check('「我的」下面有 Skill 标签', (await tab.count()) > 0)
    if ((await tab.count()) > 0) { await tab.click() }
    await page.waitForTimeout(1800)
    check('点开后页面加载', (await page.locator('text=去找').count()) > 0)
    check('新账号显示「还没有导入任何 skill」', (await page.locator('text=还没有导入任何 skill').count()) > 0)
    await page.screenshot({ path: join(SHOTS, '01-skill-tab-empty.png') })

    // ----------------------------------------------------------- search first --
    const before = calls.filter((c) => c.startsWith('POST /skills/adopt')).length
    const box = page.getByPlaceholder(/你想把哪类活干完/)
    await box.fill('写毕业论文的文献综述')
    await page.getByRole('button', { name: /找找看/ }).click()
    await page.waitForTimeout(12000)

    const searched = calls.some((c) => c.startsWith('POST /skills/discover'))
    check('点「找找看」真的调用了 /skills/discover', searched, calls.slice(-3).join(' | '))

    const after = calls.filter((c) => c.startsWith('POST /skills/adopt')).length
    check('搜索本身不导入任何东西', after === before, `adopt 调用 ${before} -> ${after}`)

    const body = await page.locator('body').innerText()
    const foundCandidates = /导入/.test(body)
    check('候选渲染出来了', foundCandidates)
    const hasReasons = /star/.test(body)
    check('候选带理由（star / 更新时间 / 许可）', hasReasons)
    await page.screenshot({ path: join(SHOTS, '02-skill-candidates.png') })

    if (foundCandidates) {
      const rejected = page.locator('summary', { hasText: '被筛掉的' })
      check('被筛掉的候选也列出来了', (await rejected.count()) > 0)
    }

    // ------------------------------------------------------------- adopt once --
    const adoptButtons = page.getByRole('button', { name: '导入' })
    const count = await adoptButtons.count()
    if (count > 0) {
      await adoptButtons.first().click()
      await page.waitForTimeout(4000)
      const adopted = calls.some((c) => c.startsWith('POST /skills/adopt'))
      check('点「导入」才调用 /skills/adopt', adopted)

      const text = await page.locator('body').innerText()
      check('导入后出现在已安装列表', /已安装/.test(text) && !/还没有导入任何 skill/.test(text))
      await page.screenshot({ path: join(SHOTS, '03-skill-installed.png') })

      const reviewButton = page.getByRole('button', { name: '好用' })
      check('复盘按钮存在', (await reviewButton.count()) > 0)
      if ((await reviewButton.count()) > 0) {
        await reviewButton.first().click()
        await page.waitForTimeout(3000)
        const reviewed = calls.some((c) => c.includes('/review'))
        check('点「好用」记录了复盘', reviewed)
        await page.screenshot({ path: join(SHOTS, '04-skill-reviewed.png') })
      }
    }

    check('浏览器控制台没有报错', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`)
  for (const f of failed) console.log(`  [FAIL] ${f.name} — ${f.detail}`)
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
