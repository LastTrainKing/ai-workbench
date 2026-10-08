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
   * This is what makes the handover from the local copy to the server's copy exact.
   * Once the server's list contains one more user message than this, the server has
   * recorded this one and the local copy is dropped — so the two are never on screen
   * together, and there is never a gap either.
   *
   * Counting rather than matching on text: the same sentence can legitimately be sent
   * twice, and matching on content would silently drop the second one.
   */
  serverUserCount: number
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
  serverUserCount: number,
): PendingMessage[] {
  return pending.filter(
    (item) => item.state === 'failed' || item.serverUserCount >= serverUserCount,
  )
}
