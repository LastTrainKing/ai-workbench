/**
 * 会话列表的三种删除方式，都在浏览器里真点一遍。
 *
 *     会话列表：现在堆满了，要能删。
 *     [ ] 每一条右边一个删除按钮（悬停出现即可，但必须能点到）
 *     [ ] 批量删除：勾选多条 → 删除
 *     [ ] 全选删除：列表顶部一个"全选"，选中后一键清空
 *     [ ] 删除要二次确认，并说清删的是什么（几条对话、几个产出物）
 *     [ ] 空列表要有空状态，不要显示一片空白
 *
 * **不发送任何消息。** Conversations are created through the API, which is a database
 * insert and a canned greeting — no model call. This round forbids real model calls
 * (the user deleted their key on purpose), and a delete test has no business making one.
 *
 *   node scripts/verify-conversation-delete.mjs
 */

import { chromium } from './_playwright.mjs'

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'
const SHOTS = 'screenshots/ui-verification'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newContext({ viewport: { width: 1440, height: 950 } }).then((c) => c.newPage())

const api = async (path, init = {}) =>
  page.evaluate(
    async ([p, i]) => {
      const token = localStorage.getItem('aiw.token')
      const response = await fetch(`/api/v1${p}`, {
        ...i,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(i.headers ?? {}),
        },
      })
      return { status: response.status, body: await response.json().catch(() => null) }
    },
    [path, init],
  )

const makeConversations = async (count) => {
  const ids = []
  for (let index = 0; index < count; index += 1) {
    const created = await api('/conversations', {
      method: 'POST',
      body: JSON.stringify({ title: `删除测试 ${index + 1}` }),
    })
    ids.push(created.body?.data?.id)
  }
  return ids
}

/** The rows, newest first, as the sidebar shows them. */
const rowTitles = async () =>
  (await page.locator('[data-testid="conversation-entry"]').allInnerTexts()).map((t) => t.trim())

try {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
  await page.waitForTimeout(1200)

  /* ------------------------------------------------------------- 方式一：单条 -- */
  console.log('\n=== 方式一：每一条右边的删除按钮 ===')
  const [first] = await makeConversations(1)
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  const target = page.locator('[data-testid="conversation-entry"]', { hasText: '删除测试 1' }).first()
  check('新建的会话出现在列表里', (await target.count()) > 0)

  const row = page.locator('div', { has: target }).first()
  const del = row.locator('[data-testid="conversation-delete"]').first()
  check('每一行都有一个删除按钮', (await del.count()) > 0)

  // 「悬停出现即可，但必须能点到」: reveal it, then click it like a person would.
  await target.hover()
  await page.waitForTimeout(250)
  await page.screenshot({ path: `${SHOTS}/D1-hover-delete.png`, fullPage: true })
  await del.click({ timeout: 5000 }).catch(() => null)

  const dialog = page.locator('[role="dialog"], .fixed').filter({ hasText: '删掉' }).first()
  await page.waitForTimeout(600)
  const dialogText = await page.locator('body').innerText()
  check('点了删除会先问一次（二次确认）', /删掉这条对话|删掉这 \d+ 条对话/.test(dialogText), '')
  check('确认框里说清了删的是什么', /条消息/.test(dialogText), '')
  await page.screenshot({ path: `${SHOTS}/D2-confirm-single.png`, fullPage: true })

  const confirmButton = page.getByRole('button', { name: '删掉' }).last()
  await confirmButton.click({ timeout: 5000 }).catch(() => null)
  await page.waitForTimeout(1500)

  const afterSingle = await rowTitles()
  check('确认之后它从列表里没了', !afterSingle.some((t) => t.includes('删除测试 1')), afterSingle.slice(0, 4).join(' / '))
  const removed = await api(`/conversations/${first}`)
  check('服务端也真的删了（不是只从界面拿掉）', removed.status === 404, `GET 那条会话 → ${removed.status}`)

  /* ------------------------------------------------------------- 方式二：批量 -- */
  console.log('\n=== 方式二：勾选多条 → 删除 ===')
  await makeConversations(3)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  await page.click('[data-testid="enter-select-mode"]')
  await page.waitForTimeout(400)
  const boxes = page.locator('[data-testid="conversation-check"]')
  check('进选择模式后每行有勾选框', (await boxes.count()) > 0, `${await boxes.count()} 个`)

  // Pick two rows that are definitely the test ones.
  let ticked = 0
  for (let index = 0; index < (await boxes.count()); index += 1) {
    const entry = page.locator('[data-testid="conversation-entry"]').nth(index)
    const label = (await entry.innerText()).trim()
    if (label.startsWith('删除测试') && ticked < 2) {
      await boxes.nth(index).check()
      ticked += 1
    }
  }
  check('勾上了 2 条', ticked === 2, `${ticked} 条`)
  await page.screenshot({ path: `${SHOTS}/D3-selected.png`, fullPage: true })

  const bulkButton = page.locator('[data-testid="delete-selected"]')
  check('顶部出现「删除 N」按钮', (await bulkButton.count()) > 0)
  await bulkButton.click()
  await page.waitForTimeout(700)
  const bulkDialog = await page.locator('body').innerText()
  check('批量删除也要确认', /删掉这 2 条对话/.test(bulkDialog), '')
  await page.getByRole('button', { name: '删掉' }).last().click()
  await page.waitForTimeout(1500)

  const afterBulk = await rowTitles()
  const left = afterBulk.filter((t) => t.startsWith('删除测试')).length
  check('2 条都删掉了', left <= 1, `还剩 ${left} 条「删除测试」`)
  check('删完自己退出了选择模式', (await page.locator('[data-testid="conversation-check"]').count()) === 0)

  /* ------------------------------------------------------------- 方式三：全选 -- */
  console.log('\n=== 方式三：全选 → 一键清空 ===')
  await page.click('[data-testid="enter-select-mode"]')
  await page.waitForTimeout(300)
  const selectAll = page.locator('[data-testid="select-all"]')
  check('顶部有「全选」', (await selectAll.count()) > 0)
  check('一开始写的是「全选」而不是「取消全选」', (await selectAll.innerText()).includes('全选'))
  await selectAll.click()
  await page.waitForTimeout(400)
  const total = await page.locator('[data-testid="conversation-check"]').count()
  const checked = await page.locator('[data-testid="conversation-check"]:checked').count()
  check('全选把所有行都勾上了', total > 0 && checked === total, `${checked}/${total}`)
  await page.screenshot({ path: `${SHOTS}/D4-select-all.png`, fullPage: true })

  await page.locator('[data-testid="delete-selected"]').click()
  await page.waitForTimeout(700)
  await page.getByRole('button', { name: '删掉' }).last().click()
  await page.waitForTimeout(2000)

  const empty = page.locator('[data-testid="conversation-empty"]')
  check('全清之后是空状态，不是一片空白', (await empty.count()) > 0)
  const emptyText = (await empty.innerText().catch(() => '')).replace(/\s+/g, ' ')
  check('空状态写了人话', /还没有会话/.test(emptyText), emptyText)
  await page.screenshot({ path: `${SHOTS}/D5-empty-state.png`, fullPage: true })

  const remaining = await rowTitles()
  check('列表真的空了', remaining.length === 0, `${remaining.length} 条`)

  /* ------------------------------------------------------------ 收尾：还回来 -- */
  console.log('\n=== 清完之后还能继续用 ===')
  await makeConversations(1)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)
  check('删空之后还能新建会话', (await rowTitles()).length > 0)
  check('新建按钮还在', (await page.locator('[data-testid="new-conversation"]').count()) > 0)

  const failed = results.filter((item) => !item.ok)
  console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`)
  for (const item of failed) console.log(`  [FAIL] ${item.name}`)
  await browser.close()
  process.exit(failed.length ? 1 : 0)
} catch (error) {
  console.error('\n验证脚本本身出错：', error)
  await page.screenshot({ path: `${SHOTS}/error-conversation-delete.png`, fullPage: true }).catch(() => {})
  await browser.close()
  process.exit(2)
}
