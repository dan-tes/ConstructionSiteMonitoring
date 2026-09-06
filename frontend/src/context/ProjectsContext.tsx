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
import { todayDateString } from '../lib/date'
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
  addJournalEntry: (id: string, mediaFiles: File[], author: string) => Promise<void>
  /** Re-fetch one video's analysis and merge it in. Returns the new status. */
  refreshVideo: (projectId: string, videoId: string) => Promise<VideoInsight['status'] | undefined>
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

  const patchVideo = useCallback((projectId: string, videoId: string, insight?: VideoInsight) => {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? {
              ...p,
              entries: p.entries.map((e) => ({
                ...e,
                media: e.media.map((m) => (m.id === videoId ? { ...m, insight } : m)),
              })),
            }
          : p,
      ),
    )
  }, [])

  const refreshVideo = useCallback(
    async (projectId: string, videoId: string) => {
      const video = await projectsApi.getVideo(projectId, videoId)
      patchVideo(projectId, videoId, video.insight)
      // A finished video may have produced a fresh project-level summary.
      if (!isPending(video.insight?.status)) {
        projectsApi.get(projectId).then(upsert).catch(() => {})
      }
      return video.insight?.status
    },
    [patchVideo, upsert],
  )

  const pollUntilReady = useCallback(
    (projectId: string, videoId: string) => {
      const timers = pollTimers.current
      if (timers.has(videoId)) return
      const timer = setInterval(async () => {
        try {
          const next = await refreshVideo(projectId, videoId)
          if (!isPending(next)) {
            clearInterval(timer)
            timers.delete(videoId)
          }
        } catch {
          // transient error — keep polling
        }
      }, ANALYSIS_POLL_MS)
      timers.set(videoId, timer)
    },
    [refreshVideo],
  )

  const addJournalEntry = useCallback(
    async (id: string, mediaFiles: File[], author: string) => {
      const entry: JournalEntry = await projectsApi.uploadVideos(
        id,
        mediaFiles,
        author,
        todayDateString(),
      )
      setProjects((prev) =>
        prev.map((p) => (p.id === id ? { ...p, entries: [entry, ...p.entries] } : p)),
      )
      for (const video of entry.media) {
        if (isPending(video.insight?.status)) pollUntilReady(id, video.id)
      }
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
    addJournalEntry,
    refreshVideo,
  }

  return <ProjectsContext.Provider value={value}>{children}</ProjectsContext.Provider>
}

export function useProjects() {
  const ctx = useContext(ProjectsContext)
  if (!ctx) throw new Error('useProjects must be used within ProjectsProvider')
  return ctx
}
