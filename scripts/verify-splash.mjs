/**
 * Splash screen test: does it render per-service readiness from a real payload?
 *
 * The splash is a static page inside the desktop shell, so it cannot be reached
 * over HTTP like the application. It is loaded from disk with a stub for the
 * Tauri event API, then fed the same payload shape the shell emits.
 *
 * What this proves: given a status payload, the splash renders one row per
 * service with the right state. What it does not prove: that the shell emits
 * that payload — that is `probe_services()` in backend.rs, which performs real
 * TCP connects and an HTTP health check.
 *
 *   node scripts/verify-splash.mjs
 */

import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'ui-verification')
const SPLASH = join(HERE, '..', 'desktop', 'splash', 'index.html')

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newContext({ viewport: { width: 900, height: 600 } }).then((c) => c.newPage())

  try {
    // Stub the Tauri bridge before the page script runs, so the splash's
    // `api.event.listen` registration lands on our dispatcher.
    await page.addInitScript(() => {
      window.__awListeners = {}
      window.__TAURI_INTERNALS__ = {
        invoke: async () => ({ ok: false, message: 'ui-loading' }),
        transformCallback: (cb) => cb,
      }
      window.__TAURI__ = {
        event: {
          listen: async (name, handler) => {
            window.__awListeners[name] = handler
            return () => {}
          },
        },
        core: { invoke: async () => ({ ok: false, message: 'ui-loading' }) },
      }
    })

    await page.goto(pathToFileURL(SPLASH).href, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(800)

    check('闪屏加载', (await page.locator('body').innerText()).length > 0)

    const hasList = await page.locator('#services').count()
    check('存在逐项服务列表容器', hasList === 1)

    // Feed a mid-startup payload: two services up, one not yet, API not yet.
    await page.evaluate(() => {
      const handler = window.__awListeners['startup://status']
      handler({
        payload: {
          phase: 'launching',
          message: 'Starting PostgreSQL, Redis, Qdrant and the API...',
          detail: '',
          ok: false,
          finished: false,
          services: [
            { name: 'PostgreSQL', state: 'ready', detail: '127.0.0.1:5432' },
            { name: 'Redis', state: 'ready', detail: '127.0.0.1:6379' },
            { name: 'Qdrant', state: 'pending', detail: '127.0.0.1:6333' },
            { name: 'API', state: 'pending', detail: 'http://127.0.0.1:8000' },
          ],
        },
      })
    })
    await page.waitForTimeout(400)

    const rows = page.locator('#services li')
    const count = await rows.count()
    check('渲染出 4 个服务行', count === 4, `实际 ${count}`)

    if (count === 4) {
      const states = await rows.evaluateAll((nodes) => nodes.map((n) => n.dataset.state))
      check(
        '状态与载荷一致（2 ready / 2 pending）',
        states.join(',') === 'ready,ready,pending,pending',
        states.join(','),
      )
      const text = await page.locator('#services').innerText()
      check('显示服务名', /PostgreSQL/.test(text) && /Redis/.test(text) && /Qdrant/.test(text) && /API/.test(text))
      check('显示 READY 标签', /READY/.test(text))
      await page.screenshot({ path: join(SHOTS, '30-splash-starting.png') })
    }

    // Then the finished payload: everything ready.
    await page.evaluate(() => {
      const handler = window.__awListeners['startup://status']
      handler({
        payload: {
          phase: 'ready',
          message: 'Ready',
          detail: 'http://127.0.0.1:8000',
          ok: true,
          finished: true,
          services: [
            { name: 'PostgreSQL', state: 'ready', detail: '127.0.0.1:5432' },
            { name: 'Redis', state: 'ready', detail: '127.0.0.1:6379' },
            { name: 'Qdrant', state: 'ready', detail: '127.0.0.1:6333' },
            { name: 'API', state: 'ready', detail: 'http://127.0.0.1:8000' },
          ],
        },
      })
    })
    await page.waitForTimeout(400)

    const finalStates = await page
      .locator('#services li')
      .evaluateAll((nodes) => nodes.map((n) => n.dataset.state))
    check('全部就绪时 4 行都是 ready', finalStates.every((s) => s === 'ready'), finalStates.join(','))
    await page.screenshot({ path: join(SHOTS, '31-splash-ready.png') })

    // A failure payload must show the reason and the log path, not a blank card.
    await page.evaluate(() => {
      const handler = window.__awListeners['startup://status']
      handler({
        payload: {
          phase: 'failed',
          message: 'PostgreSQL did not start',
          detail: 'see .runtime/logs/startup-error.log',
          ok: false,
          finished: true,
          services: [{ name: 'PostgreSQL', state: 'failed', detail: '127.0.0.1:5432' }],
        },
      })
    })
    await page.waitForTimeout(400)
    const errorText = await page.locator('#error').innerText()
    check('失败时显示原因', /PostgreSQL did not start/.test(errorText))
    check('失败时显示日志路径', /startup-error\.log/.test(errorText))
    const errorVisible = await page.locator('#error').evaluate((n) => n.classList.contains('show'))
    check('失败面板真的显示出来', errorVisible)
    await page.screenshot({ path: join(SHOTS, '32-splash-failed.png') })
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
