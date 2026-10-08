/**
 * Shared helper: get from "a goal in my head" to "work has started", through the UI.
 *
 * Every test that needs a running build has to get there the way a person does, and since
 * this round there is more than one way the product can respond:
 *
 *   it may start immediately, or it may ask first — and which one it does is a real model's
 *   decision, not something a test can assume.
 *
 * So the helper accepts both: it sends the goal, and if it asked, it presses
 * 「就按你说的做」. That button exists precisely for "I do not want to answer questions,
 * get on with it", so using it is a supported path rather than a way of forcing the test
 * past the feature.
 *
 * Not a test on its own — it has no assertions. `verify-understanding-ui.mjs` is where the
 * three outcomes are actually checked.
 */

/** Talk to the API with the page's own token, so the record read is the real one. */
export async function api(page, path, init = {}) {
  return page.evaluate(
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
}

export async function newConversation(page) {
  const created = await api(page, '/conversations', { method: 'POST', body: JSON.stringify({}) })
  const id = created.body?.data?.id
  if (!id) throw new Error(`could not create a conversation: HTTP ${created.status}`)
  return id
}

/**
 * Send a goal, answer if asked, and return the run that started.
 *
 * `timeoutMs` is generous because the first step is a real model call and the second is a
 * real agent run: the point is to test the product, not to race it.
 */
export async function startThroughConversation(page, base, goal, timeoutMs = 180000) {
  const conversationId = await newConversation(page)
  await page.goto(`${base}/c/${conversationId}`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="goal-input"]')
  await page.waitForTimeout(800)

  await page.fill('[data-testid="goal-input"]', goal)
  await page.click('[data-testid="start-build"]')

  await page
    .locator('[data-testid="clarify-questions"], [data-testid="conversation-turn"]')
    .first()
    .waitFor({ timeout: timeoutMs })
    .catch(() => null)

  const asked = await page.locator('[data-testid="confirm-start"]').count()
  if (asked > 0) {
    await page.locator('[data-testid="confirm-start"]').first().click()
    await page
      .locator('[data-testid="conversation-turn"]')
      .first()
      .waitFor({ timeout: timeoutMs })
      .catch(() => null)
  }
  await page.waitForTimeout(1500)

  const detail = await api(page, `/conversations/${conversationId}`)
  const started = (detail.body?.data?.messages ?? []).find((m) => m.kind === 'start')
  return { conversationId, runId: started?.build_run_id ?? null, asked: asked > 0 }
}
