/**
 * Browser test for two claims that were previously "probably fine".
 *
 * 1. A task shows the knowledge it is linked to. The data existed
 *    (`task_documents` had rows) and no page displayed it, so the claim had to be
 *    checked by clicking, not by reading the schema.
 *
 * 2. A memory says where it came from, and can be walked back to the delivery
 *    that taught it. "You prefer more charts" is an assertion until the user can
 *    see which rejection produced it.
 *
 *   node scripts/verify-knowledge-and-memory-ui.mjs
 */

import { chromium } from './_playwright.mjs'
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
function note(name, detail) {
  results.push({ name, ok: false, detail, unverified: true })
  console.log(`  [NOT VERIFIED] ${name} — ${detail}`)
}

async function api(page, path) {
  return page.evaluate(async (p) => {
    const token = window.localStorage.getItem('aiw.token') ?? ''
    const response = await fetch(`/api/v1${p}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    return response.json()
  }, path)
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser
    .newContext({ viewport: { width: 1600, height: 1000 } })
    .then((c) => c.newPage())
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))
    console.log('  登录成功\n')

    // ------------------------------------------------- task ↔ knowledge link --
    console.log('  ① 任务页「关联知识」')
    // The links exist on only a few of the 250-odd tasks, so every page is walked
    // rather than the first one — "the data is there but this page happens not to
    // show it" is exactly the failure this test exists to catch.
    const tasks = []
    // `pageNumber`, not `page`: the loop variable shadowed the browser page, so every
    // API call was made on the number 1.
    for (let pageNumber = 1; pageNumber <= 5; pageNumber += 1) {
      const listed = await api(page, `/tasks?page_size=100&page=${pageNumber}`)
      const items = listed?.data?.items ?? []
      if (items.length === 0) break
      tasks.push(...items)
    }
    let linked = null
    let linkedDetail = null
    for (const task of tasks) {
      const detail = await api(page, `/tasks/${task.id}`)
      const documents = detail?.data?.documents ?? []
      if (documents.length > 0) {
        linked = task
        linkedDetail = detail.data
        break
      }
    }
    console.log(`    （扫描了 ${tasks.length} 个任务）`)

    if (!linked) {
      note('任务详情返回真实 documents', `检查了 ${tasks.length} 个任务，没有一个有关联文档`)
    } else {
      const documents = linkedDetail.documents
      check(
        '后端 GET /tasks/{id} 返回真实 documents',
        documents.length > 0,
        `${documents.length} 条：${documents.map((d) => d.title).slice(0, 3).join('、')}`
      )
      await page.goto(`${BASE}/tasks?task=${linked.id}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      const drawer = await page.locator('body').innerText()
      check('抽屉渲染出「关联知识」标题', /关联知识（\d+）/.test(drawer), drawer.match(/关联知识（\d+）/)?.[0] ?? '')
      const link = page.locator(`a[href*="/documents?doc="]`).first()
      const count = await link.count()
      check('关联文档是可点的链接', count > 0)
      if (count > 0) {
        const href = await link.getAttribute('href')
        const title = (await link.innerText()).trim()
        check('链接指向资料详情', /\/documents\?doc=/.test(href ?? ''), `${title} → ${href}`)
        await page.screenshot({ path: join(SHOTS, '70-task-knowledge.png'), fullPage: true })
        await link.click()
        await page.waitForTimeout(1500)
        check('点击后进入资料页', page.url().includes('/documents'), page.url().replace(BASE, ''))
      }
    }

    // ------------------------------------------------------ memory provenance --
    console.log('\n  ② 记忆页来源追溯')
    const memories = await api(page, '/memories?status=active')
    const rows = memories?.data?.memories ?? []
    const withOrigin = rows.filter((m) => m.structured?.origin)
    check('记忆接口返回 structured.origin', withOrigin.length > 0, `${withOrigin.length}/${rows.length} 条带来源`)

    if (withOrigin.length === 0) {
      note('记忆页渲染来源并可跳回交付', '没有任何记忆带来源信息')
    } else {
      const memory = withOrigin[0]
      const origin = memory.structured.origin
      await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(2000)
      const body = await page.locator('body').innerText()
      check('页面出现「来源：」', /来源：/.test(body))
      check(
        '来源包含交付标题',
        origin.deliverable_title ? body.includes(origin.deliverable_title) : false,
        origin.deliverable_title
      )
      check(
        '来源包含版本号',
        origin.version ? new RegExp(`第\\s*${origin.version}\\s*版`).test(body) : false,
        `v${origin.version}`
      )
      check(
        '来源包含你当时说的原话',
        origin.feedback ? body.includes(origin.feedback.slice(0, 12)) : false,
        origin.feedback?.slice(0, 24)
      )
      const jump = page.locator('a', { hasText: '查看那次交付' }).first()
      const jumpCount = await jump.count()
      check('有「查看那次交付」链接', jumpCount > 0)
      if (jumpCount > 0) {
        await page.screenshot({ path: join(SHOTS, '71-memory-origin.png'), fullPage: true })
        await jump.click()
        await page.waitForTimeout(2000)
        check(
          '点击后跳到那次交付的运行页',
          page.url().includes(`/workbench/${origin.build_run_id}`),
          page.url().replace(BASE, '')
        )
        await page.screenshot({ path: join(SHOTS, '72-memory-jump.png'), fullPage: true })
      }
    }

    check('页面没有 JS 报错', errors.length === 0, errors.slice(0, 2).join(' | '))
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok && !r.unverified)
  console.log(`\n  ${results.length - failed.length} 项通过，${failed.length} 项失败，${results.filter((r) => r.unverified).length} 项未验证`)
  return failed.length ? 1 : 0
}

main().then((code) => process.exit(code))
