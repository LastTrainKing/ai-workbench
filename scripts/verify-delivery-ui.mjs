/**
 * Browser test for the delivery loop.
 *
 * The claim being checked is the product's core one: a person can state a goal,
 * watch it be worked on, look at what came out, and accept or reject it — and
 * rejecting produces a belief the system then holds.
 *
 * Asserting on requests rather than on rendering, because the failure this catches
 * is a button wired to local state instead of to the API. That passes typecheck,
 * lint and build, and only a real click finds it.
 *
 *   node scripts/verify-delivery-ui.mjs
 */

import { chromium } from 'playwright-core'
import { mkdirSync, existsSync } from 'node:fs'
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
  const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage())

  const calls = []
  page.on('request', (r) => {
    const url = r.url()
    if (url.includes('/api/v1/')) calls.push(`${r.method()} ${url.split('/api/v1')[1].split('?')[0]}`)
  })
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  try {
    // ---------------------------------------------------------------- login --
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))
    check('登录成功', true)

    // ------------------------------------------------------------ home page --
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)
    const home = await page.locator('body').innerText()

    check('① 区域「你想让我完成什么」存在', /你想让我完成什么/.test(home))
    check('② 区域「正在处理」存在', /正在处理/.test(home))
    check('③ 区域「最近产出」存在', /最近产出/.test(home))
    check('④ 区域「它学到了什么」存在', /它学到了什么/.test(home))

    check('资料范围可见', /用哪些资料/.test(home))
    check('产出格式可选', /产出格式/.test(home) && /PPT/.test(home))

    const goalInput = page.locator('[data-testid="goal-input"]')
    check('目标输入框存在', (await goalInput.count()) > 0)
    await page.screenshot({ path: join(SHOTS, '60-home.png'), fullPage: true })

    // -------------------------------------------------------- create a run --
    await goalInput.fill('用我的资料做一份 15 分钟的答辩 PPT')
    await page.locator('[data-testid="format-pptx"]').click()
    const before = calls.length
    await page.locator('[data-testid="start-build"]').click()
    await page.waitForTimeout(4000)

    const posted = calls.slice(before).some((c) => c === 'POST /build-runs')
    check('点击「开始」真的创建了 BuildRun', posted, calls.slice(before).slice(0, 3).join(' | '))
    check('提交后跳到工作台', page.url().includes('/workbench/'), page.url())

    // ------------------------------------------------------------ workbench --
    await page.waitForTimeout(2500)
    const workbench = await page.locator('body').innerText()
    check('工作台：执行计划栏', /执行计划/.test(workbench))
    check('工作台：实时执行栏', /实时执行/.test(workbench))
    check('工作台：本次上下文栏', /本次上下文/.test(workbench))
    check('工作台：产出物栏', /产出物/.test(workbench))
    check('工作台显示目标原文', /答辩 PPT/.test(workbench))
    await page.screenshot({ path: join(SHOTS, '61-workbench.png'), fullPage: true })

    // ---------------------------------------------- register a deliverable --
    // The agent has not run for this BuildRun, so a file is attached through the
    // API the same way the execution path would, then verified in the UI.
    const runId = page.url().split('/workbench/')[1]
    check('拿到 BuildRun id', Boolean(runId), runId)

    // Obtain a token the way any client would, rather than reaching into the
    // app's persisted store — that store's shape is an implementation detail, and
    // a test that depends on it breaks every time the store is refactored.
    const login = await page.request.post(`${BASE}/api/v1/auth/login`, {
      data: { identifier: 'demo', password: 'demo1234' },
    })
    const token = (await login.json())?.data?.access_token
    check('测试取到 API token', Boolean(token))

    const demoPath = [
      'C:',
      'Users',
      'user',
      'Desktop',
      'AI Workbench Workspace',
      'doc-demo',
      'demo.pptx',
    ].join('/')

    const attachResponse = await page.request.post(
      `${BASE}/api/v1/build-runs/${runId}/deliverables`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { path: demoPath, kind: 'pptx', title: '答辩PPT v1' },
      },
    )
    const attach = {
      status: attachResponse.status(),
      body: await attachResponse.json().catch(() => null),
    }

    check('登记产出物成功', attach.status === 201, `HTTP ${attach.status}`)
    const verification = attach.body?.data?.verification
    check(
      '产出物经过机器校验',
      Boolean(verification?.passed),
      verification ? `${verification.check_count} 项检查，全部通过` : 'no verification',
    )

    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const withFile = await page.locator('body').innerText()
    check('产出物出现在工作台', /答辩PPT v1/.test(withFile))
    check('显示验证证据', /验证证据/.test(withFile))
    check('显示「已机器校验」', /已机器校验/.test(withFile))

    const checks = page.locator('[data-testid="verification-checks"]')
    check('验证证据是结构化列表', (await checks.count()) > 0)
    const checkText = (await checks.count()) > 0 ? await checks.first().innerText() : ''
    check('证据含 file_exists', /file_exists/.test(checkText), checkText.replace(/\n/g, ' | ').slice(0, 80))
    check('证据含 readback', /readback/.test(checkText))
    await page.screenshot({ path: join(SHOTS, '62-workbench-deliverable.png'), fullPage: true })

    // ------------------------------------------------------------- reject ---
    const startReject = page.locator('[data-testid="start-reject"]')
    check('存在「退回」按钮', (await startReject.count()) > 0)
    await startReject.click()
    await page.waitForTimeout(500)

    const reason = page.locator('[data-testid="reject-reason"]')
    check('退回时要求填写原因', (await reason.count()) > 0)

    const confirmReject = page.locator('[data-testid="confirm-reject"]')
    const disabledWhenEmpty = await confirmReject.isDisabled()
    check('原因为空时确认按钮禁用', disabledWhenEmpty)

    await reason.fill('图表太少，页数也偏多')
    await page.waitForTimeout(300)
    const beforeReject = calls.length
    await confirmReject.click()
    await page.waitForTimeout(6000)

    const rejected = calls.slice(beforeReject).some((c) => c.includes('/reject'))
    check('退回真的调用了 reject 端点', rejected, calls.slice(beforeReject).slice(0, 3).join(' | '))

    const afterReject = await page.locator('body').innerText()
    check('界面显示已退回', /已退回/.test(afterReject))
    check('界面回显退回原因', /图表太少/.test(afterReject))
    await page.screenshot({ path: join(SHOTS, '63-workbench-rejected.png'), fullPage: true })

    // ------------------------------------------- the loop actually learned --
    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)
    const mePage = await page.locator('body').innerText()
    const learnedSomething = /图表|页数|标准|偏好/.test(mePage)
    check('记忆页能看到由退回产生的偏好', learnedSomething, learnedSomething ? 'found' : 'not found')

    // ----------------------------------------------------- legacy routes ----
    const routes = [
      '/', '/workbench', '/dashboard', '/me', '/welcome', '/agent', '/projects',
      '/tasks', '/planner', '/knowledge', '/documents', '/search', '/chat',
      '/notes', '/ai-runs', '/graph', '/settings', '/settings/agent',
    ]
    let ok = 0
    for (const route of routes) {
      const response = await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded' })
      if (response && response.status() === 200) ok += 1
    }
    check(`全部 ${routes.length} 条路由可达`, ok === routes.length, `${ok}/${routes.length}`)

    const real = errors.filter((e) => !/favicon|ResizeObserver/i.test(e))
    check('无 JS 运行时错误', real.length === 0, real.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 180))
    await page.screenshot({ path: join(SHOTS, 'error-delivery.png') }).catch(() => {})
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
