import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { PageContainer } from '@/components/ui/primitives'
import { EmptyState } from '@/components/ui/feedback'

export default function NotFoundPage() {
  return (
    <PageContainer className="flex min-h-[60vh] items-center justify-center">
      <EmptyState
        icon={<Compass className="size-4" />}
        title="页面不存在"
        description="你访问的路径没有对应的页面，可能链接已失效。"
        action={
          <Link to="/">
            <Button size="sm" variant="primary">
              返回仪表盘
            </Button>
          </Link>
        }
        className="border-0"
      />
    </PageContainer>
  )
}
