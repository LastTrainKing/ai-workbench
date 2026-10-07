/**
 * Browser test for 下载 and 打开 — the two buttons that did nothing.
 *
 * Both were silent failures, which is why neither could be caught by typecheck,
 * lint or build:
 *
 *   下载   `<a href={downloadUrl(id)} download>` — a navigation carries no
 *          `Authorization` header, so the authenticated endpoint answered 401 and
 *          the button did nothing.
 *   打开   `window.open('file:///…')` — a WebView refuses `file://`.
 *
 * So the checks are about *effects*, not about wiring. 下载 must produce real bytes
 * on disk with the right name and a matching SHA-256.
 *
 * 打开 is checked differently, and the difference is deliberate. Clicking it against
 * the live backend makes the server launch the file's default program, which means
 * running this test throws a PowerPoint window onto whoever's desktop it runs on.
 * That is not something a test gets to do. So the request is intercepted at the
 * browser: this asserts the click really issues `POST /deliverables/{id}/open`, and
 * that a refusal is *shown* rather than swallowed — and nothing is launched.
 *
 * What is therefore NOT covered here, and is recorded as NOT VERIFIED rather than
 * implied: whether a program window actually appears. The path resolution behind
 * that endpoint is covered for real in
 * `backend/tests/integration/test_deliverable_open.py`.
 *
 *   node scripts/verify-deliverable-actions.mjs
 */

import { chromium } from './_playwright.mjs'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'ui-verification')
const DOWNLOADS = join(HERE, '..', '.runtime', 'download-check')
const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  rmSync(DOWNLOADS, { recursive: true, force: true })
  mkdirSync(DOWNLOADS, { recursive: true })

  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    acceptDownloads: true,
  })
  const page = await context.newPage()

  const calls = []
  page.on('request', (r) => {
    const url = r.url()
    if (url.includes('/api/v1/')) calls.push(`${r.method()} ${url.split('/api/v1')[1].split('?')[0]}`)
  })
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  // Intercepted, so the backend never launches anything. See the header.
  let openMode = 'ok'
  const openRequests = []
  await page.route(/\/api\/v1\/deliverables\/[^/]+\/open$/, async (route) => {
    openRequests.push(route.request().method())
    if (openMode === 'fail') {
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: { code: 'validation_error', message: '文件已不在磁盘上，无法打开：gone.pptx' },
          request_id: 'intercepted',
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { path: '', name: '', opened: true, method: 'os.startfile' },
        message: '已用系统默认程序打开',
      }),
    })
  })

  try {
    // ---------------------------------------------------------------- login --
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))
    check('登录成功', true)

    // ------------------------------------------- find a run with a file on disk --
    const listing = await page.evaluate(async (base) => {
      const t = localStorage.getItem('aiw.token')
      const response = await fetch(`${base}/api/v1/deliverables?limit=50`, {
        headers: { Authorization: `Bearer ${t}` },
      })
      const body = await response.json()
      return body?.data?.deliverables ?? []
    }, BASE)

    check('能读到产出物列表', listing.length > 0, `${listing.length} 个`)
    if (listing.length === 0) throw new Error('没有产出物可供验证')

    const target = listing[0]
    check('挑到一个产出物用于验证', Boolean(target?.build_run_id), `${target.title} (${target.kind}, ${target.size_bytes} B)`)

    // ----------------------------------------------------------- workbench ---
    await page.goto(`${BASE}/workbench/${target.build_run_id}`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="deliverable-card"]', { timeout: 20000 })
    await page.screenshot({ path: join(SHOTS, '70-deliverable-actions.png'), fullPage: true })

    // The old shape was an <a>. Asserting the element type is what catches a
    // regression back to a bare link — the click would still "work", it would just
    // 401 in silence.
    const downloadTag = await page
      .locator('[data-testid="download-deliverable"]')
      .first()
      .evaluate((el) => el.tagName.toLowerCase())
    check('下载是一个按钮，不是 <a href>（旧写法会 401）', downloadTag === 'button', `<${downloadTag}>`)

    const anchorToDownload = await page.locator('a[href*="/deliverables/"][href*="download"]').count()
    check('页面上没有指向下载端点的裸链接', anchorToDownload === 0, `${anchorToDownload} 个`)

    const fileUrls = await page.locator('a[href^="file://"]').count()
    check('页面上没有 file:// 链接（WebView 会拦）', fileUrls === 0, `${fileUrls} 个`)

    // ------------------------------------------------------------- download --
    const beforeDownload = calls.length
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      page.locator('[data-testid="download-deliverable"]').first().click(),
    ])

    const suggested = download.suggestedFilename()
    const saved = join(DOWNLOADS, suggested)
    await download.saveAs(saved)

    const downloadCalls = calls.slice(beforeDownload)
    check(
      '下载真的请求了下载端点',
      downloadCalls.some((c) => c.startsWith('GET /deliverables/')),
      downloadCalls.join(' | ') || '(没有请求)',
    )

    // Matched by filename rather than by index. A run can produce several files and
    // the page lists them in its own order, so `listing[0]` is not necessarily the
    // card whose button was clicked — asserting against it compares two different
    // deliverables and reports a mismatch that is the test's fault, not the code's.
    const match = listing.find(
      (d) => d.path.replace(/\\/g, '/').split('/').pop() === suggested,
    )
    check('下载的文件对应到列表里的一个产出物', Boolean(match), suggested)

    if (match) {
      check('文件名是原文件名（中文没乱码）', true, suggested)

      const onDisk = statSync(saved)
      check(
        '文件真的落地，字节数与记录一致',
        onDisk.size === match.size_bytes,
        `${onDisk.size} B vs 记录 ${match.size_bytes} B`,
      )

      // Bytes, not just size: a truncated body can match a length by accident, and
      // this is the check that proves the content actually arrived.
      const remoteDigest = await page.evaluate(async (args) => {
        const response = await fetch(`${args.base}/api/v1/deliverables/${args.id}/download`, {
          headers: { Authorization: `Bearer ${args.token}` },
        })
        const buffer = await response.arrayBuffer()
        const hash = await crypto.subtle.digest('SHA-256', buffer)
        return Array.from(new Uint8Array(hash))
          .map((b) => b.toString(16).padStart(2, '0'))
          .join('')
      }, {
        base: BASE,
        id: match.id,
        token: await page.evaluate(() => localStorage.getItem('aiw.token')),
      })

      const localDigest = createHash('sha256').update(readFileSync(saved)).digest('hex')
      check('落地文件的 SHA256 与后端一致', localDigest === remoteDigest, `${localDigest.slice(0, 16)}…`)
    }

    // ---------------------------------------------------------------- open ---
    // Intercepted: this proves the click is wired to the endpoint, without asking
    // the server to open anything on the machine running this test.
    openMode = 'ok'
    openRequests.length = 0
    await page.locator('[data-testid="open-deliverable"]').first().click()
    await page.waitForTimeout(1200)
    check(
      '点「打开」真的发出 POST /deliverables/{id}/open',
      openRequests.length === 1 && openRequests[0] === 'POST',
      openRequests.join(',') || '(没有请求)',
    )

    const afterOk = await page.locator('[data-testid="deliverable-notice"]').count()
    check('成功时不弹错误提示', afterOk === 0, `${afterOk} 条提示`)

    // The other half of the defect: the old button reported nothing either way.
    openMode = 'fail'
    await page.locator('[data-testid="open-deliverable"]').first().click()
    await page.waitForTimeout(1200)
    const notice = await page.locator('[data-testid="deliverable-notice"]').innerText().catch(() => '')
    check('打开失败时把原因显示出来（不是静默无反应）', /文件已不在磁盘上/.test(notice), notice.replace(/\n/g, ' ').slice(0, 90))

    await page.screenshot({ path: join(SHOTS, '71-deliverable-open-failed.png'), fullPage: true })

    const real = errors.filter((e) => !/favicon|DevTools|ResizeObserver/i.test(e))
    check('无 JS 运行时错误', real.length === 0, real.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error-deliverable-actions.png') }).catch(() => {})
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
