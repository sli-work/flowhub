import { useApp } from './store/app-store'
import { Sidebar, Topbar } from './components/layout'
import { DialogHost } from './components/dialogs'
import { LoginPage } from './pages/login'
import { MyTasksPage } from './pages/tasks'
import { ProjectsPage } from './pages/projects'
import { WorkItemPage } from './pages/workitem'
import { NodeProcessPage } from './pages/node'
import { TemplatesPage } from './pages/templates'
import { CanvasPage } from './pages/canvas'
import { DocumentsPage } from './pages/documents'
import { OrgPage } from './pages/org'
import { PermMatrixPage } from './pages/matrix'
import { NotificationsPage } from './pages/notifications'
import { ChannelsPage } from './pages/channels'
import { AuditPage } from './pages/audit'
import { DashboardPage } from './pages/dashboard'
import { ExpertOsOverviewPage } from './pages/expert-os-overview'
import { AiChatPage } from './pages/aichat'
import { ExpertCenterPage } from './pages/expert-center'
import { ExpertEditorPage } from './pages/expert-editor'
import { ResourceCenterPage } from './pages/expert-resources'
import { RuntimeCenterPage, ApprovalsPage } from './pages/runtime-center'
import { ExternalToolsPage } from './pages/external-tools'

function PageRouter() {
  const { page } = useApp()
  switch (page) {
    case 'os-overview': return <ExpertOsOverviewPage />
    case 'aichat': return <AiChatPage />
    case 'expert-center': return <ExpertCenterPage />
    case 'expert-editor': return <ExpertEditorPage />
    case 'skill-center': return <ResourceCenterPage kind="skill" />
    case 'mcp-center': return <ResourceCenterPage kind="mcp" />
    case 'provider-center': return <ResourceCenterPage kind="provider" />
    case 'knowledge': return <ResourceCenterPage kind="knowledge" />
    case 'memory': return <ResourceCenterPage kind="memory" />
    case 'runtime-center': return <RuntimeCenterPage />
    case 'approvals': return <ApprovalsPage />
    case 'external-tools': return <ExternalToolsPage />
    case 'tasks': return <MyTasksPage />
    case 'notif': return <NotificationsPage />
    case 'channel': return <ChannelsPage />
    case 'dashboard': return <DashboardPage />
    case 'projects': return <ProjectsPage />
    case 'workitem': return <WorkItemPage />
    case 'node': return <NodeProcessPage />
    case 'templates': return <TemplatesPage />
    case 'canvas': return <CanvasPage />
    case 'docs': return <DocumentsPage />
    case 'org': return <OrgPage />
    case 'matrix': return <PermMatrixPage />
    case 'audit': return <AuditPage />
    default: return <MyTasksPage />
  }
}

export default function App() {
  const { authed } = useApp()

  if (!authed) return <LoginPage />

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col pl-[60px] md:pl-[224px]">
        <Topbar />
        <main className="flex-1 overflow-y-auto">
          <PageRouter />
        </main>
      </div>
      <DialogHost />
    </div>
  )
}
