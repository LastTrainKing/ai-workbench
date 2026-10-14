/**
 * 开关滑块在不在轨道里面 —— 量**滑块**，不是量轨道。
 *
 * 用户的报告：
 *
 *     开关（Switch）的滑块，在「开」的状态下顶出轨道右边缘。
 *     关的时候正常，开的时候凸出来了。
 *
 * 以及上一轮为什么漏掉它，这句话比 bug 本身重要：
 *
 *     上一轮我给了「开关位置正确」的结论，依据是轨道的 DOM 链
 *     （轨道 right=1328，卡片 right=1341，在内侧 13px）。
 *     **那个测量没错，但量的对象错了** —— 出问题的是滑块，不是轨道。
 *     量之前先说清量的是哪一个元素。
 *
 * 所以这个脚本量**滑块自己的矩形**，而不是轨道的：
 * 滑块是绝对定位子元素、**没有 overflow 裁切**，轨道没越界完全不代表滑块没越界。
 *
 * ## 判据是什么，以及第一版判据错在哪
 *
 * 第一版写的是「左右留白相等」，量出来每一个开关都不通过 —— 因为**判据本身是错的**。
 * 开关的滑块是要移动的：关的时候靠左、开的时候靠右，左右留白**本来就不该相等**，
 * 相等意味着它卡在中间。这条判据会把所有正确的开关判成错的。
 *
 * 正确的问题是：**两个状态下的"贴边留白"是不是同一个数**。
 *
 *     关  左 2 ······ 右 18        贴边留白 = 2（贴左）
 *     开  左 18 ······ 右 2        贴边留白 = 2（贴右）
 *
 * 两句话：
 *
 *   [ ] 关的时候靠左那条边、开的时候靠右那条边，留白是同一个数 —— 这就是"两侧对称"
 *   [ ] 反过来那一侧也是同一个数（它就是留白 + 行程）
 *   [ ] 滑块在两个状态下都完全在轨道内沿以内 —— 越界就是用户说的"凸出来"
 *   [ ] 上下留白在两个状态下都相等（纵向没有行程，所以这一条是本状态内的）
 *
 * 量的是 border-box；滑块的包含块是轨道的**内边距盒**，所以"内沿"要减掉边框宽度。
 *
 *   node scripts/verify-switch-geometry.mjs
 */

import { chromium } from './_playwright.mjs'

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'
/**
 * Three states, not two.
 *
 * A switch that cannot be flipped — disabled, or gated by the permission level — has only
 * one measurable state, so its two-state symmetry **cannot be checked**. Reporting that
 * as PASS would be a lie and reporting it as FAIL would be a false alarm. It is
 * `NOT VERIFIED`, and it is listed separately at the end so nobody reads the summary line
 * as "everything was checked".
 */
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, state: ok ? 'pass' : 'fail', detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}
const unverified = (name, detail) => {
  results.push({ name, state: 'unverified', detail })
  console.log(`  [NOT VERIFIED] ${name} — ${detail}`)
}

/** Every switch on the page, measured. Runs in the browser. */
const MEASURE = () => {
  const round = (value) => Math.round(value * 100) / 100
  return Array.from(document.querySelectorAll('[role="switch"]')).map((track, index) => {
    const knob = track.querySelector('span')
    if (!knob) return { index, error: '没有滑块子元素' }

    const t = track.getBoundingClientRect()
    const k = knob.getBoundingClientRect()
    const style = getComputedStyle(track)
    const border = {
      top: parseFloat(style.borderTopWidth) || 0,
      right: parseFloat(style.borderRightWidth) || 0,
      bottom: parseFloat(style.borderBottomWidth) || 0,
      left: parseFloat(style.borderLeftWidth) || 0,
    }

    // Distance from the knob's edge to the track's **inner** edge. Negative means the
    // knob has crossed the border and is outside the track's visual bounds.
    const gap = {
      left: round(k.left - (t.left + border.left)),
      right: round(t.right - border.right - k.right),
      top: round(k.top - (t.top + border.top)),
      bottom: round(t.bottom - border.bottom - k.bottom),
    }

    const wrapper = track.closest('div')
    const label = (wrapper?.innerText ?? track.getAttribute('aria-label') ?? '')
      .split('\n')[0]
      .trim()
      .slice(0, 40)

    return {
      index,
      label: label || `(第 ${index + 1} 个)`,
      checked: track.getAttribute('aria-checked') === 'true',
      disabled: track.disabled === true,
      track: { width: round(t.width), height: round(t.height) },
      knob: { width: round(k.width), height: round(k.height) },
      gap,
    }
  })
}

/** Compare the two states of one switch. */
const judge = (page, off, on) => {
  const label = `${page} · ${off.label}`
  console.log(
    `\n    ── ${label} ──\n` +
      `       轨道 ${off.track.width}×${off.track.height}  滑块 ${off.knob.width}×${off.knob.height}\n` +
      `       关  左 ${off.gap.left}  右 ${off.gap.right}  上 ${off.gap.top}  下 ${off.gap.bottom}\n` +
      `       开  左 ${on.gap.left}  右 ${on.gap.right}  上 ${on.gap.top}  下 ${on.gap.bottom}`,
  )

  for (const [state, row] of [
    ['关', off],
    ['开', on],
  ]) {
    const inside =
      row.gap.left >= 0 && row.gap.right >= 0 && row.gap.top >= 0 && row.gap.bottom >= 0
    check(
      `${label} · ${state}：滑块完全在轨道内沿以内`,
      inside,
      inside ? '' : `左${row.gap.left} 右${row.gap.right} 上${row.gap.top} 下${row.gap.bottom}`,
    )
  }

  if (off.disabled && on.checked === off.checked) {
    console.log(`    （禁用的开关只有一个状态，只判"在轨道内"）`)
    return
  }

  const near = Math.abs(off.gap.left - on.gap.right) <= 0.5
  check(`${label}：关贴左 / 开贴右 的留白是同一个数`, near, `关左 ${off.gap.left} vs 开右 ${on.gap.right}`)

  const far = Math.abs(off.gap.right - on.gap.left) <= 0.5
  check(`${label}：另一侧也是同一个数`, far, `关右 ${off.gap.right} vs 开左 ${on.gap.left}`)

  for (const [state, row] of [
    ['关', off],
    ['开', on],
  ]) {
    const even = Math.abs(row.gap.top - row.gap.bottom) <= 0.5
    check(`${label} · ${state}：上下留白相等`, even, `上 ${row.gap.top} vs 下 ${row.gap.bottom}`)
  }
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newContext({ viewport: { width: 1440, height: 950 } }).then((c) => c.newPage())

try {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#identifier')
  await page.fill('#identifier', 'demo')
  await page.fill('#password', 'demo1234')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.includes('/login'))
  await page.waitForTimeout(1200)

  // Every route that has a switch on it. Listed rather than guessed: a switch added to a
  // page nobody measures is a switch that can go wrong unmeasured.
  const PAGES = ['/settings', '/settings/agent']

  for (const path of PAGES) {
    console.log(`\n=== ${path} ===`)
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1800)

    const before = await page.evaluate(MEASURE)
    if (before.length === 0) {
      console.log('  这个页面没有开关')
      continue
    }
    console.log(`  找到 ${before.length} 个开关`)
    for (const row of before) {
      if (row.error) check(`${path} · 第 ${row.index + 1} 个开关`, false, row.error)
    }

    // Flip each one, measure, and put it back — so the test leaves the settings alone.
    for (const row of before) {
      if (row.error) continue
      const target = page.locator('[role="switch"]').nth(row.index)
      const canFlip = !row.disabled
      let after = row
      let flipped = false
      if (canFlip) {
        await target.click({ timeout: 4000 }).catch(() => null)
        await page.waitForTimeout(450)
        const measured = await page.evaluate(MEASURE)
        after = measured.find((item) => item.index === row.index) ?? row
        flipped = after.checked !== row.checked
      }

      if (!flipped) {
        // **Not a geometry failure.** Some capabilities are gated by the permission level:
        // the row is clickable, the server refuses the change, and the switch snaps back.
        // The first version of this script judged it as if the two states had been
        // measured and reported a failure — comparing a state against itself. Saying
        // "could not flip this one" is the honest report; a switch whose second state
        // cannot be reached cannot be checked for symmetry.
        const why = row.disabled ? '开关是禁用的' : '点了没变状态（可能被权限级别挡住）'
        console.log(
          `\n    ── ${path} · ${row.label} ──\n` +
            `       轨道 ${row.track.width}×${row.track.height}  滑块 ${row.knob.width}×${row.knob.height}\n` +
            `       ${row.checked ? '开' : '关'}  左 ${row.gap.left}  右 ${row.gap.right}  上 ${row.gap.top}  下 ${row.gap.bottom}\n` +
            `       ${why} —— 只有一个状态可量，跳过"两侧对称"这一条`,
        )
        const inside =
          row.gap.left >= 0 && row.gap.right >= 0 && row.gap.top >= 0 && row.gap.bottom >= 0
        check(`${path} · ${row.label}：滑块在轨道内沿以内`, inside, `${why}`)
        const even = Math.abs(row.gap.top - row.gap.bottom) <= 0.5
        check(`${path} · ${row.label}：上下留白相等`, even, `上 ${row.gap.top} vs 下 ${row.gap.bottom}`)
        unverified(`${path} · ${row.label}：两侧留白对称`, `${why}，第二个状态量不到`)
        continue
      }

      const off = row.checked ? after : row
      const on = row.checked ? row : after
      judge(path, off, on)
      await target.click({ timeout: 4000 }).catch(() => null)
      await page.waitForTimeout(400)
    }
  }

  const failed = results.filter((item) => item.state === 'fail')
  const pending = results.filter((item) => item.state === 'unverified')
  const passed = results.filter((item) => item.state === 'pass')
  console.log(
    `\n=== ${passed.length} 通过 / ${failed.length} 失败 / ${pending.length} 未验证（共 ${results.length}）===`,
  )
  for (const item of failed) console.log(`  [FAIL] ${item.name} — ${item.detail}`)
  for (const item of pending) console.log(`  [NOT VERIFIED] ${item.name} — ${item.detail}`)
  await browser.close()
  process.exit(failed.length ? 1 : 0)
} catch (error) {
  console.error('\n验证脚本本身出错：', error)
  await browser.close()
  process.exit(2)
}
