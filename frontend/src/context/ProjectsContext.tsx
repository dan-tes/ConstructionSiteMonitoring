import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { projectsApi } from '../api/projectsApi'
import { useAuth } from './AuthContext'
import type { JournalEntry, Project, VideoInsight } from '../types/project'

const ANALYSIS_POLL_MS = 4000

interface ProjectsContextValue {
  projects: Project[]
  loading: boolean
  error: string | null
  getProject: (id: string) => Project | undefined
  refreshProject: (id: string) => Promise<void>
  createProject: (name: string, description: string) => Promise<Project>
  updateProject: (id: string, patch: { name?: string; description?: string }) => Promise<void>
  setProjectPlan: (id: string, file: File | null) => Promise<void>
  downloadCanonicalPlan: (id: string) => Promise<Blob>
  addJournalEntry: (id: string, mediaFiles: File[], author: string, date: string) => Promise<void>
  /** Re-fetch one journal entry's combined analysis and merge it in. Returns the new status. */
  refreshEntry: (projectId: string, entryId: string) => Promise<VideoInsight['status'] | undefined>
}

const ProjectsContext = createContext<ProjectsContextValue | null>(null)

const isPending = (status?: VideoInsight['status']) =>
  status === 'pending' || status === 'analyzing'

export function ProjectsProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())

  const upsert = useCallback((project: Project) => {
    setProjects((prev) => {
      const exists = prev.some((p) => p.id === project.id)
      return exists ? prev.map((p) => (p.id === project.id ? project : p)) : [project, ...prev]
    })
  }, [])

  useEffect(() => {
    if (status !== 'authenticated') {
      setProjects([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    projectsApi
      .list()
      .then((list) => {
        if (!cancelled) setProjects(list)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Не удалось загрузить объекты')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [status])

  useEffect(() => {
    const timers = pollTimers.current
    return () => timers.forEach(clearInterval)
  }, [])

  const getProject = (id: string) => projects.find((p) => p.id === id)

  const refreshProject = useCallback(
    async (id: string) => {
      upsert(await projectsApi.get(id))
    },
    [upsert],
  )

  const createProject = useCallback(async (name: string, description: string) => {
    const project = await projectsApi.create(name.trim(), description.trim())
    setProjects((prev) => [project, ...prev])
    return project
  }, [])

  const updateProject = useCallback(
    async (id: string, patch: { name?: string; description?: string }) => {
      upsert(await projectsApi.update(id, patch))
    },
    [upsert],
  )

  const setProjectPlan = useCallback(async (id: string, file: File | null) => {
    if (file) {
      const plan = await projectsApi.uploadPlan(id, file)
      setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, plan } : p)))
    } else {
      await projectsApi.deletePlan(id)
      setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, plan: null } : p)))
    }
  }, [])

  const downloadCanonicalPlan = useCallback((id: string) => projectsApi.downloadCanonicalPlan(id), [])

  const patchEntry = useCallback((projectId: string, entryId: string, insight?: VideoInsight) => {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? {
              ...p,
              entries: p.entries.map((e) => (e.id === entryId ? { ...e, insight } : e)),
            }
          : p,
      ),
    )
  }, [])

  const refreshEntry = useCallback(
    async (projectId: string, entryId: string) => {
      const entry = await projectsApi.getEntry(projectId, entryId)
      patchEntry(projectId, entryId, entry.insight)
      // A finished entry may have produced a fresh project-level summary.
      if (!isPending(entry.insight?.status)) {
        projectsApi.get(projectId).then(upsert).catch(() => {})
      }
      return entry.insight?.status
    },
    [patchEntry, upsert],
  )

  const pollUntilReady = useCallback(
    (projectId: string, entryId: string) => {
      const timers = pollTimers.current
      if (timers.has(entryId)) return
      const timer = setInterval(async () => {
        try {
          const next = await refreshEntry(projectId, entryId)
          if (!isPending(next)) {
            clearInterval(timer)
            timers.delete(entryId)
          }
        } catch {
          // transient error — keep polling
        }
      }, ANALYSIS_POLL_MS)
      timers.set(entryId, timer)
    },
    [refreshEntry],
  )

  const addJournalEntry = useCallback(
    async (id: string, mediaFiles: File[], author: string, date: string) => {
      const entry: JournalEntry = await projectsApi.uploadVideos(id, mediaFiles, author, date)
      setProjects((prev) =>
        prev.map((p) => (p.id === id ? { ...p, entries: [entry, ...p.entries] } : p)),
      )
      if (isPending(entry.insight?.status)) pollUntilReady(id, entry.id)
    },
    [pollUntilReady],
  )

  const value: ProjectsContextValue = {
    projects,
    loading,
    error,
    getProject,
    refreshProject,
    createProject,
    updateProject,
    setProjectPlan,
    downloadCanonicalPlan,
    addJournalEntry,
    refreshEntry,
  }

  return <ProjectsContext.Provider value={value}>{children}</ProjectsContext.Provider>
}

export function useProjects() {
  const ctx = useContext(ProjectsContext)
  if (!ctx) throw new Error('useProjects must be used within ProjectsProvider')
  return ctx
}
