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

import { chromium } from './_playwright.mjs'
import { requireModelOrSkip } from './_model_guard.mjs'

// 要真跑一次对话的脚本：没有模型就跳过（exit 3），不许假装通过。
await requireModelOrSkip('verify-delivery-ui.mjs')

import { startThroughConversation } from './_conversation.mjs'
import { copyFileSync, existsSync, mkdirSync, statSync } from 'node:fs'
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

    // ------------------------------------------------------ the conversation --
    // `/` is the conversation now, and the four-area home moved to `/home`
    // unchanged. Both are checked, in that order, because "the old screen is gone"
    // and "the old screen still exists" are different claims and only one of them
    // is acceptable.
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const conversation = await page.locator('body').innerText()

    check('对话页：有时间线', /你想完成的事|告诉它你想做什么/.test(conversation))
    check('对话页：有输入框', (await page.locator('[data-testid="goal-input"]').count()) > 0)
    check('对话页不含状态码', !/DRAFT|PARTIAL|LIMIT_REACHED/.test(conversation))

    await page.goto(`${BASE}/home`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)
    const home = await page.locator('body').innerText()

    check('① 区域「你想让我完成什么」存在（旧四区首页仍在 /home）', /你想让我完成什么/.test(home))
    check('② 区域「正在处理」存在', /正在处理/.test(home))
    check('③ 区域「最近产出」存在', /最近产出/.test(home))
    check('④ 区域「它学到了什么」存在', /它学到了什么/.test(home))

    check('资料范围可见', /用哪些资料/.test(home))
    check('产出格式可选', /产出格式/.test(home) && /PPT/.test(home))
    await page.screenshot({ path: join(SHOTS, '60-home.png'), fullPage: true })

    // -------------------------------------------------------- create a run --
    // Submitted from the conversation, because that is where a person does it now. The
    // understanding layer may answer, may ask, or may start — the helper answers if it
    // asks, which is the supported "stop asking, get on with it" path rather than a way
    // of forcing the test past the feature.
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const goalInput = page.locator('[data-testid="goal-input"]')
    check('目标输入框存在', (await goalInput.count()) > 0)

    // The format chips are behind 「选项」 — one click, not a removal.
    await page.locator('[data-testid="toggle-options"]').click()
    await page.waitForTimeout(300)
    check('产出格式仍可选（在「选项」里）', (await page.locator('[data-testid="format-pptx"]').count()) > 0)
    await page.locator('[data-testid="toggle-options"]').click()
    await page.waitForTimeout(200)

    const goalText = `用我的资料做一份 15 分钟的答辩 PPT（${Date.now()}）`
    const before = calls.length
    const { conversationId, runId: runIdForStatus, asked } = await startThroughConversation(
      page,
      BASE,
      goalText,
    )
    check('说一句话之后真的开工了', Boolean(runIdForStatus), `run=${runIdForStatus} 先问过=${asked}`)

    // It stays inside a conversation. Navigating away to a detail page is what the old
    // screen did, and the whole point of this one is that the work shows up where you
    // asked for it.
    check(
      '提交后留在对话里（产出会出现在这条对话里）',
      new URL(page.url()).pathname.startsWith('/c/'),
      page.url(),
    )

    // Create and start are **one action**, and since this round they are one request too:
    // the conversation endpoint creates the run and starts it server-side, through the
    // single implementation of "start a run". So the client must *not* be making two calls
    // — that split is what let the frontend call one of them and leave a run in DRAFT
    // while the screen looked like work in progress.
    //
    // The old shape of this check ("POST /build-runs then POST .../execute") is gone on
    // purpose rather than deleted quietly: one user action producing two client requests
    // was the bug, not the requirement.
    const seq = calls.slice(before)
    check(
      '开工只发了发消息这一个请求（创建与开始是同一个动作）',
      seq.some((c) => /^POST \/conversations\/[^/]+\/messages$/.test(c)),
      seq.slice(0, 6).join(' | '),
    )
    check(
      '客户端没有再分两步去 create / execute',
      !seq.some((c) => c === 'POST /build-runs') &&
        !seq.some((c) => /^POST \/build-runs\/[^/]+\/execute$/.test(c)),
      seq.filter((c) => c.startsWith('POST /build-runs')).join(' | ') || '(没有)',
    )

    // The real failure the user hit: the row stays DRAFT. Read it back from the API
    // rather than trusting the UI, because the UI is what lied.
    const loginEarly = await page.request.post(`${BASE}/api/v1/auth/login`, {
      data: { identifier: 'demo', password: 'demo1234' },
    })
    const tokenEarly = (await loginEarly.json())?.data?.access_token
    const statusResponse = await page.request.get(`${BASE}/api/v1/build-runs/${runIdForStatus}`, {
      headers: { Authorization: `Bearer ${tokenEarly}` },
    })
    const statusBody = await statusResponse.json().catch(() => null)
    const runStatus = statusBody?.data?.status
    check(
      '提交后的 run 不再是 DRAFT',
      runStatus && runStatus !== 'DRAFT',
      `status=${runStatus} started_at=${statusBody?.data?.started_at ?? '(空)'}`,
    )
    check(
      'run 有 started_at（真的开始过）',
      Boolean(statusBody?.data?.started_at),
      String(statusBody?.data?.started_at ?? '(空)'),
    )

    // The turn is in the conversation, with its goal, without going anywhere.
    await page.goto(`${BASE}/c/${conversationId}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const conversationAfter = await page.locator('body').innerText()
    check('新的一轮出现在对话时间线上', conversationAfter.includes('答辩 PPT'))
    await page.screenshot({ path: join(SHOTS, '60b-conversation-submitted.png'), fullPage: true })

    // ------------------------------------------------------------ workbench --
    await page.goto(`${BASE}/workbench/${runIdForStatus}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const workbenchVisible = await page.locator('body').innerText()
    check('工作台：产出物栏', /产出物/.test(workbenchVisible))
    check('工作台显示目标原文', /答辩 PPT/.test(workbenchVisible))

    const details = page.locator('[data-testid="run-details"]')
    check('工作台：详情区存在', (await details.count()) > 0)
    if ((await details.count()) > 0) {
      // It is open by default while a run is EXECUTING, so clicking unconditionally
      // would toggle it *shut* and fail the next assertions for the wrong reason.
      const open = await page
        .locator('details:has([data-testid="run-details"])')
        .evaluate((el) => el.hasAttribute('open'))
      if (!open) {
        await details.click()
        await page.waitForTimeout(600)
      }
    }
    const workbench = await page.locator('body').innerText()
    check('详情里：执行计划栏', /执行计划/.test(workbench))
    check('详情里：实时执行栏', /实时执行/.test(workbench))
    check('详情里：这次依据了什么栏', /这次依据了什么/.test(workbench))
    await page.screenshot({ path: join(SHOTS, '61-workbench.png'), fullPage: true })

    // ---------------------------------------------- register a deliverable --
    // The agent has not run for this BuildRun, so a file is attached through the
    // API the same way the execution path would, then verified in the UI.
    const runId = runIdForStatus
    check('拿到 BuildRun id', Boolean(runId), runId)

    // Obtain a token the way any client would, rather than reaching into the
    // app's persisted store — that store's shape is an implementation detail, and
    // a test that depends on it breaks every time the store is refactored.
    const login = await page.request.post(`${BASE}/api/v1/auth/login`, {
      data: { identifier: 'demo', password: 'demo1234' },
    })
    const token = (await login.json())?.data?.access_token
    check('测试取到 API token', Boolean(token))

    // The workspace lives inside the project now, not on the desktop.
    // The file has to exist: `record_deliverable` verifies the path before it registers
    // anything, because a deliverable row that was never read back is a claim rather than
    // a file. The first version of this test assumed a fixture that is not in the repo, so
    // every check after it failed with a 422 that said nothing about the real cause.
    const demoDir = [process.cwd(), '.runtime', 'workspace', 'deliverables'].join('/')
    const demoPath = `${demoDir}/ui-check.pptx`
    mkdirSync(demoDir, { recursive: true })
    // A **real** pptx, not a text file with a .pptx name. The first version wrote a
    // placeholder string, and the readback check correctly rejected it with
    // `PackageNotFoundError` — the verification was right and the fixture was fake.
    // Copying a genuine file is what makes "the deliverable was read back" mean anything.
    const fixture = join(HERE, 'fixtures', 'ui-check.pptx')
    copyFileSync(fixture, demoPath)
    check('演示文件是真实的 pptx', existsSync(demoPath), `${demoPath} (${statSync(demoPath).size} B)`)

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
    // The detail has to describe what was actually found. The first version said
    // "N 项检查，全部通过" unconditionally, so a failing run reported a pass in its own
    // failure message — the check was right and its explanation was a lie.
    const failedChecks = (verification?.checks || []).filter((c) => !c.ok)
    check(
      '产出物经过机器校验',
      Boolean(verification?.passed),
      verification
        ? `${verification.check_count} 项检查，失败 ${verification.failed_count}` +
          (failedChecks.length ? `：${failedChecks.map((c) => `${c.name}(${c.detail})`).join('; ')}` : '')
        : 'no verification',
    )

    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const withFile = await page.locator('body').innerText()
    check('产出物出现在工作台', /答辩PPT v1/.test(withFile))
    check('显示验证证据', /验证证据/.test(withFile))
    // The badge now reads as a sentence rather than a pill — same fact, said the
    // way the conversation says it.
    check('显示文件已通过机器校验', /可以打开|已机器校验/.test(withFile), (withFile.match(/可以打开|已机器校验/) ?? ['(无)'])[0])

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
    // Wait for the *response*, not a duration. Rejecting runs the memory evaluator
    // synchronously — one real model call — so the round trip is tens of seconds and
    // a fixed 6s sleep read the page while the textarea still held the typed reason.
    // The old assertion passed on that text by accident and failed on the state it
    // was actually checking.
    const rejectResponse = page.waitForResponse(
      (r) => /\/api\/v1\/deliverables\/[^/]+\/reject$/.test(r.url()) && r.request().method() === 'POST',
      { timeout: 90000 },
    )
    await confirmReject.click()
    const rejectedResponse = await rejectResponse.catch(() => null)
    check('退回真的调用了 reject 端点', calls.slice(beforeReject).some((c) => c.includes('/reject')), calls.slice(beforeReject).slice(0, 3).join(' | '))
    check('退回接口返回成功', rejectedResponse?.status() === 200, `HTTP ${rejectedResponse?.status() ?? '无响应'}`)

    // And wait for the card to actually show it, rather than for a guess about how
    // long the refetch takes.
    await page
      .locator('[data-testid="deliverable-card"]')
      .filter({ hasText: '你已退回' })
      .first()
      .waitFor({ timeout: 20000 })
      .catch(() => null)

    const afterReject = await page.locator('body').innerText()
    check('界面显示已退回', /你已退回|已退回/.test(afterReject))
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
