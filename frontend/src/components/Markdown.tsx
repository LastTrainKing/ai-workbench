import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { citationIndexFromHref, linkifyCitations } from '@/lib/citations'

export interface MarkdownProps {
  children: string
  className?: string
  /** Rewrite `[n]` markers into clickable citation chips. */
  citations?: boolean
  onCitationClick?: (index: number) => void
  activeCitation?: number | null
}

/**
 * Markdown renderer for AI output. Styling lives in `.md-body` (src/index.css) so
 * the same typography applies to the note editor preview and chat answers.
 *
 * When `citations` is enabled, `[n]` markers are first rewritten to
 * `[n](#citation-n)` links so the rest of the Markdown parses normally, then the
 * link renderer swaps those links for chips.
 */
export function Markdown({
  children,
  className,
  citations = false,
  onCitationClick,
  activeCitation = null,
}: MarkdownProps) {
  const source = citations ? linkifyCitations(children) : children

  return (
    <div className={cn('md-body', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, href, children: linkChildren, ...props }) => {
            const index = citationIndexFromHref(href)
            if (index !== null) {
              return (
                <button
                  type="button"
                  onClick={() => onCitationClick?.(index)}
                  title={`跳转到引用 ${index}`}
                  className={cn(
                    'mx-0.5 inline-flex h-[18px] min-w-[18px] translate-y-[-1px] items-center justify-center rounded',
                    'border px-1 font-mono text-2xs font-semibold align-middle transition-colors',
                    activeCitation === index
                      ? 'border-accent bg-accent text-accent-fg'
                      : 'border-accent/40 bg-accent-soft text-accent hover:bg-accent hover:text-accent-fg',
                  )}
                >
                  {index}
                </button>
              )
            }
            return (
              <a {...props} href={href} target="_blank" rel="noreferrer noopener">
                {linkChildren}
              </a>
            )
          },
          table: ({ node: _node, ...props }) => (
            <div className="overflow-x-auto">
              <table {...props} />
            </div>
          ),
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
