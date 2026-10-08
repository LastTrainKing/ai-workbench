/**
 * Client for conversations — the only interface the user asked for.
 *
 *     "我点开这个软件，就是要和我的数字员工对话"
 *
 * The important shape here is `Turn`. Saying something comes back as one of **three**
 * outcomes, and which one it is is a decision the server made, not something the client
 * infers from the payload. A client that guessed would be a second implementation of the
 * understanding layer, and the two would eventually disagree about whether work had
 * started — which is exactly the bug this round exists to fix.
 */

import { request, requestResult } from './api'
import type { Deliverable } from './deliveryApi'

/* ------------------------------------------------------------------ types -- */

export type TurnOutcome = 'answer' | 'clarify' | 'start'

/**
 * What answered, and why — the same shape wherever it appears.
 *
 * `summary` is what a person reads. `explanation` carries the per-tier milliseconds and
 * is kept because the startup log and `scripts/sample_relevance.py` read it; nothing on
 * screen renders it. The user's rule: 「用户看得懂的留，看不懂的删」.
 */
export interface Retrieval {
  tier?: string
  tier_label?: string
  summary?: string
  explanation?: string
  total_ms?: number
}

export interface ConversationQuestion {
  question: string
  /**
   * What it will do if the user says nothing.
   *
   * Empty string is a real value, not a missing one: it means it has no recommendation
   * for this particular question. The screen says so rather than inventing one — a
   * question without a recommendation is still worth asking.
   */
  default: string
}

export interface ConversationMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  /** `null` means "just words": the greeting, and everything the user said. */
  kind: 'answer' | 'clarify' | 'start' | null
  content: string
  questions: ConversationQuestion[]
  build_run_id: string | null
  decided_for_you: string[]
  retrieval: Retrieval
  /** True while it is the user's turn to reply. */
  waiting: boolean
  created_at: string
}

export interface Conversation {
  id: string
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[]
  deliverables: Deliverable[]
}

export interface Turn {
  outcome: TurnOutcome
  message: ConversationMessage
  build_run_id: string | null
  decided_for_you: string[]
  retrieval: Retrieval
}

/* ------------------------------------------------------------- the client -- */

export const conversationApi = {
  list: () => request<{ conversations: Conversation[]; count: number }>('/conversations'),

  create: (title = '') =>
    requestResult<Conversation>('/conversations', { method: 'POST', body: { title } }),

  get: (id: string) => request<ConversationDetail>(`/conversations/${id}`),

  /** Say something. One of three things comes back; see `Turn`. */
  send: (id: string, content: string) =>
    requestResult<Turn>(`/conversations/${id}/messages`, {
      method: 'POST',
      body: { content },
    }),

  /** 「就按你说的做」 — the same thing as sending that sentence, decided server-side. */
  confirm: (id: string) =>
    requestResult<Turn>(`/conversations/${id}/confirm`, { method: 'POST' }),
}
