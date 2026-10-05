/**
 * Keyboard navigation test for the home screen.
 *
 * The claim being checked is specific: the launch grid can be driven entirely
 * from the keyboard, the focus ring is visible, and the mouse and the keyboard
 * agree about where "here" is. A grid that responds to arrows but gives no visual
 * feedback is not navigable — it is just a keyboard shortcut nobody can use.
 *
 *   node scripts/verify-focus-grid.mjs
 */

import { chromium } from 'playwright-core'
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

/** Index of the focused card, or -1. */
async function focusedIndex(page) {
  return page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-focused]'))
    return cards.findIndex((el) => el.getAttribute('data-focused') === 'true')
  })
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)

    const grid = page.locator('[aria-label="快速入口"]')
    check('首页存在入口栅格', (await grid.count()) > 0)

    const cards = grid.locator('button[data-focused]')
    const cardCount = await cards.count()
    check('栅格包含多个入口卡片', cardCount >= 6, `${cardCount} 张`)

    const columns = await page.evaluate(() => {
      const el = document.querySelector('[aria-label="快速入口"]')
      return el ? getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length : 0
    })
    check('栅格为多列布局', columns >= 2, `${columns} 列`)

    // Nothing focused until a key is pressed.
    check('初始无焦点', (await focusedIndex(page)) === -1)

    // First arrow press focuses the first card.
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(250)
    check('首次按方向键聚焦第一张卡', (await focusedIndex(page)) === 0)

    // The ring must be visibly different, not just a data attribute.
    const ringStyle = await page.evaluate(() => {
      const el = document.querySelector('[data-focused="true"]')
      if (!el) return null
      const style = getComputedStyle(el)
      return { shadow: style.boxShadow, border: style.borderColor }
    })
    check(
      '焦点态有可见的视觉反馈（描边 + 发光）',
      Boolean(ringStyle && ringStyle.shadow && ringStyle.shadow !== 'none'),
      ringStyle ? ringStyle.shadow.slice(0, 44) : 'no element',
    )
    await page.screenshot({ path: join(SHOTS, '50-focus-first.png') })

    // Movement.
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(200)
    const right = await focusedIndex(page)
    check('→ 移动到下一张', right === 1, `index=${right}`)

    await page.keyboard.press('ArrowLeft')
    await page.waitForTimeout(200)
    const left = await focusedIndex(page)
    check('← 移回上一张', left === 0, `index=${left}`)

    await page.keyboard.press('ArrowDown')
    await page.waitForTimeout(200)
    const down = await focusedIndex(page)
    check('↓ 跳到下一行', down === columns, `index=${down}（列数 ${columns}）`)

    await page.keyboard.press('ArrowUp')
    await page.waitForTimeout(200)
    const up = await focusedIndex(page)
    check('↑ 回到上一行', up === 0, `index=${up}`)

    // Wrapping: left from the first card goes to the end of its row.
    await page.keyboard.press('ArrowLeft')
    await page.waitForTimeout(200)
    const wrapped = await focusedIndex(page)
    check('← 在行首环绕到行尾', wrapped === columns - 1, `index=${wrapped}`)

    // Escape releases focus.
    await page.keyboard.press('Escape')
    await page.waitForTimeout(250)
    check('Esc 释放焦点', (await focusedIndex(page)) === -1)

    // Enter activates.
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(200)
    await page.keyboard.press('Enter')
    await page.waitForTimeout(1500)
    check('Enter 进入对应页面', page.url().includes('/agent'), page.url())

    // The mouse still works, and hover sets the same focus the keyboard uses.
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    const third = grid.locator('button[data-focused]').nth(2)
    await third.hover()
    await page.waitForTimeout(300)
    check('鼠标悬停也设置焦点（与键盘一致）', (await focusedIndex(page)) === 2)
    await third.click()
    await page.waitForTimeout(1500)
    check('鼠标点击仍然可用', !page.url().endsWith('/'), page.url())

    // Tab must still work — the grid must not swallow it.
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    await page.keyboard.press('Tab')
    await page.waitForTimeout(250)
    const tabWorked = await page.evaluate(() => document.activeElement !== document.body)
    check('Tab 未被栅格吞掉', tabWorked)

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 4,
    )
    check('无横向溢出', !overflow)
    await page.screenshot({ path: join(SHOTS, '51-home-grid.png') })
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 160))
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
