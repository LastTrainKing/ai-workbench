import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { Maximize2, Network, RefreshCw, ZoomIn, ZoomOut } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { Badge, Card, CardBody, PageContainer, SectionHeader } from '@/components/ui/primitives'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/feedback'
import type { GraphNodeType, GraphResponse } from '@/lib/types'

const TYPE_COLOR: Record<GraphNodeType, string> = {
  project: '#6366f1',
  knowledge_base: '#8b5cf6',
  document: '#3b9eff',
  concept: '#10b981',
  task: '#f59e0b',
  note: '#ec4899',
  ai_run: '#7c8598',
}

const TYPE_LABEL: Record<GraphNodeType, string> = {
  project: '项目',
  knowledge_base: '知识库',
  document: '文档',
  concept: '概念',
  task: '任务',
  note: '笔记',
  ai_run: 'AI 运行',
}

const TYPE_RADIUS: Record<GraphNodeType, number> = {
  project: 15,
  knowledge_base: 12,
  document: 8,
  concept: 6,
  task: 7,
  note: 7,
  ai_run: 5,
}

interface SimNode extends SimulationNodeDatum {
  id: string
  type: GraphNodeType
  label: string
  meta: Record<string, unknown>
  radius: number
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  source: SimNode | string
  target: SimNode | string
  type: string
  weight: number
}

interface Viewport {
  k: number
  x: number
  y: number
}

const WIDTH = 1000
const HEIGHT = 620

/** Run the layout synchronously so the first paint is already stable. */
function layout(nodes: SimNode[], links: SimLink[]): { nodes: SimNode[]; links: SimLink[] } {
  const simulation = forceSimulation<SimNode>(nodes)
    .force(
      'link',
      forceLink<SimNode, SimLink>(links)
        .id((node) => node.id)
        .distance((link) => 46 + (1 - Math.min(1, link.weight)) * 40)
        .strength(0.35),
    )
    .force('charge', forceManyBody<SimNode>().strength((node) => -110 - node.radius * 6))
    .force('center', forceCenter(WIDTH / 2, HEIGHT / 2))
    .force('collide', forceCollide<SimNode>().radius((node) => node.radius + 3))
    .stop()

  const ticks = Math.min(320, Math.max(120, nodes.length * 3))
  for (let i = 0; i < ticks; i += 1) simulation.tick()

  return { nodes, links }
}

function nodeRoute(node: SimNode): string | null {
  const [, rawId] = node.id.split(':')
  if (!rawId) return null
  switch (node.type) {
    case 'project':
      return `/projects/${rawId}`
    case 'document':
      return `/documents?doc=${rawId}`
    case 'knowledge_base':
      return `/knowledge/${rawId}/documents`
    case 'task':
      return `/tasks?task=${rawId}`
    case 'note':
      return `/notes?note=${rawId}`
    case 'ai_run':
      return `/ai-runs/${rawId}`
    default:
      return null
  }
}

export default function GraphPage() {
  const navigate = useNavigate()
  const [includeConcepts, setIncludeConcepts] = useState(true)
  const [includeRuns, setIncludeRuns] = useState(false)
  const [limit, setLimit] = useState(200)
  const [viewport, setViewport] = useState<Viewport>({ k: 1, x: 0, y: 0 })
  const [hovered, setHovered] = useState<{ node: SimNode; x: number; y: number } | null>(null)

  const dragRef = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['graph', { includeConcepts, includeRuns, limit }],
    queryFn: ({ signal }) =>
      api.insights.graph(
        { include_concepts: includeConcepts, include_runs: includeRuns, limit },
        signal,
      ),
    staleTime: 60_000,
  })

  const graph: GraphResponse | undefined = data

  const simulated = useMemo(() => {
    if (!graph || graph.nodes.length === 0) return { nodes: [] as SimNode[], links: [] as SimLink[] }
    const nodes: SimNode[] = graph.nodes.map((node) => ({
      id: node.id,
      type: node.type,
      label: node.label,
      meta: node.meta ?? {},
      radius: TYPE_RADIUS[node.type] ?? 6,
    }))
    const links: SimLink[] = graph.edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      type: edge.type,
      weight: edge.weight,
    }))
    return layout(nodes, links)
  }, [graph])

  // Fit the graph into the viewport whenever a new layout lands.
  useEffect(() => {
    if (simulated.nodes.length === 0) return
    const xs = simulated.nodes.map((node) => node.x ?? 0)
    const ys = simulated.nodes.map((node) => node.y ?? 0)
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)
    const spanX = Math.max(1, maxX - minX)
    const spanY = Math.max(1, maxY - minY)
    const k = Math.min(0.95, Math.min((WIDTH - 80) / spanX, (HEIGHT - 80) / spanY))
    setViewport({
      k,
      x: WIDTH / 2 - ((minX + maxX) / 2) * k,
      y: HEIGHT / 2 - ((minY + maxY) / 2) * k,
    })
  }, [simulated])

  const onWheel = useCallback((event: React.WheelEvent<SVGSVGElement>) => {
    event.preventDefault()
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const px = ((event.clientX - rect.left) / rect.width) * WIDTH
    const py = ((event.clientY - rect.top) / rect.height) * HEIGHT
    setViewport((prev) => {
      const nextK = Math.max(0.15, Math.min(4, prev.k * (event.deltaY < 0 ? 1.12 : 0.89)))
      const ratio = nextK / prev.k
      return {
        k: nextK,
        x: px - (px - prev.x) * ratio,
        y: py - (py - prev.y) * ratio,
      }
    })
  }, [])

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    dragRef.current = {
      x: ((event.clientX - rect.left) / rect.width) * WIDTH,
      y: ((event.clientY - rect.top) / rect.height) * HEIGHT,
      vx: viewport.x,
      vy: viewport.y,
    }
    ;(event.target as Element).setPointerCapture?.(event.pointerId)
  }

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current) return
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const px = ((event.clientX - rect.left) / rect.width) * WIDTH
    const py = ((event.clientY - rect.top) / rect.height) * HEIGHT
    setViewport((prev) => ({
      k: prev.k,
      x: dragRef.current!.vx + (px - dragRef.current!.x),
      y: dragRef.current!.vy + (py - dragRef.current!.y),
    }))
  }

  const endDrag = () => {
    dragRef.current = null
  }

  const zoomBy = (factor: number) =>
    setViewport((prev) => {
      const nextK = Math.max(0.15, Math.min(4, prev.k * factor))
      const ratio = nextK / prev.k
      const cx = WIDTH / 2
      const cy = HEIGHT / 2
      return { k: nextK, x: cx - (cx - prev.x) * ratio, y: cy - (cy - prev.y) * ratio }
    })

  const types = useMemo(() => {
    const set = new Set<GraphNodeType>()
    for (const node of graph?.nodes ?? []) set.add(node.type)
    return [...set]
  }, [graph])

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="知识图谱"
        description="项目、知识库、文档、概念、任务与 AI 运行之间的关系网络，可缩放拖拽并点击跳转。"
        actions={
          <>
            <Button
              size="sm"
              variant={includeConcepts ? 'primary' : 'secondary'}
              onClick={() => setIncludeConcepts((value) => !value)}
            >
              概念节点
            </Button>
            <Button
              size="sm"
              variant={includeRuns ? 'primary' : 'secondary'}
              onClick={() => setIncludeRuns((value) => !value)}
            >
              AI 运行节点
            </Button>
            <IconButton label="刷新" size="sm" disabled={isFetching} onClick={() => void refetch()}>
              <RefreshCw className={cn('size-3.5', isFetching && 'animate-spin')} />
            </IconButton>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-2xs text-faint">节点上限</span>
        {[100, 200, 400].map((value) => (
          <Button
            key={value}
            size="xs"
            variant={limit === value ? 'primary' : 'ghost'}
            onClick={() => setLimit(value)}
          >
            {value}
          </Button>
        ))}
        {graph ? (
          <>
            <Badge tone="neutral">节点 {graph.stats.nodes}</Badge>
            <Badge tone="neutral">边 {graph.stats.edges}</Badge>
            {graph.stats.truncated ? <Badge tone="warn">已截断</Badge> : null}
          </>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {types.map((type) => (
          <span key={type} className="flex items-center gap-1.5 text-2xs text-muted">
            <span className="size-2 rounded-full" style={{ backgroundColor: TYPE_COLOR[type] }} />
            {TYPE_LABEL[type]}
            <span className="text-faint tabular-nums">{graph?.stats.by_type?.[type] ?? 0}</span>
          </span>
        ))}
      </div>

      {isLoading ? (
        <Skeleton className="h-[520px]" />
      ) : isError ? (
        <ErrorState title="图谱加载失败" error={error} onRetry={() => void refetch()} />
      ) : simulated.nodes.length === 0 ? (
        <EmptyState
          icon={<Network className="size-4" />}
          title="图谱还是空的"
          description="创建项目、知识库并上传文档后，节点与关系会自动出现在这里。"
        />
      ) : (
        <Card className="relative overflow-hidden">
          <div className="absolute right-2.5 top-2.5 z-10 flex flex-col gap-1">
            <IconButton label="放大" size="xs" onClick={() => zoomBy(1.2)}>
              <ZoomIn className="size-3.5" />
            </IconButton>
            <IconButton label="缩小" size="xs" onClick={() => zoomBy(0.83)}>
              <ZoomOut className="size-3.5" />
            </IconButton>
            <IconButton
              label="适应窗口"
              size="xs"
              onClick={() => {
                const xs = simulated.nodes.map((node) => node.x ?? 0)
                const ys = simulated.nodes.map((node) => node.y ?? 0)
                const spanX = Math.max(1, Math.max(...xs) - Math.min(...xs))
                const spanY = Math.max(1, Math.max(...ys) - Math.min(...ys))
                const k = Math.min(0.95, Math.min((WIDTH - 80) / spanX, (HEIGHT - 80) / spanY))
                setViewport({
                  k,
                  x: WIDTH / 2 - ((Math.min(...xs) + Math.max(...xs)) / 2) * k,
                  y: HEIGHT / 2 - ((Math.min(...ys) + Math.max(...ys)) / 2) * k,
                })
              }}
            >
              <Maximize2 className="size-3.5" />
            </IconButton>
          </div>

          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="h-[520px] w-full cursor-grab touch-none active:cursor-grabbing"
            onWheel={onWheel}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerLeave={() => {
              endDrag()
              setHovered(null)
            }}
            role="img"
            aria-label="知识图谱关系网络"
          >
            <g transform={`translate(${viewport.x},${viewport.y}) scale(${viewport.k})`}>
              {simulated.links.map((link, index) => {
                const source = link.source as SimNode
                const target = link.target as SimNode
                if (!source?.x || !target?.x) return null
                const highlighted =
                  hovered && (hovered.node.id === source.id || hovered.node.id === target.id)
                return (
                  <line
                    key={index}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    stroke={highlighted ? 'rgb(var(--c-accent))' : 'rgb(var(--c-border-strong))'}
                    strokeWidth={highlighted ? 1.6 : 0.8}
                    strokeOpacity={hovered ? (highlighted ? 0.95 : 0.25) : 0.55}
                  />
                )
              })}

              {simulated.nodes.map((node) => {
                const dimmed = hovered && hovered.node.id !== node.id
                return (
                  <g
                    key={node.id}
                    transform={`translate(${node.x ?? 0},${node.y ?? 0})`}
                    opacity={dimmed ? 0.45 : 1}
                    onPointerEnter={(event) => {
                      const rect = svgRef.current?.getBoundingClientRect()
                      if (!rect) return
                      setHovered({
                        node,
                        x: event.clientX - rect.left,
                        y: event.clientY - rect.top,
                      })
                    }}
                    onPointerLeave={() => setHovered(null)}
                    onClick={() => {
                      const route = nodeRoute(node)
                      if (route) navigate(route)
                    }}
                    className={nodeRoute(node) ? 'cursor-pointer' : 'cursor-default'}
                  >
                    <circle
                      r={node.radius}
                      fill={TYPE_COLOR[node.type]}
                      fillOpacity={0.85}
                      stroke={hovered?.node.id === node.id ? 'rgb(var(--c-text))' : 'transparent'}
                      strokeWidth={1.5}
                    />
                    {node.radius >= 7 && viewport.k > 0.6 ? (
                      <text
                        y={node.radius + 10}
                        textAnchor="middle"
                        fontSize={8}
                        fill="rgb(var(--c-muted))"
                        style={{ pointerEvents: 'none' }}
                      >
                        {node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label}
                      </text>
                    ) : null}
                  </g>
                )
              })}
            </g>
          </svg>

          {hovered ? (
            <div
              className="pointer-events-none absolute z-20 max-w-[260px] rounded-md border border-subtle bg-elevated px-2.5 py-2 shadow-pop"
              style={{ left: hovered.x + 12, top: hovered.y + 12 }}
            >
              <p className="text-2xs font-medium text-primary">{hovered.node.label}</p>
              <p className="font-mono text-2xs text-faint">
                {TYPE_LABEL[hovered.node.type]} · {hovered.node.id.split(':')[1]?.slice(0, 8)}
              </p>
              {Object.entries(hovered.node.meta ?? {}).slice(0, 4).map(([key, value]) => (
                <p key={key} className="truncate text-2xs text-muted">
                  {key}: {String(value)}
                </p>
              ))}
              {nodeRoute(hovered.node) ? (
                <p className="mt-0.5 text-2xs text-accent">点击跳转</p>
              ) : null}
            </div>
          ) : null}
        </Card>
      )}

      <Card>
        <CardBody className="flex flex-wrap items-center gap-x-6 gap-y-2 text-2xs text-faint">
          <span>滚轮缩放 · 拖拽平移 · 点击节点跳转</span>
          <span>布局：d3-force（斥力 + 弹簧 + 碰撞）</span>
        </CardBody>
      </Card>
    </PageContainer>
  )
}
