/**
 * UI verification for the AI Provider settings screen.
 *
 * Logs in through the real API, opens Settings, asserts the key labels are
 * present in the rendered DOM, and captures a screenshot. Read-only: it does
 * not change the configuration.
 */
import { createRequire } from 'node:module'
import path from 'node:path'

const ROOT = 'C:/Users/user/Desktop/ai-workbench'
const require = createRequire(path.join(ROOT, 'frontend', 'package.json'))
const { chromium } = require('playwright-core')
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const API = 'http://127.0.0.1:8000/api/v1'
const WEB = 'http://127.0.0.1:5173'

async function api(p, o = {}) {
  const r = await fetch(API + p, {
    method: o.method ?? 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(o.token ? { Authorization: 'Bearer ' + o.token } : {}),
    },
    body: o.body ? JSON.stringify(o.body) : undefined,
  })
  return (await r.json()).data
}

const login = await api('/auth/login', {
  method: 'POST',
  body: { identifier: 'demo', password: 'demo1234' },
})
const token = login.access_token
const user = await api('/auth/me', { token })

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
  colorScheme: 'dark',
  locale: 'zh-CN',
})
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})

await page.goto(WEB + '/login', { waitUntil: 'domcontentloaded' })
await page.evaluate(
  ([t, u]) => {
    localStorage.setItem('aiw.token', t)
    localStorage.setItem('aiw.user', JSON.stringify(u))
  },
  [token, user],
)

await page.goto(WEB + '/settings', { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(4500)

const body = await page.locator('body').innerText()
const needles = [
  'AI Provider',
  'deepseek-flash',
  'DeepSeek V4.1 Flash',
  '当前生效',
  '测试连接',
  'Ollama',
  'Offline',
  '应用配置',
]
console.log('--- rendered-DOM assertions ---')
let missing = 0
for (const needle of needles) {
  const found = body.includes(needle)
  if (!found) missing += 1
  console.log('  ' + (found ? 'FOUND   ' : 'MISSING ') + needle)
}

await page.screenshot({
  path: path.join(ROOT, 'screenshots', '14-ai-provider-settings.png'),
  fullPage: true,
})
console.log('screenshot: screenshots/14-ai-provider-settings.png')
console.log('missing labels:', missing)
console.log('console errors:', errors.length ? errors.slice(0, 3) : 'none')

await browser.close()
process.exit(missing > 0 ? 1 : 0)
