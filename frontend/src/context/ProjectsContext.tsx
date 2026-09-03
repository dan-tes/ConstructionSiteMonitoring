import { createContext, type ReactNode, useContext, useState } from 'react'
import { toProjectFile } from '../lib/files'
import type { JournalEntry, Project } from '../types/project'

interface ProjectsContextValue {
  projects: Project[]
  getProject: (id: string) => Project | undefined
  createProject: (name: string, description: string) => Project
  updateProject: (id: string, patch: { name?: string; description?: string }) => void
  setProjectPlan: (id: string, file: File | null) => void
  addJournalEntry: (
    id: string,
    comment: string,
    mediaFiles: File[],
    date: string,
    author: string,
  ) => void
}

const ProjectsContext = createContext<ProjectsContextValue | null>(null)

// In-memory only for now: projects, plans and journal media reset on page reload.
// Once a real backend exists, these actions become HTTP calls and file uploads
// (see toProjectFile / URL.createObjectURL usage) get replaced with server uploads.
export function ProjectsProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])

  const getProject = (id: string) => projects.find((p) => p.id === id)

  const createProject = (name: string, description: string) => {
    const project: Project = {
      id: crypto.randomUUID(),
      name: name.trim(),
      description: description.trim(),
      plan: null,
      entries: [],
      createdAt: new Date().toISOString(),
    }
    setProjects((prev) => [project, ...prev])
    return project
  }

  const updateProject = (id: string, patch: { name?: string; description?: string }) => {
    setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, ...patch } : p)))
  }

  const setProjectPlan = (id: string, file: File | null) => {
    setProjects((prev) =>
      prev.map((p) => (p.id === id ? { ...p, plan: file ? toProjectFile(file) : null } : p)),
    )
  }

  const addJournalEntry = (
    id: string,
    comment: string,
    mediaFiles: File[],
    date: string,
    author: string,
  ) => {
    const entry: JournalEntry = {
      id: crypto.randomUUID(),
      comment: comment.trim(),
      author,
      date,
      media: mediaFiles.map(toProjectFile),
    }
    setProjects((prev) =>
      prev.map((p) => (p.id === id ? { ...p, entries: [entry, ...p.entries] } : p)),
    )
  }

  const value = { projects, getProject, createProject, updateProject, setProjectPlan, addJournalEntry }

  return <ProjectsContext.Provider value={value}>{children}</ProjectsContext.Provider>
}

export function useProjects() {
  const ctx = useContext(ProjectsContext)
  if (!ctx) throw new Error('useProjects must be used within ProjectsProvider')
  return ctx
}
