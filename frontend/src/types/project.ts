export interface ProjectFile {
  id: string
  name: string
  type: string
  size: number
  url: string
  kind: 'image' | 'video' | 'other'
}

export interface JournalEntry {
  id: string
  comment: string
  author: string
  date: string
  media: ProjectFile[]
}

export interface Project {
  id: string
  name: string
  description: string
  plan: ProjectFile | null
  entries: JournalEntry[]
  createdAt: string
}
