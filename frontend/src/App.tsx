import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { ProtectedRoute, PublicOnlyRoute } from '@/routes/ProtectedRoute'
import { LoadingBlock } from '@/components/ui/feedback'

/* Pages are code-split so the shell paints immediately on a cold load. */
const LoginPage = lazy(() => import('@/pages/LoginPage'))
const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage'))
const ProjectDetailPage = lazy(() => import('@/pages/ProjectDetailPage'))
const KnowledgePage = lazy(() => import('@/pages/KnowledgePage'))
const KnowledgeDocumentsPage = lazy(() => import('@/pages/KnowledgeDocumentsPage'))
const DocumentsPage = lazy(() => import('@/pages/DocumentsPage'))
const SearchPage = lazy(() => import('@/pages/SearchPage'))
const ChatPage = lazy(() => import('@/pages/ChatPage'))
const PlannerPage = lazy(() => import('@/pages/PlannerPage'))
const TasksPage = lazy(() => import('@/pages/TasksPage'))
const NotesPage = lazy(() => import('@/pages/NotesPage'))
const AgentPage = lazy(() => import('@/pages/AgentPage'))
const AIRunsPage = lazy(() => import('@/pages/AIRunsPage'))
const AIRunDetailPage = lazy(() => import('@/pages/AIRunDetailPage'))
const GraphPage = lazy(() => import('@/pages/GraphPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'))

export default function App() {
  return (
    <Suspense fallback={<LoadingBlock label="加载页面…" className="h-full" />}>
      <Routes>
        <Route element={<PublicOnlyRoute />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:projectId" element={<ProjectDetailPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="knowledge/:kbId/documents" element={<KnowledgeDocumentsPage />} />
            <Route path="documents" element={<DocumentsPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="planner" element={<PlannerPage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="notes" element={<NotesPage />} />
            <Route path="agent" element={<AgentPage />} />
            <Route path="ai-runs" element={<AIRunsPage />} />
            <Route path="ai-runs/:runId" element={<AIRunDetailPage />} />
            <Route path="graph" element={<GraphPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
