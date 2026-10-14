import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { ProtectedRoute, PublicOnlyRoute, RequireOnboarding } from '@/routes/ProtectedRoute'
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
const AgentWorkbenchPage = lazy(() => import('@/pages/AgentWorkbenchPage'))
const AgentPermissionsPage = lazy(() => import('@/pages/AgentPermissionsPage'))
const HomePage = lazy(() => import('@/pages/HomePage'))
const ConversationPage = lazy(() => import('@/pages/ConversationPage'))
const WorkbenchPage = lazy(() => import('@/pages/WorkbenchPage'))
const MyAIPage = lazy(() => import('@/pages/MyAIPage'))
const OnboardingPage = lazy(() => import('@/pages/OnboardingPage'))
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
          {/* `/welcome` is deliberately outside the onboarding guard: it is where the
              guard sends people, so putting it inside would loop. */}
          <Route path="welcome" element={<OnboardingPage />} />

          <Route element={<RequireOnboarding />}>
            <Route element={<AppShell />}>
              {/* `/` is the conversation, and `/c/:id` is one that was reopened from the
                  list. The four-area home it replaced still exists at `/home` rather than
                  being deleted — nothing was removed to make the new screen look simpler,
                  and the old routes all still resolve (verify-sidebar.mjs counts them). */}
              <Route index element={<ConversationPage />} />
              <Route path="c/:id" element={<ConversationPage />} />
              <Route path="home" element={<HomePage />} />
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="workbench" element={<WorkbenchPage />} />
              <Route path="workbench/:runId" element={<WorkbenchPage />} />
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
              <Route path="agent" element={<AgentWorkbenchPage />} />
              <Route path="ai-runs" element={<AIRunsPage />} />
              <Route path="ai-runs/:runId" element={<AIRunDetailPage />} />
              <Route path="graph" element={<GraphPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="settings/agent" element={<AgentPermissionsPage />} />
              <Route path="me" element={<MyAIPage />} />
              <Route path="me/:tab" element={<MyAIPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
