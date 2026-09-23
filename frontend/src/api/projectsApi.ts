import type { JournalEntry, Project, ProjectFile, ProjectTimeline } from '../types/project'
import { request, requestBlob } from './client'

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

  /** The project's plan re-expressed in our canonical .xlsx format (see
   * backend/plan_parser.py) plus the current fact from the journal (phase
   * statuses, `actuals`/`history` sheets — backend/progress.py). Built fresh
   * on every download; available once the plan has been parsed/normalized,
   * regardless of whether the original upload already was canonical or
   * needed the LLM fallback. */
  downloadCanonicalPlan(id: string): Promise<Blob> {
    return requestBlob(`/projects/${id}/plan/canonical`)
  },

  /** Series for the project page's delay charts (backend's GET /projects/{id}/timeline). */
  timeline(id: string): Promise<ProjectTimeline> {
    return request<ProjectTimeline>(`/projects/${id}/timeline`)
  },

  /** Freeze the project: stores the final plan-vs-actual report and blocks further changes. */
  close(id: string): Promise<Project> {
    return request<Project>(`/projects/${id}/close`, { method: 'POST' })
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

  getEntry(id: string, entryId: string): Promise<JournalEntry> {
    return request<JournalEntry>(`/projects/${id}/entries/${entryId}`)
  },
}
