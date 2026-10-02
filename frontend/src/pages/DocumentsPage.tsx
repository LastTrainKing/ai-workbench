import { useSearchParams } from 'react-router-dom'
import { DocumentsPanel } from '@/features/documents/DocumentsPanel'

export default function DocumentsPage() {
  const [params] = useSearchParams()
  const docId = params.get('doc')
  const upload = params.get('upload') === '1'

  return (
    <DocumentsPanel
      initialDocumentId={docId}
      initialUploadOpen={upload}
      heading="全部文档"
      description="跨知识库浏览所有文档，支持状态筛选与实时进度刷新。"
    />
  )
}
