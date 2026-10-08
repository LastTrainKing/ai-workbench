/**
 * Browser test for the conversation screen — the only screen most people need.
 *
 * The acceptance this exists for, in the user's own words: a person who has never seen the
 * product should be able to say, unprompted, where they are supposed to type. That is not
 * something typecheck or build can tell you, and it is not something that can be asserted
 * from the DOM being merely *present* — a card of inputs in the middle of a dashboard is
 * present too. So the checks are positional and verbal:
 *
 *   * the input is in the lower part of the viewport, on arrival, without scrolling
 *   * the page says what to do in it
 *   * the timeline holds what was said, what happened, and what came out
 *   * the machinery is folded, and still there when unfolded
 *   * nothing on the screen is a status code or an engine-room word
 *
 * It gets to a running build the way a person does — through the conversation, answering
 * if it asks (`_conversation.mjs`). Whether it asks is a real model's decision, and the
 * test accepts either answer rather than assuming one.
 *
 *   node scripts/verify-conversation-ui.mjs
 */

import { chromium } from './_playwright.mjs'
import { requireModelOrSkip } from './_model_guard.mjs'

// 要真跑一次对话的脚本：没有模型就跳过（exit 3），不许假装通过。
await requireModelOrSkip('verify-conversation-ui.mjs')

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

//: Words that mean nothing to the person this screen is for. Any of them appearing
//: is the old interface leaking back in.
const JARGON = /RAG|向量化|\btier\b|工具注册表|DRAFT|PLANNING|EXECUTING|VERIFYING|PARTIAL|LIMIT_REACHED/

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()

  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  const consoleErrors = []
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))

    // ------------------------------------------------------- where do I type --
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)

    const input = page.locator('[data-testid="goal-input"]')
    check('首页有一个输入框', (await input.count()) === 1, `${await input.count()} 个`)

    const box = await input.boundingBox()
    const viewport = page.viewportSize()
    // Bottom third of the screen, and on screen without scrolling. This is the whole
    // claim: a chat box you have to go looking for is not a chat box.
    const inLowerPart = box ? box.y + box.height > viewport.height * 0.6 : false
    check(
      '输入框在屏幕下方（像聊天工具，不用找）',
      inLowerPart,
      box ? `y=${Math.round(box.y)} h=${Math.round(box.height)} viewport=${viewport.height}` : '未找到',
    )
    check('输入框在首屏可见', box ? box.y + box.height <= viewport.height : false)

    const placeholder = await input.getAttribute('placeholder')
    check('输入框自己说清了要写什么', Boolean(placeholder && placeholder.length > 5), placeholder)

    const sendLabel = await page.locator('[data-testid="start-build"]').innerText()
    check('有一个明确的发送按钮', /发送|配置/.test(sendLabel), sendLabel)

    // -------------------------------------------------- the sidebar is a list --
    const rail = await page.locator('aside').first().innerText()
    check('侧栏是会话列表，不是菜单树', /新对话/.test(rail), rail.replace(/\n/g, ' | ').slice(0, 70))
    check('侧栏不列出工作台/执行记录', !/工作台|执行记录/.test(rail))
    await page.screenshot({ path: join(SHOTS, '80-conversation.png'), fullPage: true })

    // ------------------------------------------------- get to a running build --
    const { conversationId, runId } = await startThroughConversation(
      page,
      BASE,
      '用我的资料做一份 15 分钟的答辩 PPT，只要目录，每个阶段带两三个例子，给我自己看',
    )
    check('说一句话之后真的开工了', Boolean(runId), `conversation=${conversationId} run=${runId}`)
    await page.goto(`${BASE}/c/${conversationId}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)

    // ------------------------------------------------------------- timeline ---
    const turns = await page.locator('[data-testid="conversation-turn"]').count()
    check('时间线上有对话轮次', turns > 0, `${turns} 轮`)

    const body = await page.locator('body').innerText()
    check('页面不含状态码与工程术语', !JARGON.test(body), (body.match(JARGON) ?? []).join(','))
    check('它说的话在同一条时间线上', /答辩 PPT/.test(body))

    const status = await page.locator('[data-testid="turn-status"]').first().innerText()
    check('状态是一句话，不是一个码', !JARGON.test(status) && status.length > 2, status.slice(0, 40))

    // ------------------------------------- a file shows up inside the turn ----
    if (runId) {
      const dir = [process.cwd(), '.runtime', 'workspace', 'deliverables'].join('/')
      const target = `${dir}/conversation-check.pptx`
      mkdirSync(dir, { recursive: true })
      // A **real** pptx: `record_deliverable` reads the file back before registering it,
      // so a text file with a .pptx name is correctly refused and would prove nothing.
      copyFileSync(join(HERE, 'fixtures', 'ui-check.pptx'), target)
      check('演示文件是真实的 pptx', existsSync(target), `${statSync(target).size} B`)

      const token = await page.evaluate(() => localStorage.getItem('aiw.token'))
      const attached = await page.request.post(`${BASE}/api/v1/build-runs/${runId}/deliverables`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { path: target, kind: 'pptx', title: '答辩PPT v1' },
      })
      check('登记产出物成功', attached.status() === 201, `HTTP ${attached.status()}`)

      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForTimeout(2500)

      const inlineCards = await page
        .locator('[data-testid="conversation-turn"] [data-testid="deliverable-card"]')
        .count()
      check('产出物长在对话里（不是另一个页面）', inlineCards > 0, `${inlineCards} 张`)

      // 接受 / 退回 must stay reachable in the conversation: this is the loop the product
      // exists for, and hiding it behind a click would be the one change that made this
      // redesign a regression.
      const hasAccept = await page.locator('[data-testid="accept-deliverable"]').count()
      const hasReject = await page.locator('[data-testid="start-reject"]').count()
      const alreadyDecided = await page.locator('text=你已验收').count()
      check(
        '验收与退回就在对话里',
        hasAccept > 0 || hasReject > 0 || alreadyDecided > 0,
        `接受 ${hasAccept} · 退回 ${hasReject} · 已决定 ${alreadyDecided}`,
      )
      await page.screenshot({ path: join(SHOTS, '81-conversation-deliverable.png'), fullPage: true })
    } else {
      check('产出物长在对话里（不是另一个页面）', false, '没有拿到 runId')
      check('验收与退回就在对话里', false, '没有拿到 runId')
    }

    // ------------------------------------------------------------ 详情折叠 ----
    const fold = page.locator('details:has([data-testid="run-details"])').first()
    check('有「详情」折叠区', (await fold.count()) > 0)
    if ((await fold.count()) > 0) {
      const open = await fold.evaluate((el) => el.hasAttribute('open')).catch(() => null)
      // A run that is executing opens by default on purpose — that is the moment somebody
      // is watching. `verify-workbench-layout.mjs` covers the settled-run case.
      if (!open) {
        await page.locator('[data-testid="run-details"]').first().click()
        await page.waitForTimeout(800)
      }
      // innerText on a collapsed <details> still returns the content — it is in the DOM,
      // just not laid out. Assert on what Playwright says is visible.
      // 执行计划这一栏现在只在真的有计划时渲染。空状态「还没有生成计划」占了地方
      // 却不告诉用户任何事，用户明确要求删掉 —— 所以这里检查的是「有内容就显示，
      // 没内容就整栏不出现」，而不是「这一栏永远在」。
      const planVisible = await page.locator('[data-testid="panel-plan"]').first().isVisible().catch(() => false)
      const emptyPlan = (await page.locator('[data-testid="message-stream"]').innerText()).includes('还没有生成计划')
      check('「详情」里执行计划要么有步骤、要么整栏不出现', planVisible || !emptyPlan, `plan=${planVisible} 空占位=${emptyPlan}`)
      check('「详情」里有实时执行', await page.locator('[data-testid="panel-execution"]').first().isVisible().catch(() => false))
      check('「详情」里有「这次依据了什么」', await page.locator('[data-testid="panel-context"]').first().isVisible().catch(() => false))
      await page.screenshot({ path: join(SHOTS, '82-conversation-details.png'), fullPage: true })
    } else {
      check('「详情」里执行计划要么有步骤、要么整栏不出现', false, '没有找到详情折叠')
      check('「详情」里有实时执行', false, '没有找到详情折叠')
      check('「详情」里有「这次依据了什么」', false, '没有找到详情折叠')
    }

    // ----------------------------------------------------------- old routes ---
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

    // ------------------------------------------------------ the old home too ---
    await page.goto(`${BASE}/home`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const oldHome = await page.locator('body').innerText()
    check('上一版的四区首页仍然在（没有被删掉）', /最近产出/.test(oldHome) && /它学到了什么/.test(oldHome))

    const real = consoleErrors.filter((e) => !/favicon|DevTools|ResizeObserver/i.test(e))
    check('无 JS 运行时错误', errors.length === 0 && real.length === 0, [...errors, ...real].slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error-conversation.png') }).catch(() => {})
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
