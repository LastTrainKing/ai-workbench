// @vitest-environment node
/**
 * End-to-end flow against the live backend, exercising the real `src/lib/api.ts`
 * client (envelope unwrapping, bearer token, typed errors).
 *
 * It is skipped by default so `npm test` stays hermetic. Run it with:
 *
 *   VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1 AI_WORKBENCH_E2E=1 npx vitest run src/test/api-flow.e2e.test.ts
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import { api, setAccessToken, API_BASE_URL } from '@/lib/api'

const ENABLED = process.env.AI_WORKBENCH_E2E === '1'
const SAMPLE = resolve(process.cwd(), '..', 'samples', 'rag-notes.md')

const created: { projectId?: string; kbId?: string; documentId?: string } = {}
let ragRunId: string | undefined

function log(message: string): void {
  // eslint-disable-next-line no-console
  console.log(`[e2e] ${message}`)
}

describe.skipIf(!ENABLED)('live API flow', () => {
  it('reports the configured base URL', () => {
    log(`base url = ${API_BASE_URL}`)
    expect(API_BASE_URL).toMatch(/^https?:\/\//)
  })

  it('1. logs in with the seeded demo account', async () => {
    const { data } = await api.auth.login({ identifier: 'demo', password: 'demo1234' })
    setAccessToken(data.access_token)
    log(`logged in as ${data.user.username} <${data.user.email}> token_type=${data.token_type}`)
    expect(data.access_token).toBeTruthy()
    expect(data.user.username).toBe('demo')
  })

  it('2. creates a project', async () => {
    const stamp = Date.now()
    const { data } = await api.projects.create({
      name: `E2E 前端验证 ${stamp}`,
      description: '由 frontend e2e 脚本创建，结束后会自动删除',
      color: '#6366f1',
    })
    created.projectId = data.id
    log(`project created: ${data.name} (${data.id})`)
    expect(data.id).toBeTruthy()
  })

  it('3. creates a knowledge base inside that project', async () => {
    const { data } = await api.knowledgeBases.create({
      name: `E2E 知识库 ${Date.now()}`,
      description: 'e2e',
      project_id: created.projectId ?? null,
      color: '#8b5cf6',
    })
    created.kbId = data.id
    log(`knowledge base created: ${data.name} (${data.id})`)
    expect(data.project_id).toBe(created.projectId)
  })

  it('4. uploads a sample markdown document', async () => {
    const buffer = readFileSync(SAMPLE)
    const file = new File([buffer], 'rag-notes.md', { type: 'text/markdown' })
    const { data, message } = await api.documents.upload({
      file,
      knowledge_base_id: created.kbId as string,
      project_id: created.projectId,
    })
    created.documentId = data.document.id
    log(
      `uploaded "${data.document.title}" status=${data.document.status} duplicate=${data.duplicate} job=${data.job_id} msg=${message}`,
    )
    expect(data.document.id).toBeTruthy()
  })

  it('5. polls ingestion until the document is COMPLETED', async () => {
    const deadline = Date.now() + 90_000
    let status = 'PENDING'
    let chunks = 0
    while (Date.now() < deadline) {
      const doc = await api.documents.get(created.documentId as string)
      status = doc.status
      chunks = doc.chunk_count
      if (status === 'COMPLETED' || status === 'FAILED') break
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
    log(`ingestion finished: status=${status} chunks=${chunks}`)
    expect(status).toBe('COMPLETED')
    expect(chunks).toBeGreaterThan(0)

    const embedding = await api.documents.embeddingStatus(created.documentId as string)
    log(
      `embedding: ${embedding.chunks_embedded}/${embedding.chunks_total} embedded, model=${embedding.embedding_model}`,
    )
    expect(embedding.chunks_embedded).toBeGreaterThan(0)
  })

  it('6. runs a hybrid search and inspects the score breakdown', async () => {
    const result = await api.search.run({ q: 'RAG 检索 分块', mode: 'hybrid', limit: 5 })
    log(
      `search mode=${result.mode} took=${result.took_ms}ms dense=${result.dense_hits} sparse=${result.sparse_hits} reranked=${result.reranked} hits=${result.results.length}`,
    )
    if (result.results[0]) {
      const top = result.results[0]
      log(
        `top hit: "${top.document_title}" #${top.chunk_index} score=${top.score.toFixed(4)} vector=${top.vector_score.toFixed(4)} keyword=${top.keyword_score.toFixed(4)}`,
      )
    }
    expect(result.results.length).toBeGreaterThan(0)
    expect(result.results[0]).toHaveProperty('vector_score')
    expect(result.results[0]).toHaveProperty('keyword_score')
  })

  it('7. asks a grounded RAG question and asserts citations are returned', async () => {
    const { data } = await api.chat.ask({
      question: 'RAG 的检索流程包含哪些步骤？',
      knowledge_base_ids: [created.kbId as string],
      mode: 'hybrid',
      top_k: 6,
      history: [],
    })
    log(
      `rag run=${data.run_id} grounded=${data.grounded} citations=${data.citations.length} provider=${data.provider}/${data.model} latency=${data.latency_ms}ms`,
    )
    log(`answer preview: ${data.answer.slice(0, 160).replace(/\n/g, ' ')}`)
    if (data.citations[0]) {
      log(
        `citation[1]: "${data.citations[0].document_title}" #${data.citations[0].chunk_index} score=${data.citations[0].score.toFixed(4)}`,
      )
    }

    expect(data.run_id).toBeTruthy()
    expect(data.answer.length).toBeGreaterThan(0)
    expect(data.citations.length).toBeGreaterThan(0)
    expect(data.retrieval.used_chunks).toBeGreaterThan(0)
    ragRunId = data.run_id
  })

  it('8. surfaces the run in the AI run trace endpoints', async () => {
    const list = await api.aiRuns.list({ page_size: 5 })
    log(`ai runs (global, latest 5): total=${list.meta.total}`)
    expect(list.meta.total).toBeGreaterThan(0)

    // Fetch the trace of the RAG run created in step 7.
    const trace = await api.aiRuns.get(ragRunId as string)
    log(
      `trace ${trace.run.run_id} type=${trace.run.run_type} status=${trace.run.status} prompt=${trace.run.prompt_name} v${trace.run.prompt_version} sources=${trace.sources.length} retrieval=${trace.run.retrieval_count}`,
    )
    expect(trace.run.run_type).toBe('RAG_ANSWER')
    expect(trace.run.prompt_version).toBeGreaterThan(0)
    expect(trace.sources.length).toBeGreaterThan(0)
  })

  it('9. reads the dashboard and health endpoints', async () => {
    const dashboard = await api.insights.dashboard()
    log(
      `dashboard: projects=${dashboard.workspace.projects} documents=${dashboard.knowledge.documents} chunks=${dashboard.knowledge.chunks} ai_runs=${dashboard.ai.total_runs} llm_degraded=${dashboard.providers.llm.degraded}`,
    )
    expect(dashboard.providers.llm.model).toBeTruthy()

    const deps = await api.health.deps()
    log(
      `deps: postgres=${deps.postgres.ok} redis=${deps.redis.ok} qdrant=${deps.qdrant.ok} ai_degraded=${deps.ai_degraded} embedding_dim=${deps.ai.embedding_dimension}`,
    )
    expect(deps.status).toBe('ok')
  })

  afterAll(async () => {
    // Cascade delete keeps the demo workspace clean between runs.
    if (created.projectId) {
      try {
        await api.projects.remove(created.projectId)
        log(`cleanup: deleted project ${created.projectId}`)
      } catch (error) {
        log(`cleanup failed (non-fatal): ${String(error)}`)
      }
    }
  })
})
