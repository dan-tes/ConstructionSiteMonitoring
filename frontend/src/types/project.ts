export interface ProjectFile {
  id: string
  name: string
  type: string
  size: number
  url: string
  kind: 'table' | 'image' | 'video' | 'other'
  /**
   * Backend neural-network analysis. Only ever set for the project plan
   * file (its own normalization status) — a journal video/photo has no
   * insight of its own, see JournalEntry.insight: analysis is combined
   * once per upload batch (entry), not once per individual file.
   */
  insight?: VideoInsight
}

export type VideoInsightStatus = 'pending' | 'analyzing' | 'ready' | 'failed'

/**
 * Result of the backend neural-network analysis — either a plan file's own
 * normalization status, or (on a JournalEntry) the combined analysis across
 * every file uploaded in that batch. Until `status` is `ready` no other
 * field is meaningful yet — the backend has nothing to report until
 * analysis finishes.
 */
export interface VideoInsight {
  status: VideoInsightStatus
  /** Which construction stage (per the project plan) the entry's footage shows. */
  stageSummary?: string
  /** How much and what machinery the model detected across the entry's footage. */
  equipmentSummary?: string
  /** Frames pulled from the footage showing machinery or notable events. May be empty. */
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
  /** Combined analysis for every file in `media` together, computed once
   * the whole batch's vision step is done — see backend's analysis.py
   * (run_entry_analysis/_maybe_advance_entry). */
  insight?: VideoInsight
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
