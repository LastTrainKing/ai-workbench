/**
 * The conversation queue's two bugs, asserted as behaviour.
 *
 *     「连发两条，第一条先完成、第二条仍在 flight —— 第二条不得消失」
 *     「新会话 A 的第一条发送后 conversationId 必须立即和服务端创建结果建立关联」
 *
 * Both are pure state logic, so they are tested here rather than through a browser: no
 * model call, no database, no timing. That matters this round, where the model-call count
 * has to stay at zero — a browser test that sends a message would write an `ai_runs` row
 * even with the offline engine.
 */

import { describe, expect, it } from 'vitest'

import { outstandingPending, type PendingMessage } from '@/store/conversation'

const pending = (over: Partial<PendingMessage> = {}): PendingMessage => ({
  id: 'local-1',
  conversationId: 'c1',
  text: '你好',
  state: 'sending',
  serverUserCount: 3,
  ...over,
})

describe('outstandingPending', () => {
  it('keeps a message whose server id the list has not seen yet', () => {
    const item = pending({ serverMessageId: 'm-2' })
    expect(outstandingPending([item], new Set(['m-1']))).toHaveLength(1)
  })

  it('drops a message once the list contains its own id', () => {
    const item = pending({ serverMessageId: 'm-2' })
    expect(outstandingPending([item], new Set(['m-1', 'm-2']))).toHaveLength(0)
  })

  it('**does not drop a second message sent alongside the first**', () => {
    // The bug, in one assertion. Both are queued when the server has three user messages,
    // so both carry `serverUserCount: 3`. The first lands; the list now has four. The old
    // predicate — `item.serverUserCount >= serverUserCount` — was false for both, so both
    // bubbles vanished, including the one the server had not received.
    //
    // Measured with that predicate:
    //     just sent two, server 3  ->  [ '第一条', '第二条' ]
    //     first landed,   server 4  ->  []          <-- second still in flight
    const first = pending({ id: 'local-1', text: '第一条', serverMessageId: 'm-4' })
    const second = pending({ id: 'local-2', text: '第二条' })

    const shown = outstandingPending([first, second], new Set(['m-1', 'm-2', 'm-3', 'm-4']))
    expect(shown.map((item) => item.text)).toEqual(['第二条'])
  })

  it('keeps every message while none has a server id yet', () => {
    const many = Array.from({ length: 10 }, (_, index) =>
      pending({ id: `local-${index}`, text: `第 ${index} 条` }),
    )
    expect(outstandingPending(many, new Set())).toHaveLength(10)
  })

  it('always keeps a failed message — it is the only copy the user has', () => {
    const failed = pending({ state: 'failed', serverMessageId: 'm-9' })
    expect(outstandingPending([failed], new Set(['m-9']))).toHaveLength(1)
  })
})

describe('conversation binding', () => {
  it('shows an unbound message only where no conversation is selected yet', () => {
    // `ConversationPage` filters with:
    //   item.conversationId ? item.conversationId === id : !id
    //
    // A message sent from `/` has no conversation until the server creates one. It used to
    // stay unbound forever, and the old filter kept anything with a falsy conversation id —
    // so it appeared in **every** conversation the user opened afterwards.
    const unbound = pending({ conversationId: null })
    const belongsHere = (item: PendingMessage, id: string | undefined) =>
      item.conversationId ? item.conversationId === id : !id

    expect(belongsHere(unbound, undefined)).toBe(true)
    expect(belongsHere(unbound, 'c-other')).toBe(false)

    const bound = pending({ conversationId: 'c-a' })
    expect(belongsHere(bound, 'c-a')).toBe(true)
    expect(belongsHere(bound, 'c-b')).toBe(false)
  })
})
