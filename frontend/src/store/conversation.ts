/**
 * What the user has said but the server has not yet confirmed.
 *
 *     "我按了发送，我的话应该立刻出现，不该等服务器。"
 *
 * Sending a message is one round trip that can take eleven seconds when the answer is
 * long, and the old screen showed nothing at all for the whole of it — the user's own
 * sentence appeared at the same moment as the reply, which reads as "the app is stuck",
 * not "it is thinking".
 *
 * **Why a store and not `useState`.** The first message of a conversation is sent from
 * `/`, and the reply arrives after the app has navigated to `/c/<id>`. Two different
 * route entries render `ConversationPage`, and component state does not survive that
 * navigation reliably. A message that disappears from the screen while it is still in
 * flight is the exact failure this file exists to prevent, so the queue lives outside
 * the component.
 *
 * The queue is deliberately **not** persisted. After a refresh the server's own list is
 * the whole truth, in the server's order — which is what "刷新页面后，消息顺序和内容必须
 * 和提交时一致" asks for.
 */

import { create } from 'zustand'

export interface PendingMessage {
  /** Local only. Never sent to the server and never rendered as a server id. */
  id: string
  /** `null` while the conversation itself is still being created. */
  conversationId: string | null
  text: string
  state: 'sending' | 'failed'
  /** Shown under a failed message; the retry sends `text` again. */
  error?: string
  /**
   * How many user messages the server had already recorded when this was sent.
   *
   * Kept for display ordering only. **It is no longer what decides when to drop the local
   * copy** — see `serverMessageId`.
   */
  serverUserCount: number

  /**
   * The server's id for this message, once the POST has come back.
   *
   * ## Why this replaced a count
   *
   * The handover used to be `item.serverUserCount < serverUserCount` — "the server's list
   * has grown past where I was sent, so it has me". That is true for one message in
   * flight and false for two: both are sent when the server has N messages, both carry
   * `serverUserCount = N`, and the moment the *first* lands the server has N+1 and **both
   * are dropped** — including the one the server has not received yet.
   *
   * Measured, with the source predicate:
   *
   *     just sent two, server has 3  ->  screen: [ '第一条', '第二条' ]
   *     first one landed, server 4   ->  screen: []          <-- second is still in flight
   *
   * The file's own header says a message that disappears while in flight is the exact
   * failure it exists to prevent. Counting cannot express "this specific one"; an id can.
   */
  serverMessageId?: string
}

interface ConversationState {
  pending: PendingMessage[]
  /** Queue a message and return its local id. */
  addPending: (input: { conversationId: string | null; text: string; serverUserCount: number }) => string
  /** Mark it failed. It stays on screen, with a retry. */
  failPending: (id: string, error: string) => void
  /** Put it back in flight, for a retry. */
  retryPending: (id: string) => void
  /** Remove it — the server's copy has taken over. */
  dropPending: (id: string) => void
  /** Attach the server's id, once the POST has answered. */
  attachServerId: (id: string, serverMessageId: string) => void
  /**
   * Bind a message to the conversation the server just created for it.
   *
   * A message sent from `/` is queued before its conversation exists, so it starts with
   * `conversationId: null` — and it used to stay that way forever. Two consequences, both
   * real: the bubble was shown in **every** conversation (the filter keeps anything with
   * no conversation id), and a retry from a different conversation sent the text to
   * **that** conversation.
   */
  bindPending: (id: string, conversationId: string) => void
}

let sequence = 0

export const useConversationStore = create<ConversationState>((set) => ({
  pending: [],

  addPending({ conversationId, text, serverUserCount }) {
    sequence += 1
    const id = `local-${Date.now()}-${sequence}`
    set((state) => ({
      pending: [...state.pending, { id, conversationId, text, state: 'sending', serverUserCount }],
    }))
    return id
  },

  failPending(id, error) {
    set((state) => ({
      pending: state.pending.map((item) =>
        item.id === id ? { ...item, state: 'failed', error } : item,
      ),
    }))
  },

  retryPending(id) {
    set((state) => ({
      pending: state.pending.map((item) =>
        item.id === id ? { ...item, state: 'sending', error: undefined } : item,
      ),
    }))
  },

  dropPending(id) {
    set((state) => ({ pending: state.pending.filter((item) => item.id !== id) }))
  },

  attachServerId(id, serverMessageId) {
    set((state) => ({
      pending: state.pending.map((item) =>
        item.id === id ? { ...item, serverMessageId } : item,
      ),
    }))
  },

  bindPending(id, conversationId) {
    set((state) => ({
      pending: state.pending.map((item) =>
        item.id === id ? { ...item, conversationId } : item,
      ),
    }))
  },
}))

/**
 * The local messages that should still be drawn.
 *
 * A failed one always is — it is the only copy the user has of something the server
 * never received. A sending one is drawn until the server's list has grown past the
 * point where it was sent, at which point the server's own copy is on screen and this
 * one would be a duplicate.
 */
export function outstandingPending(
  pending: PendingMessage[],
  serverMessageIds: ReadonlySet<string>,
): PendingMessage[] {
  return pending.filter((item) => {
    // A failed one always stays: it is the only copy the user has of something the server
    // never received.
    if (item.state === 'failed') return true
    // Sending: drop it only when the server's own list contains **this** message. Not
    // "the list got longer" — that is the count comparison that dropped the second of two
    // messages sent together.
    if (item.serverMessageId) return !serverMessageIds.has(item.serverMessageId)
    return true
  })
}
