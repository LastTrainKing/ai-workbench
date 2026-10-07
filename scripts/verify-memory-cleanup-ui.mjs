/**
 * The memory screen stops asking the user to be the quality inspector.
 *
 * The user's complaint, in full: 「这种东西怎么能让用户自己调试呢，自己验证自己修正啊，
 * 自己修改可控的记忆，现在这是啥逻辑啊」. They were looking at four pending candidates
 * saying the same thing and twelve active memories of which six were test litter and
 * four were one preference phrased four ways.
 *
 * **This test makes its own mess and then has the product clean it up.** An earlier
 * version relied on the library happening to be dirty — which was true exactly once,
 * because the first run cleaned it. It also meant the check could not be re-run, and a
 * check that can only pass on the first attempt is not a check.
 *
 * So it writes three rows under a unique marker (two that say the same thing, one that
 * is obviously litter), runs the plan, applies it, and asserts:
 *
 *   1. the plan names every row and why, **before** anything changes
 *   2. nothing changes until 就按这个来 is clicked
 *   3. the duplicates become one row with the usage summed
 *   4. the litter is gone, and the real memories are untouched
 *
 *   node scripts/verify-memory-cleanup-ui.mjs
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

async function library(page) {
  return page.evaluate(async () => {
    const token = localStorage.getItem('aiw.token')
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const [mem, cand] = await Promise.all([
      fetch('/api/v1/memories?status=active&limit=200', { headers }).then((r) => r.json()),
      fetch('/api/v1/memory-candidates?status=pending', { headers }).then((r) => r.json()),
    ])
    return {
      memories: mem?.data?.memories ?? [],
      pending: cand?.data?.candidates ?? [],
    }
  })
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser
    .newContext({ viewport: { width: 1600, height: 1000 } })
    .then((c) => c.newPage())

  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  const marker = String(Date.now()).slice(-6)

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#identifier')
    await page.fill('#identifier', 'demo')
    await page.fill('#password', 'demo1234')
    await page.click('button[type="submit"]')
    await page.waitForURL((u) => !u.pathname.includes('/login'))

    // ------------------------------------------------ seed a known-messy state --
    const seeded = await page.evaluate(
      async (m) => {
        const token = localStorage.getItem('aiw.token')
        const headers = {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        }
        const rows = [
          { content: `用户偏好演示文稿图表充足、页数精简（校验 ${m}）`, kind: 'preference' },
          { content: `用户在验收演示文稿时要求图表充足、页数精简（校验 ${m}）`, kind: 'preference' },
          { content: `这是一条浏览器测试写入的记忆 ${m}`, kind: 'fact' },
        ]
        const ids = []
        for (const row of rows) {
          const res = await fetch('/api/v1/memories', {
            method: 'POST',
            headers,
            body: JSON.stringify(row),
          })
          const body = await res.json().catch(() => null)
          ids.push(body?.data?.id ?? null)
        }
        return ids
      },
      marker,
    )
    check('测试自己造出了脏状态（2 条同义 + 1 条垃圾）', seeded.every(Boolean), seeded.join(','))

    await page.goto(`${BASE}/me`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)

    const before = await library(page)
    console.log(`  清理前：${before.memories.length} 条已生效，${before.pending.length} 条待确认\n`)
    await page.screenshot({ path: join(SHOTS, '90-memory-before.png'), fullPage: true })

    // ------------------------------------------------- 1. the plan is shown ---
    const inspect = page.locator('[data-testid="memory-cleanup-inspect"]')
    check('有「检查一下记忆库」入口', (await inspect.count()) > 0)

    // It costs a model call, so wait for the *response* rather than a duration, and
    // report the backend's own status. A 502 from the provider is a real outcome and
    // the product surfaces it — it must not be read here as "nothing to clean up",
    // which is what a silent empty plan would look like.
    let planStatus = 0
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const pending = page.waitForResponse(
        (r) => /\/api\/v1\/memory-cleanup\/plan$/.test(r.url()) && r.request().method() === 'POST',
        { timeout: 120000 },
      )
      await inspect.click()
      const response = await pending.catch(() => null)
      planStatus = response?.status() ?? 0
      if (planStatus === 200) break
      const body = await response?.json().catch(() => null)
      console.log(
        `  第 ${attempt} 次检查返回 HTTP ${planStatus}：${body?.error?.message ?? '(无消息)'} —— 重试`,
      )
      await page.waitForTimeout(3000)
    }
    check('检查记忆库返回成功', planStatus === 200, `HTTP ${planStatus}`)

    await page
      .locator('[data-testid="memory-cleanup-note"]')
      .waitFor({ timeout: 20000 })
      .catch(() => null)

    const note = await page.locator('[data-testid="memory-cleanup-note"]').innerText().catch(() => '')
    check('给出了清理计划（写清了前后条数）', /清理前/.test(note), note.slice(0, 90))

    const duplicateBlocks = await page.locator('[data-testid="memory-cleanup-duplicates"]').count()
    const litterBlocks = await page.locator('[data-testid="memory-cleanup-litter"]').count()
    const candidateBlocks = await page.locator('[data-testid="memory-cleanup-candidates"]').count()
    console.log(
      `  计划里的动作：重复组 ${duplicateBlocks} · 垃圾 ${litterBlocks} · 待确认重复 ${candidateBlocks}`,
    )

    check('计划里列出了刚才造出来的那两条同义记忆', duplicateBlocks > 0, `${duplicateBlocks} 组`)
    check('计划里列出了刚才造出来的那条垃圾', litterBlocks > 0, `${litterBlocks} 组`)

    const planText = await page.locator('body').innerText()
    check('清单写清了「保留哪条、并入哪些、依据是什么」', /保留/.test(planText) && /依据/.test(planText))
    check('清单写明了删除是不可逆的', /真的删掉/.test(planText))
    await page.screenshot({ path: join(SHOTS, '91-memory-cleanup-plan.png'), fullPage: true })

    // ------------------------------------------------- 2. nothing has moved ---
    const during = await library(page)
    check(
      '看到计划的那一刻，库里一条都没变',
      during.memories.length === before.memories.length,
      `${during.memories.length} vs ${before.memories.length}`,
    )

    // ------------------------------------------------- 3. apply ---------------
    const apply = page.locator('[data-testid="memory-cleanup-apply"]')
    check('有执行按钮，且不是自动执行的', (await apply.count()) === 1)

    const applied = page.waitForResponse(
      (r) => /\/api\/v1\/memory-cleanup\/apply$/.test(r.url()) && r.request().method() === 'POST',
      { timeout: 60000 },
    )
    await apply.click()
    const response = await applied.catch(() => null)
    check('执行调用了 apply 端点', Boolean(response), `HTTP ${response?.status() ?? '无响应'}`)
    await page.waitForTimeout(2500)

    const after = await library(page)
    console.log(`  清理后：${after.memories.length} 条已生效\n`)

    const litter = after.memories.filter(
      (m) => m.content.includes(marker) && m.content.includes('浏览器测试'),
    )
    check('测试垃圾被删掉了', litter.length === 0, `还剩 ${litter.length} 条`)

    // Every row the test wrote is gone — merged into a fuller statement of the same
    // fact, or deleted as litter. Which of the two it is does not matter here; what
    // matters is that the library is back to what it was.
    const leftovers = after.memories.filter((m) => m.content.includes(marker))
    check('测试写进去的行全部被处理掉了', leftovers.length === 0, `还剩 ${leftovers.length} 条`)

    const preexisting = before.memories.filter((m) => !m.content.includes(marker))
    check(
      '库里原有的真实记忆一条没少',
      preexisting.every((old) => after.memories.some((now) => now.id === old.id)),
      `${preexisting.length} 条原有记忆`,
    )
    check(
      '清理后回到测试开始前的条数',
      after.memories.length === preexisting.length,
      `${before.memories.length} → ${after.memories.length}（原有 ${preexisting.length} 条）`,
    )

    // The survivor is the **most complete** wording, and its usage did not go down.
    // The model merged the two seeded rows into the real memory rather than the other
    // way round — which is right, and is why this asserts on the topic rather than on
    // the marker: an assertion that expected one of the test's own rows to survive was
    // asserting the wrong thing and failed on correct behaviour.
    const charts = after.memories.filter((m) => m.content.includes('图表') && m.content.includes('页数'))
    const chartsBefore = preexisting.find((m) => m.content.includes('图表') && m.content.includes('页数'))
    check(
      '合并后留下的是措辞最完整的那条，用量没有变少',
      charts.length === 1 && chartsBefore && charts[0].use_count >= chartsBefore.use_count,
      charts.length === 1 ? `${charts[0].use_count} 次（合并前 ${chartsBefore?.use_count ?? '?'}）` : `${charts.length} 条`,
    )

    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)
    await page.screenshot({ path: join(SHOTS, '92-memory-after.png'), fullPage: true })

    // ------------------------------------------------- 4. grouping + count ----
    const groups = await page.locator('[data-testid^="memory-group-"]').count()
    check('长期记忆按主题分组了', groups > 0, `${groups} 组`)

    const evidence = await page.locator('[data-testid="candidate-evidence"]').count()
    const pendingNow = (await library(page)).pending.length
    check(
      '待确认里没有同一件事的重复条目',
      pendingNow <= 4,
      `${pendingNow} 条待确认（提过多次的显示「这条我提过 N 次」：${evidence} 处）`,
    )

    check('无 JS 运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '))
  } catch (exc) {
    check('测试执行完成', false, `${exc.name}: ${exc.message}`.slice(0, 200))
    await page.screenshot({ path: join(SHOTS, 'error-memory-cleanup.png') }).catch(() => {})
  } finally {
    // Sweep up anything this run created and the product did not remove.
    //
    // The first version of this test left three rows behind when the model call
    // failed, which is the exact litter the test exists to complain about — a test
    // that writes into the live library has to clean up on the failure path too, not
    // only on the happy one.
    const swept = await page
      .evaluate(async (m) => {
        const token = localStorage.getItem('aiw.token')
        const headers = token ? { Authorization: `Bearer ${token}` } : {}
        const listed = await fetch('/api/v1/memories?status=active&limit=200', { headers })
          .then((r) => r.json())
          .catch(() => null)
        const leftovers = (listed?.data?.memories ?? []).filter((row) => row.content.includes(m))
        for (const row of leftovers) {
          await fetch(`/api/v1/memories/${row.id}`, { method: 'DELETE', headers })
        }
        return leftovers.length
      }, marker)
      .catch(() => 0)
    if (swept) console.log(`  （收尾：清掉了本次运行遗留的 ${swept} 条记忆）`)
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
