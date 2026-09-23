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
  /**
   * Short GPT narrative for this entry (backend's report.py, block 5),
   * grounded in stageSummary/equipmentSummary's own numbers plus each
   * uploaded file's own equipment findings. May contain markdown-style
   * links (`[text](url)`) back to a specific photo/video that supports a
   * claim in the text — render with `LinkifiedText`, not as plain text.
   * Undefined/empty if narrative generation isn't configured or failed;
   * stageSummary/equipmentSummary are unaffected either way.
   */
  narrativeReport?: string
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
  /**
   * Project-level status summary (backend's report.py, block 6), generated
   * from the most recently analysed journal entry. Same as
   * `VideoInsight.narrativeReport` — may contain markdown-style links back
   * to a specific photo/video, render with `LinkifiedText`.
   */
  planStatus: string | null
  entries: JournalEntry[]
  createdAt: string
  /** Set once the project is closed — no new entries or plan changes after that. */
  closedAt: string | null
  /** Plan-vs-actual workbook frozen at close time (backend's progress.py). */
  finalReport: ProjectFile | null
}

/** One phase's planned window vs what the journal has observed (backend's progress.py). */
export interface TimelinePhase {
  phase: string
  phaseOrder: number
  status: 'Completed' | 'In Progress' | 'Planned'
  plannedStart: string | null
  plannedEnd: string | null
  firstDetected: string | null
  lastDetected: string | null
  /** When the phase most likely started — the same estimate the delay forecast uses. */
  estimatedStart: string | null
}

/** One analysed journal entry — one point on each delay chart. */
export interface TimelinePoint {
  entryId: string
  date: string
  phase: string
  phaseConfidence: number | null
  /** Forecast delay at completion, days; positive = behind schedule. */
  delayDays: number | null
  expectedCompletion: string | null
  /** Effective SPI(t): 1 = on schedule, 0.9 = nine plan-days per ten calendar days. */
  spiTime: number | null
}

export interface ProjectTimeline {
  /** Null until the project has a processed plan and at least one entry. */
  plannedStart: string | null
  plannedFinish: string | null
  phases: TimelinePhase[]
  points: TimelinePoint[]
}
