import type { JournalEntry, Project, ProjectFile } from '../types/project'
import { request } from './client'

export const projectsApi = {
  list(): Promise<Project[]> {
    return request<Project[]>('/projects')
  },

  get(id: string): Promise<Project> {
    return request<Project>(`/projects/${id}`)
  },

  create(name: string, description: string): Promise<Project> {
    return request<Project>('/projects', { method: 'POST', body: { name, description } })
  },

  update(id: string, patch: { name?: string; description?: string }): Promise<Project> {
    return request<Project>(`/projects/${id}`, { method: 'PATCH', body: patch })
  },

  uploadPlan(id: string, file: File): Promise<ProjectFile> {
    const form = new FormData()
    form.append('file', file)
    return request<ProjectFile>(`/projects/${id}/plan`, { method: 'POST', body: form })
  },

  deletePlan(id: string): Promise<void> {
    return request<void>(`/projects/${id}/plan`, { method: 'DELETE' })
  },

  uploadVideos(
    id: string,
    files: File[],
    author: string,
    date: string,
  ): Promise<JournalEntry> {
    const form = new FormData()
    form.append('author', author)
    form.append('date', date)
    for (const file of files) form.append('files', file)
    return request<JournalEntry>(`/projects/${id}/entries`, { method: 'POST', body: form })
  },

  getVideo(id: string, videoId: string): Promise<ProjectFile> {
    return request<ProjectFile>(`/projects/${id}/videos/${videoId}`)
  },
}
