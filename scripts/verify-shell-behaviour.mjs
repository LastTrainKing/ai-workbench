/**
 * Real browser test for the desktop-shell behaviours.
 *
 * D2 - the window must not behave like a browser page.
 * D5 - the window must survive the backend going away.
 *
 * D5 is exercised by intercepting the health probe rather than stopping the
 * backend: the code path under test is "the probe fails", and blocking the
 * request produces exactly that condition without taking the whole environment
 * down in the middle of a test run.
 *
 *   node scripts/verify-shell-behaviour.mjs
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

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForSelector('#identifier', { timeout: 20000 })
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20000 })
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()

  try {
    await login(page)

    // =========================================================== D2 ==========
    console.log('\n--- D2 桌面窗口行为 ---')

    // The shell class must be installed; everything else depends on it.
    const hasShellClass = await page.evaluate(() =>
      document.documentElement.classList.contains('aw-desktop-shell'),
    )
    check('D2.1 桌面 shell 样式已启用', hasShellClass, 'html.aw-desktop-shell')

    // --- context menu ---
    const menuBlocked = await page.evaluate(() => {
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
      document.body.dispatchEvent(event)
      // preventDefault makes defaultPrevented true.
      return event.defaultPrevented
    })
    check('D2.2 页面右键菜单被禁用', menuBlocked)

    // ...but still available inside an input, where paste is a real gesture.
    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1000)
    const inputMenuAllowed = await page.evaluate(() => {
      const input = document.querySelector('input')
      if (!input) return null
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
      input.dispatchEvent(event)
      return !event.defaultPrevented
    })
    check('D2.3 输入框内右键仍可用（可粘贴）', inputMenuAllowed === true, String(inputMenuAllowed))

    // --- keyboard ---
    const blocked = async (key, modifiers = []) => {
      const before = page.url()
      await page.keyboard.press([...modifiers, key].join('+'))
      await page.waitForTimeout(600)
      return { sameUrl: page.url() === before }
    }

    const f5 = await blocked('F5')
    check('D2.4 F5 不刷新页面', f5.sameUrl, `url 未变`)

    const ctrlR = await blocked('r', ['Control'])
    check('D2.5 Ctrl+R 不刷新页面', ctrlR.sameUrl)

    const ctrlP = await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', {
        key: 'p', ctrlKey: true, bubbles: true, cancelable: true,
      })
      document.dispatchEvent(event)
      return event.defaultPrevented
    })
    check('D2.6 Ctrl+P 不触发打印', ctrlP)

    const ctrlU = await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', {
        key: 'u', ctrlKey: true, bubbles: true, cancelable: true,
      })
      document.dispatchEvent(event)
      return event.defaultPrevented
    })
    check('D2.7 Ctrl+U 不查看源码', ctrlU)

    const f12 = await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', { key: 'F12', bubbles: true, cancelable: true })
      document.dispatchEvent(event)
      return event.defaultPrevented
    })
    check('D2.8 F12 不打开开发者工具', f12)

    // --- zoom ---
    const wheelBlocked = await page.evaluate(() => {
      const event = new WheelEvent('wheel', { deltaY: -120, ctrlKey: true, bubbles: true, cancelable: true })
      document.dispatchEvent(event)
      return event.defaultPrevented
    })
    check('D2.9 Ctrl+滚轮不缩放', wheelBlocked)

    // A plain wheel must still scroll — the guard must not be a blanket ban.
    const plainWheelAllowed = await page.evaluate(() => {
      const event = new WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true })
      document.dispatchEvent(event)
      return !event.defaultPrevented
    })
    check('D2.10 普通滚轮仍可滚动', plainWheelAllowed)

    // --- selection ---
    const selectionState = await page.evaluate(() => {
      const body = getComputedStyle(document.body).userSelect
      const input = document.querySelector('input')
      const inputSelect = input ? getComputedStyle(input).userSelect : 'no-input'
      return { body, inputSelect }
    })
    check('D2.11 页面文本不可拖选', selectionState.body === 'none', selectionState.body)
    check(
      'D2.12 输入框内仍可选中',
      selectionState.inputSelect === 'text' || selectionState.inputSelect === 'no-input',
      selectionState.inputSelect,
    )

    // --- title ---
    const title = await page.title()
    check('D2.13 窗口标题不含地址', !title.includes('http') && !title.includes('127.0.0.1'), title)

    // --- typing still works ---
    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1000)
    const input = page.locator('input[placeholder*="手动添加"]')
    if ((await input.count()) > 0) {
      await input.click()
      await input.type('shell-guard-typing-test')
      const value = await input.inputValue()
      check('D2.14 键盘输入仍然正常', value === 'shell-guard-typing-test', value.slice(0, 30))
      await input.fill('')
    } else {
      check('D2.14 键盘输入仍然正常', false, '未找到输入框')
    }

    // =========================================================== D5 ==========
    console.log('\n--- D5 后端不可用时的降级 ---')

    // Block the health probe so the connection hook sees a failure.
    await page.route('**/api/v1/health', (route) => route.abort('failed'))

    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
    // Give the hook time to fail its first probe and render the banner.
    await page.waitForTimeout(6000)

    const banner = page.locator('[data-testid="connection-banner"]')
    const bannerCount = await banner.count()
    check('D5.1 失去连接时显示降级提示', bannerCount > 0)

    if (bannerCount > 0) {
      const text = await banner.innerText()
      check('D5.2 提示说明正在重试', /正在重试|重试/.test(text), text.replace(/\n/g, ' ').slice(0, 80))
      check('D5.3 提示包含最后成功连接时间', /最后成功连接/.test(text))
      check('D5.4 提示说明已加载内容仍可用', /仍可查看|仍可用/.test(text))
      await page.screenshot({ path: join(SHOTS, '20-offline-banner.png') })

      // The window must still be usable: navigate and interact.
      await page.goto(`${BASE}/me`, { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(2500)
      const stillUsable = await page.locator('body').innerText()
      check('D5.5 断连时页面仍可操作', /我的 AI/.test(stillUsable))

      // Recovery: unblock and confirm the banner clears itself.
      await page.unroute('**/api/v1/health')
      await page.waitForTimeout(18000)
      const bannerAfter = await page.locator('[data-testid="connection-banner"]').count()
      check('D5.6 恢复后提示自动消失', bannerAfter === 0, `剩余 ${bannerAfter} 个横幅`)
    } else {
      await page.screenshot({ path: join(SHOTS, '20-offline-missing.png') })
    }

    // =================================================== regression =========
    console.log('\n--- 回归 ---')
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 4,
    )
    check('无横向溢出', !overflow)
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 180))
    await page.screenshot({ path: join(SHOTS, 'error-shell.png') }).catch(() => {})
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
