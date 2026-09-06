export interface ProjectFile {
  id: string
  name: string
  type: string
  size: number
  url: string
  kind: 'image' | 'video' | 'other'
  /** Backend neural-network analysis, present only on uploaded videos. */
  insight?: VideoInsight
}

export type VideoInsightStatus = 'pending' | 'analyzing' | 'ready' | 'failed'

/**
 * Result of the backend neural-network analysis of an uploaded video.
 * Until `status` is `ready` no other field is meaningful yet — the backend
 * has nothing to report about the video until analysis finishes.
 */
export interface VideoInsight {
  status: VideoInsightStatus
  /** Which construction stage (per the project plan) the footage shows. */
  stageSummary?: string
  /** How much and what machinery the model detected in the footage. */
  equipmentSummary?: string
  /** Frames pulled from the video showing machinery or notable events. May be empty. */
  photos?: ProjectFile[]
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
  /** Project-level summary generated from the latest analysed video. */
  planStatus: string | null
  entries: JournalEntry[]
  createdAt: string
}
