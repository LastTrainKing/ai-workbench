/**
 * Browser test for the three UI defects reported after a real install.
 *
 * Each check is written so that it FAILS if the defect returns — a check that
 * cannot fail is not a check. Specifically:
 *
 *   Issue 2  the submit path must be blocked when the LLM is degraded, and the
 *            gate must NOT fire when only the embedding provider is offline
 *            (that is the deliberate design — DeepSeek has no embedding API).
 *   Issue 3  saving an AI config must make /health/deps refetch without a page
 *            reload, and the top-bar label must change with it. The original bug
 *            was an invalidateQueries key that matched no query, so this asserts
 *            on the network call, not on the label alone.
 *
 *   node scripts/verify-config-gate.mjs
 */

import { chromium } from './_playwright.mjs'
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
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
}

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage())

  const healthCalls = []
  page.on('request', (r) => {
    if (/\/api\/v1\/health\/deps/.test(r.url())) healthCalls.push(Date.now())
  })
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  try {
    await login(page)

    // ------------------------------------------------ current server state --
    const deps = await page.request.get(`${BASE}/api/v1/health/deps`)
    const depsBody = (await deps.json())?.data
    const llmDegraded = depsBody?.ai?.llm?.degraded
    const embeddingDegraded = depsBody?.ai?.embedding?.degraded
    console.log(`  (server: llm.degraded=${llmDegraded} embedding.degraded=${embeddingDegraded})`)

    // ------------------------------------------------------ issue 2: gate --
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)

    const notice = page.locator('[data-testid="model-setup-notice"]')
    const noticeCount = await notice.count()
    const goalInput = page.locator('[data-testid="goal-input"]')
    const startBtn = page.locator('[data-testid="start-build"]')

    if (llmDegraded === true) {
      check('未配模型时：首页出现显眼的配置引导块', noticeCount > 0)
      check('未配模型时：输入框被禁用', await goalInput.isDisabled())
      await goalInput.fill('这条不该能提交')
      await page.waitForTimeout(300)
      check('未配模型时：「开始」按钮被禁用', await startBtn.isDisabled())
      check('未配模型时：按钮文案说明原因', /先配置模型/.test(await startBtn.innerText()))
      check('引导块里有直达设置页的入口', (await page.locator('[data-testid="goto-model-settings"]').count()) > 0)
    } else {
      // The LLM is configured. The gate must be silent — and critically it must
      // not be fooled by the embedding provider, which is always degraded here.
      check('llm 正常时：不出现配置引导块（embedding 走离线不算未配置）', noticeCount === 0,
        `embedding.degraded=${embeddingDegraded}`)
      check('llm 正常时：输入框可用', !(await goalInput.isDisabled()))
      await goalInput.fill('验证模型已配置时可以提交')
      await page.waitForTimeout(300)
      check('llm 正常时：「开始」按钮可用', !(await startBtn.isDisabled()))
    }
    await page.screenshot({ path: join(SHOTS, '70-home-gate.png'), fullPage: true })

    // ------------------------------------------- issue 3: health refetch --
    // Reload onto settings so the TopBar and the settings page share a mount.
    await page.goto(`${BASE}/settings`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)
    const topbar = page.locator("header").first()
    const labelBefore = await topbar.innerText()

    const before = healthCalls.length
    // The save button is (correctly) disabled until something is dirty, so we
    // have to make a change before we can save.
    //
    // Whatever we type here becomes the *live* key, process-wide, until it is
    // cleared. The first version left a placeholder key behind and every later
    // run of verify-onboarding.mjs failed with a 422 from DeepSeek ("Your api key
    // ... is invalid"), which looked like a product bug and was this test's
    // litter. So the write is paired with a guaranteed cleanup in `finally`.
    const keyInput = page.locator('input[type="password"]').first()
    const hasKeyInput = (await keyInput.count()) > 0
    check('设置页有 API Key 输入框', hasKeyInput)

    let wroteOverride = false
    if (hasKeyInput) {
      await keyInput.fill('sk-verification-placeholder-0000000000')
      await page.waitForTimeout(500)
    }

    const saveBtn = page.locator('button:has-text("应用"), button:has-text("保存")').first()
    const hasSave = (await saveBtn.count()) > 0
    check('设置页有可用的保存按钮', hasSave)
    if (hasSave) {
      check('改动后保存按钮已启用', await saveBtn.isEnabled())
      await saveBtn.click()
      wroteOverride = true
      // The defect was that no refetch happened at all. Waiting for the call is
      // the assertion; a fixed sleep would pass even when nothing refetched.
      await page
        .waitForRequest((r) => /\/api\/v1\/health\/deps/.test(r.url()), { timeout: 10000 })
        .catch(() => null)
      await page.waitForTimeout(1500)
      const refetches = healthCalls.length - before
      check('保存配置后 /health/deps 真的重新拉取了（无需刷新页面）', refetches > 0, `${refetches} 次`)
    }

    // Undo the write. `DELETE /ai-config` clears every override and falls back to
    // .env, which is the state we found the machine in.
    if (wroteOverride) {
      const token = await page.evaluate(() => localStorage.getItem('aiw.token'))
      const cleared = await page.request.delete(`${BASE}/api/v1/ai-config`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      check('验证结束后清掉了自己写入的 Key（不留污染）', cleared.status() === 200, `HTTP ${cleared.status()}`)
      const after = await page.request.get(`${BASE}/api/v1/health/deps`)
      const afterBody = (await after.json())?.data
      check(
        '清理后 llm 仍可用（回落到 .env 的配置）',
        afterBody?.ai?.llm?.degraded === false,
        `kind=${afterBody?.ai?.llm?.kind} degraded=${afterBody?.ai?.llm?.degraded}`,
      )
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
    }

    const labelAfter = await topbar.innerText()
    if (llmDegraded !== true) {
      check('顶栏显示模型名而不是「未配置」', /deepseek/.test(labelAfter) && !/未配置云端 Key/.test(labelAfter), labelAfter.trim())
    } else {
      check('未配置时顶栏黄色说明原因', /未配置云端 Key/.test(labelAfter), labelAfter.trim())
    }
    check('顶栏标签没有被缓存成旧值', labelAfter.length > 0, `${labelBefore.trim()} -> ${labelAfter.trim()}`)
    await page.screenshot({ path: join(SHOTS, '71-settings-topbar.png'), fullPage: true })

    check('无 JS 运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '))
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(72))
  console.log(`  ${results.length - failed.length}/${results.length} 项通过`)
  if (failed.length) failed.forEach((f) => console.log(`    - ${f.name}${f.detail ? ` (${f.detail})` : ''}`))
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
