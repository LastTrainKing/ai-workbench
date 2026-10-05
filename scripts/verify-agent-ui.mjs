/**
 * Real browser interaction test for the agent workbench and the permissions screen.
 *
 * This exists because "typecheck passes" and "the route returns 200" prove almost
 * nothing about 900 lines of new React. The only way to know the plan renders, the
 * event stream updates, the buttons call the API and polling stops when the run
 * ends is to drive a real browser and look.
 *
 * Uses the system Edge through playwright-core: no browser download, and it is
 * the same engine (Chromium) the Tauri shell embeds, so the layout under test is
 * the layout users get.
 *
 *   node scripts/verify-agent-ui.mjs
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
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

  // Track every backend call the UI makes, so "the button really calls the API"
  // is evidence rather than an assumption.
  const calls = []
  page.on('request', (req) => {
    const url = req.url()
    if (url.includes('/api/v1/')) calls.push(`${req.method()} ${url.split('/api/v1')[1]}`)
  })

  try {
    // ------------------------------------------------------------- login ---
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    // The form labels are wired with htmlFor -> id, so target the ids directly.
    await page.waitForSelector('#identifier', { timeout: 20000 })
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20000 })
    check('登录成功并进入应用', true)
    await page.screenshot({ path: join(SHOTS, '00-home.png') })

    // ---------------------------------------------------- agent workbench ---
    await page.goto(`${BASE}/agent`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1200)

    const heading = await page.locator('h1, h2').first().innerText().catch(() => '')
    check('Agent 页面加载', /Agent/i.test(heading) || heading.length > 0, `标题="${heading}"`)

    const textarea = page.locator('textarea').first()
    check('任务输入框存在', (await textarea.count()) > 0)
    await page.screenshot({ path: join(SHOTS, '01-agent-empty.png') })

    // ------------------------------------------------------ start a run ---
    const goal =
      '在工作目录创建 ui-check.txt，内容为 "browser verified"，然后用 file_read 读回确认，最后告诉我文件路径。'
    await textarea.fill(goal)
    const startButton = page.getByRole('button', { name: /开始执行/ })
    check('开始按钮可点击', await startButton.isEnabled())

    const before = calls.length
    await startButton.click()

    // The POST must actually happen.
    await page.waitForTimeout(2500)
    const posted = calls.slice(before).some((c) => c.startsWith('POST /agent/runs'))
    check('点击后真的调用了 POST /agent/runs', posted, calls.slice(before).slice(0, 3).join(' | '))

    // -------------------------------------------------------- plan shows ---
    await page.waitForSelector('text=执行计划', { timeout: 15000 })
    check('执行计划面板出现', true)
    await page.screenshot({ path: join(SHOTS, '02-agent-planning.png') })

    // Wait for the plan to be parsed and rendered as steps.
    let sawSteps = false
    for (let i = 0; i < 40; i++) {
      const stepCount = await page.locator('ol li').count()
      if (stepCount > 0) {
        sawSteps = true
        break
      }
      await page.waitForTimeout(1000)
    }
    check('执行计划渲染出步骤', sawSteps, `步骤数=${await page.locator('ol li').count()}`)

    // ------------------------------------------------------ events flow ---
    let eventRows = 0
    for (let i = 0; i < 60; i++) {
      eventRows = await page.locator('text=/\\[?(TOOL|VERIFY|STEP|PLAN)/').count().catch(() => 0)
      const bodyText = await page.locator('body').innerText()
      if (/file_write|file_read|验证通过|执行中|验证/.test(bodyText)) break
      await page.waitForTimeout(1500)
    }
    const body = await page.locator('body').innerText()
    check('实时事件流出现工具调用', /file_write|file_read|directory_list/.test(body))
    await page.screenshot({ path: join(SHOTS, '03-agent-executing.png'), fullPage: true })

    // --------------------------------------------------------- metrics ---
    check('运行指标面板存在', /工具调用/.test(body) && /重试/.test(body))

    // ------------------------------------------- pause / resume buttons ---
    const pauseButton = page.getByRole('button', { name: /暂停/ })
    const hasPause = (await pauseButton.count()) > 0
    check('运行中显示暂停/停止按钮', hasPause)

    if (hasPause) {
      const beforePause = calls.length
      await pauseButton.click()
      await page.waitForTimeout(1500)
      const paused = calls.slice(beforePause).some((c) => c.includes('/pause'))
      check('暂停按钮真的调用了 pause API', paused, calls.slice(beforePause).join(' | '))

      const resumeButton = page.getByRole('button', { name: /继续/ })
      if ((await resumeButton.count()) > 0) {
        const beforeResume = calls.length
        await resumeButton.click()
        await page.waitForTimeout(1500)
        const resumed = calls.slice(beforeResume).some((c) => c.includes('/resume'))
        check('继续按钮真的调用了 resume API', resumed)
      } else {
        check('暂停后出现继续按钮', false, '未找到继续按钮')
      }
      await page.screenshot({ path: join(SHOTS, '04-agent-paused.png') })
    }

    // ------------------------------------------------ wait for terminal ---
    let terminal = false
    for (let i = 0; i < 90; i++) {
      const text = await page.locator('body').innerText()
      if (/执行结果/.test(text)) {
        terminal = true
        break
      }
      await page.waitForTimeout(2000)
    }
    check('运行到达终态并显示执行结果', terminal)
    // The backend emits the final summary a second or two after the terminal
    // state, so give it a moment before asserting. Checking immediately would
    // fail a product that works.
    let resultText = ''
    for (let i = 0; i < 20; i++) {
      resultText = await page.locator('body').innerText()
      if (!/正在生成执行总结/.test(resultText)) break
      await page.waitForTimeout(800)
    }
    check(
      '结果面板包含真实总结（非空）',
      !/正在生成执行总结/.test(resultText) && resultText.length > 200,
      /正在生成执行总结/.test(resultText) ? '仍在等待总结' : `已渲染 ${resultText.length} 字符`,
    )
    await page.screenshot({ path: join(SHOTS, '05-agent-result.png'), fullPage: true })

    // ------------------------------------ polling stops when finished ----
    if (terminal) {
      await page.waitForTimeout(1000)
      const countA = calls.filter((c) => c.startsWith('GET /agent/runs/') && !c.includes('/events')).length
      await page.waitForTimeout(6000)
      const countB = calls.filter((c) => c.startsWith('GET /agent/runs/') && !c.includes('/events')).length
      check('终态后停止轮询', countB === countA, `${countA} → ${countB} 次状态请求`)
    }

    // -------------------------------------------- unmount stops polling ---
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1000)
    const afterLeave = calls.filter((c) => c.startsWith('GET /agent/runs/') && !c.includes('/events')).length
    await page.waitForTimeout(6000)
    const afterWait = calls.filter((c) => c.startsWith('GET /agent/runs/') && !c.includes('/events')).length
    check('离开页面后停止轮询', afterWait === afterLeave, `${afterLeave} → ${afterWait}`)

    // --------------------------------------------------------- diff view ---
    await page.goto(`${BASE}/agent`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)

    // ------------------------------------------- permissions screen ------
    await page.goto(`${BASE}/settings/agent`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForTimeout(1500)
    const permBody = await page.locator('body').innerText()
    check('权限页面加载', /Agent 权限/.test(permBody))
    check('显示当前模式', /Supervised|监督|自主|辅助/.test(permBody))
    await page.screenshot({ path: join(SHOTS, '06-permissions.png'), fullPage: true })

    // Protected paths live behind the third tab; open it before asserting.
    const pathsTab = page.getByRole('button', { name: /路径与网络/ })
    if ((await pathsTab.count()) > 0) {
      await pathsTab.click()
      await page.waitForTimeout(1200)
      const pathsBody = await page.locator('body').innerText()
      check('受保护路径可见且包含 .ssh', /\.ssh/.test(pathsBody))
      check('显示网络白名单', /api\.deepseek\.com|github\.com/.test(pathsBody))
      await page.screenshot({ path: join(SHOTS, '06b-permissions-paths.png'), fullPage: true })
      const capsTab = page.getByRole('button', { name: /能力开关/ })
      if ((await capsTab.count()) > 0) {
        await capsTab.click()
        await page.waitForTimeout(800)
      }
    } else {
      check('受保护路径可见且包含 .ssh', false, '未找到路径标签页')
    }

    // Change the mode and confirm the API was called.
    const beforeMode = calls.length
    const assistButton = page.getByRole('button', { name: /Assist|辅助/ }).first()
    if ((await assistButton.count()) > 0) {
      await assistButton.click()
      await page.waitForTimeout(1800)
      const patched = calls.slice(beforeMode).some((c) => c.startsWith('PATCH /agent/permissions'))
      check('修改模式真的调用了 PATCH API', patched, calls.slice(beforeMode).join(' | '))
    } else {
      check('修改模式真的调用了 PATCH API', false, '未找到模式按钮')
    }

    // Reload and confirm persistence.
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(1800)
    const afterReload = await page.locator('body').innerText()
    const persisted = /Assist|辅助/.test(afterReload)
    check('刷新后权限状态保持', persisted)

    // Restore supervised so the product is left in its default state.
    const supervised = page.getByRole('button', { name: /Supervised|监督/ }).first()
    if ((await supervised.count()) > 0) {
      await supervised.click()
      await page.waitForTimeout(1500)
    }

    // Capability toggle.
    const beforeCap = calls.length
    const toggles = page.locator('button[role="switch"]')
    const toggleCount = await toggles.count()
    if (toggleCount > 0) {
      await toggles.first().click()
      await page.waitForTimeout(1800)
      const patchedCap = calls.slice(beforeCap).some((c) => c.startsWith('PATCH /agent/capabilities'))
      check('切换能力开关真的调用了 PATCH API', patchedCap)
      await toggles.first().click()
      await page.waitForTimeout(1200)
    } else {
      check('切换能力开关真的调用了 PATCH API', false, '未找到开关')
    }

    // --------------------------------------------------------- responsive --
    for (const [w, h, label] of [
      [1280, 720, '1280x720'],
      [2560, 1440, '2560x1440'],
    ]) {
      await page.setViewportSize({ width: w, height: h })
      await page.goto(`${BASE}/agent`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1200)
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 4,
      )
      check(`${label} 无横向溢出`, !overflow)
      await page.screenshot({ path: join(SHOTS, `07-agent-${label}.png`) })
    }

    // ------------------------------------------------------ console clean --
    const realErrors = consoleErrors.filter(
      (e) => !/favicon|Download the React DevTools|ResizeObserver/i.test(e),
    )
    check('无 JS 运行时错误', realErrors.length === 0, realErrors.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error.png') }).catch(() => {})
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(72))
  console.log(`  ${results.length - failed.length}/${results.length} 项通过`)
  if (failed.length) {
    console.log('  失败项：')
    for (const f of failed) console.log(`    - ${f.name}${f.detail ? ` (${f.detail})` : ''}`)
  }
  console.log('='.repeat(72))
  process.exit(failed.length ? 1 : 0)
}

main()
