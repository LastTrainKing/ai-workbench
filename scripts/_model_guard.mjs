/**
 * 需要真模型才能跑的测试，在没有模型时**跳过并说明**，而不是失败、也不是假装通过。
 *
 * The user deleted their API key on purpose and forbade real model calls this round:
 *
 *     用户把 API Key 删了 —— 这一轮禁止跑真实模型调用。
 *     **那不是故障，是他故意的。**
 *     这一轮不许有任何真实模型调用：没有 curl 打 DeepSeek、没有真跑一轮 agent、
 *     没有让浏览器测试走一次真对话
 *
 * A test that sends a message cannot run. The three wrong ways to handle that are:
 *
 *   * let it fail, and report a red suite that is not a regression;
 *   * let it "pass" by skipping quietly, which turns a missing check into a green tick;
 *   * delete the test.
 *
 * So it exits **3** — neither 0 nor 1 — and says in words what it skipped and why. A
 * caller that only looks at "non-zero" will notice; a caller that reads the output will
 * understand. `docs/archive/PROGRESS.md` records which scripts were skipped for this reason.
 *
 *   import { requireModelOrSkip } from './_model_guard.mjs'
 *   await requireModelOrSkip('verify-send-and-details.mjs')
 */

const BASE = process.env.AWB_BASE ?? 'http://127.0.0.1:8000'

export async function requireModelOrSkip(script) {
  let degraded = null
  let reason = ''
  try {
    const response = await fetch(`${BASE}/api/v1/health/deps`)
    const body = await response.json()
    degraded = body?.data?.ai_degraded ?? null
    reason = body?.data?.ai?.llm?.reason ?? ''
  } catch (error) {
    degraded = null
    reason = String(error)
  }

  if (degraded === false) return

  console.log('')
  console.log('  SKIPPED —— 这个脚本要真跑一次对话，而现在没有模型。')
  if (degraded === null) {
    console.log(`  读不到 /health/deps，无法确认有没有模型：${reason.slice(0, 120)}`)
  } else {
    console.log(`  接口自己说的原因：${String(reason).slice(0, 160)}`)
  }
  console.log('  这一轮要求 0 次真实模型调用，所以不跑。**这不算通过，也不算失败。**')
  console.log(`  配好 Key 之后再跑：node scripts/${script}`)
  process.exit(3)
}
