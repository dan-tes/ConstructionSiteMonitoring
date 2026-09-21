export interface ProjectFile {
  id: string
  name: string
  type: string
  size: number
  url: string
  kind: 'table' | 'image' | 'video' | 'other'
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
  /**
   * Experimental secondary signal: phase read directly off the footage by a
   * visual classifier (no equipment detection involved), independent of
   * `status`/`stageSummary` — may be set before `status` is `ready`, or stay
   * unset after. Only ever one of Earthwork/Foundation/Structural Frame/
   * External Works — the classifier has no training coverage for the other
   * six canonical phases, so don't treat a missing value as "not started
   * yet" the way you would for stageSummary.
   */
  visualPhaseName?: string
  /** Relative confidence among the classifier's clusters, not a calibrated probability. */
  visualPhaseConfidence?: number
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
