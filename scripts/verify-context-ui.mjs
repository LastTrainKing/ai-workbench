/**
 * Real browser interaction test for the personal context screens.
 *
 * The same reason this exists for the agent workbench: typecheck and build prove
 * almost nothing about 900 lines of React. These pages in particular have to be
 * driven, because what they promise is that a user can *see and change* what the
 * system believes about them — and only a click can confirm that the button is
 * wired to the API rather than to local state.
 *
 *   node scripts/verify-context-ui.mjs
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

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } })
  const page = await context.newPage()

  const consoleErrors = []
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

  const calls = []
  page.on('request', (r) => {
    const url = r.url()
    if (url.includes('/api/v1/')) calls.push(`${r.method()} ${url.split('/api/v1')[1]}`)
  })

  try {
    // ------------------------------------------------------------- login ---
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier', { timeout: 20000 })
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20000 })
    check('登录成功', true)

    // --------------------------------------------------------- my AI page ---
    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1500)
    const body = await page.locator('body').innerText()
    check('「我的 AI」页面加载', /我的 AI/.test(body), body.slice(0, 50).replace(/\n/g, ' '))
    check('显示记忆统计', /长期记忆/.test(body) && /待确认/.test(body))
    await page.screenshot({ path: join(SHOTS, '10-my-ai-memory.png'), fullPage: true })

    // ------------------------------------------------------ add a memory ---
    const input = page.locator('input[placeholder*="手动添加"]')
    check('记忆输入框存在', (await input.count()) > 0)
    if ((await input.count()) > 0) {
      const before = calls.length
      await input.fill('这是一条浏览器测试写入的记忆')
      await page.getByRole('button', { name: /添加/ }).first().click()
      await page.waitForTimeout(2000)
      const posted = calls.slice(before).some((c) => c.startsWith('POST /memories'))
      check('添加记忆真的调用了 POST /memories', posted, calls.slice(before).slice(0, 2).join(' | '))

      const after = await page.locator('body').innerText()
      check('新记忆出现在列表中', /浏览器测试写入/.test(after))
      await page.screenshot({ path: join(SHOTS, '11-my-ai-added.png'), fullPage: true })
    }

    // ------------------------------------------------------- edit/delete ---
    const deleteButtons = page.locator('button:has(svg)').filter({ hasText: '' })
    const editCount = await page.locator('button:has(svg.lucide-pencil)').count()
    check('记忆行有编辑按钮', editCount > 0, `${editCount} 个`)

    // --------------------------------------------------------- profile tab ---
    const profileTab = page.getByRole('button', { name: /^档案$/ })
    if ((await profileTab.count()) > 0) {
      await profileTab.click()
      await page.waitForTimeout(1200)
      const profileBody = await page.locator('body').innerText()
      check('档案页显示字段', /身份/.test(profileBody) && /当前目标/.test(profileBody))
      await page.screenshot({ path: join(SHOTS, '12-my-ai-profile.png'), fullPage: true })

      const before = calls.length
      const identityInput = page.locator('input').nth(1)
      await identityInput.fill('浏览器测试身份')
      await page.getByRole('button', { name: /保存/ }).first().click()
      await page.waitForTimeout(2000)
      const patched = calls.slice(before).some((c) => c.startsWith('PATCH /profile'))
      check('保存档案真的调用了 PATCH /profile', patched, calls.slice(before).slice(0, 2).join(' | '))
    } else {
      check('档案页显示字段', false, '未找到档案标签')
    }

    // --------------------------------------------------------- context tab ---
    const contextTab = page.getByRole('button', { name: /上下文预览/ })
    if ((await contextTab.count()) > 0) {
      await contextTab.click()
      await page.waitForTimeout(1800)
      const ctxBody = await page.locator('body').innerText()
      check('上下文预览加载', /本次会注入什么/.test(ctxBody))
      check('显示 token 预算', /tokens/.test(ctxBody))
      check('显示实际发送文本', /实际发送的完整文本/.test(ctxBody))
      await page.screenshot({ path: join(SHOTS, '13-my-ai-context.png'), fullPage: true })
    } else {
      check('上下文预览加载', false, '未找到上下文标签')
    }

    // --------------------------------------------------------- onboarding ---
    await page.goto(`${BASE}/welcome`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1200)
    const welcomeBody = await page.locator('body').innerText()
    check('向导页面加载', /欢迎/.test(welcomeBody))
    check('两个入口都存在', /从介绍自己开始/.test(welcomeBody) && /先随便看看/.test(welcomeBody))
    await page.screenshot({ path: join(SHOTS, '14-onboarding.png'), fullPage: true })

    // The first door: describe yourself, then confirm. Nothing should be stored
    // before the confirmation step.
    const startButton = page.getByText('从介绍自己开始')
    if ((await startButton.count()) > 0) {
      await startButton.click()
      await page.waitForTimeout(900)
      const textarea = page.locator('textarea').first()
      check('自我介绍输入框出现', (await textarea.count()) > 0)
      if ((await textarea.count()) > 0) {
        await textarea.fill('我是计算机大三，在做毕业设计，最近想找实习')
        const before = calls.length
        await page.getByRole('button', { name: /看看它理解成了什么/ }).click()
        await page.waitForTimeout(6000)
        const extracted = calls.slice(before).some((c) => c.startsWith('POST /profile/extract'))
        check('抽取真的调用了 POST /profile/extract', extracted, calls.slice(before).slice(0, 2).join(' | '))

        const confirmBody = await page.locator('body').innerText()
        check('展示理解结果等待确认', /它这样理解你/.test(confirmBody))
        await page.screenshot({ path: join(SHOTS, '15-onboarding-confirm.png'), fullPage: true })

        // Confirm and land on the home page.
        const beforeConfirm = calls.length
        await page.getByRole('button', { name: /确认，开始使用/ }).click()
        await page.waitForTimeout(3000)
        const saved = calls.slice(beforeConfirm).some((c) => c.startsWith('PATCH /profile'))
        check('确认后才写入档案', saved, calls.slice(beforeConfirm).slice(0, 2).join(' | '))
        check('确认后跳转离开向导', !page.url().includes('/welcome'), page.url())
      }
    }

    // ----------------------------------------------------------- responsive --
    for (const [w, h, label] of [
      [1280, 720, '1280x720'],
      [2560, 1440, '2560x1440'],
    ]) {
      await page.setViewportSize({ width: w, height: h })
      await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1200)
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 4,
      )
      check(`${label} 无横向溢出`, !overflow)
    }

    const real = consoleErrors.filter((e) => !/favicon|DevTools|ResizeObserver/i.test(e))
    check('无 JS 运行时错误', real.length === 0, real.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 180))
    await page.screenshot({ path: join(SHOTS, 'error-context.png') }).catch(() => {})
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
