import { useEffect, useState } from 'react'
import { projectsApi } from '../../api/projectsApi'
import type { ProjectTimeline } from '../../types/project'
import { PhaseGantt } from './PhaseGantt'
import { type LinePoint, TimeLineChart } from './TimeLineChart'
import {
  C,
  CRITICAL_DAYS,
  DAY_MS,
  WARNING_DAYS,
  dateTicks,
  fmtLong,
  fmtShort,
  niceTicks,
  parseDate,
  signed,
} from './chartKit'

type Tab = 'delay' | 'finish' | 'phases' | 'pace'

const TABS: { id: Tab; label: string; hint: string }[] = [
  {
    id: 'delay',
    label: 'Отставание',
    hint: 'Прогноз отставания к сдаче после каждого выезда. Выше нуля — отстаём, ниже — опережаем.',
  },
  {
    id: 'finish',
    label: 'Прогноз сдачи',
    hint: 'Какой дате сдачи верили после каждого выезда. Линия уходит вверх — срок «уезжает».',
  },
  {
    id: 'phases',
    label: 'Фазы',
    hint: 'Серые полосы — плановые окна фаз, точки — фаза, увиденная на выезде (ярче — увереннее), засечка — оценка фактического начала фазы.',
  },
  {
    id: 'pace',
    label: 'Темп',
    hint: 'SPI(t) — сколько дней плана выполняется за один календарный день. 1,0 — по графику, 0,9 — 9 дней плана за 10 дней.',
  },
]

const TAB_KEY = 'csm.delayCharts.tab'

function readTab(): Tab {
  try {
    const saved = localStorage.getItem(TAB_KEY)
    if (TABS.some((t) => t.id === saved)) return saved as Tab
  } catch {
    // storage unavailable — fall back to the first tab
  }
  return 'delay'
}

const tint = (hex: string, alpha: number) =>
  `${hex}${Math.round(alpha * 255)
    .toString(16)
    .padStart(2, '0')}`

const fmtSpi = (v: number) => v.toFixed(2).replace('.', ',')

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-10 text-center text-sm text-site-500">{children}</p>
}

function Legend({ items }: { items: { swatch: React.CSSProperties; label: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-site-300">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <i className="inline-block rounded-sm" style={it.swatch} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

const LINE = { width: 14, height: 3, background: C.accent }
const DASH = {
  width: 14,
  height: 2,
  background: `repeating-linear-gradient(90deg, ${C.strong} 0 4px, transparent 4px 7px)`,
}

function DelayPill({ days }: { days: number }) {
  const [cls, text] =
    days >= CRITICAL_DAYS
      ? ['bg-red-500/15 text-red-400', 'критично']
      : days >= WARNING_DAYS
        ? ['bg-amber-400/15 text-amber-300', 'внимание']
        : days <= -WARNING_DAYS
          ? ['bg-emerald-500/15 text-emerald-400', 'опережение']
          : ['bg-emerald-500/15 text-emerald-400', 'по графику']
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {days > 0 ? '▲' : days < 0 ? '▼' : '●'} {signed(days)} дн. · {text}
    </span>
  )
}

interface Props {
  projectId: string
  /** Changes whenever an entry is added/removed or finishes analysis — triggers a refetch. */
  refreshKey: string
}

/** Graphs A–D for the project page, fed by GET /projects/{id}/timeline. */
export function DelayChartsSection({ projectId, refreshKey }: Props) {
  const [timeline, setTimeline] = useState<ProjectTimeline | null>(null)
  const [failed, setFailed] = useState(false)
  const [tab, setTab] = useState<Tab>(readTab)

  useEffect(() => {
    let cancelled = false
    projectsApi
      .timeline(projectId)
      .then((t) => {
        if (!cancelled) {
          setTimeline(t)
          setFailed(false)
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, refreshKey])

  const selectTab = (next: Tab) => {
    setTab(next)
    try {
      localStorage.setItem(TAB_KEY, next)
    } catch {
      // not persisted — fine
    }
  }

  const points = timeline?.points ?? []
  const withDelay = points.filter((p) => p.delayDays !== null)
  const latest = withDelay[withDelay.length - 1]

  // A, B and D share one x-axis: the span of analysed visits (at least four
  // weeks, so two early visits don't stretch edge to edge), padded a little.
  const first = points.length ? parseDate(points[0].date) : 0
  const lastT = points.length ? parseDate(points[points.length - 1].date) : 0
  const hiT = Math.max(lastT, first + 28 * DAY_MS)
  const pad = (hiT - first) * 0.03
  const xDomain: [number, number] = [first - pad, hiT + pad]

  const renderChart = () => {
    if (!timeline) {
      return <Empty>{failed ? 'Не удалось загрузить графики.' : 'Загрузка графиков…'}</Empty>
    }
    if (points.length === 0) {
      return <Empty>Графики появятся, когда будет проанализирован первый выезд.</Empty>
    }

    if (tab === 'delay') {
      if (withDelay.length === 0) return <Empty>Для прогноза отставания нужен обработанный план объекта.</Empty>
      const values = withDelay.map((p) => p.delayDays!)
      const lo = Math.min(-10, Math.floor((Math.min(...values) - 5) / 10) * 10)
      const hi = Math.max(20, Math.ceil((Math.max(...values) + 8) / 10) * 10)
      const series: LinePoint[] = withDelay.map((p) => ({
        t: parseDate(p.date),
        v: p.delayDays!,
        tip: (
          <>
            <b className="font-semibold text-site-100">{fmtLong(parseDate(p.date))}</b>
            <div>
              Отставание к сдаче: <b className="font-semibold text-site-100">{signed(p.delayDays!)} дн.</b>
            </div>
            <div className="text-site-400">
              {p.phase}
              {p.phaseConfidence !== null && ` · ${Math.round(p.phaseConfidence * 100)}%`}
            </div>
          </>
        ),
      }))
      return (
        <>
          <Legend
            items={[
              { swatch: LINE, label: 'Прогноз отставания, дн.' },
              { swatch: { width: 14, height: 10, background: tint(C.warning, 0.22) }, label: `≥ ${WARNING_DAYS} дн. — внимание` },
              { swatch: { width: 14, height: 10, background: tint(C.critical, 0.22) }, label: `≥ ${CRITICAL_DAYS} дн. — критично` },
            ]}
          />
          <TimeLineChart
            label="Прогноз отставания к сдаче по выездам"
            points={series}
            xDomain={xDomain}
            yDomain={[lo, hi]}
            yTicks={niceTicks(lo, hi)}
            yFormat={signed}
            bands={[
              { from: WARNING_DAYS, to: CRITICAL_DAYS, color: tint(C.warning, 0.1) },
              { from: CRITICAL_DAYS, to: hi, color: tint(C.critical, 0.11) },
            ]}
            reference={{ v: 0, label: 'по графику' }}
          />
        </>
      )
    }

    if (tab === 'finish') {
      const withFinish = points.filter((p) => p.expectedCompletion !== null)
      if (withFinish.length === 0) return <Empty>Для прогноза сдачи нужен обработанный план объекта.</Empty>
      const values = withFinish.map((p) => parseDate(p.expectedCompletion!))
      const planned = timeline.plannedFinish ? parseDate(timeline.plannedFinish) : null
      const all = planned !== null ? [...values, planned] : values
      const lo = Math.min(...all) - 10 * DAY_MS
      const hi = Math.max(...all) + 10 * DAY_MS
      const series: LinePoint[] = withFinish.map((p) => ({
        t: parseDate(p.date),
        v: parseDate(p.expectedCompletion!),
        tip: (
          <>
            <b className="font-semibold text-site-100">{fmtLong(parseDate(p.date))}</b>
            <div>
              Прогноз сдачи:{' '}
              <b className="font-semibold text-site-100">{fmtLong(parseDate(p.expectedCompletion!))}</b>
            </div>
            {p.delayDays !== null && <div className="text-site-400">сдвиг от плана {signed(p.delayDays)} дн.</div>}
          </>
        ),
      }))
      return (
        <>
          <Legend
            items={[
              { swatch: LINE, label: 'Прогноз даты сдачи' },
              ...(planned !== null ? [{ swatch: DASH, label: 'Плановая сдача' }] : []),
            ]}
          />
          <TimeLineChart
            label="Сдвиг прогнозной даты сдачи по выездам"
            points={series}
            xDomain={xDomain}
            yDomain={[lo, hi]}
            yTicks={dateTicks(lo, hi, 5).map((d) => d.t)}
            yFormat={fmtShort}
            reference={planned !== null ? { v: planned, label: `план: ${fmtShort(planned)}` } : undefined}
          />
        </>
      )
    }

    if (tab === 'phases') {
      if (timeline.phases.length === 0 || !timeline.plannedStart || !timeline.plannedFinish) {
        return <Empty>Загрузите план объекта — здесь появятся плановые окна фаз.</Empty>
      }
      const start = parseDate(timeline.plannedStart)
      const end = Math.max(parseDate(timeline.plannedFinish), lastT) + 7 * DAY_MS
      return (
        <>
          <Legend
            items={[
              { swatch: { width: 14, height: 10, background: C.plan }, label: 'Плановое окно фазы' },
              { swatch: { width: 9, height: 9, borderRadius: 9, background: C.accent }, label: 'Фаза на выезде' },
              { swatch: { width: 2, height: 12, background: C.accent }, label: 'Оценка фактического начала' },
            ]}
          />
          <PhaseGantt phases={timeline.phases} points={points} xDomain={[start - 3 * DAY_MS, end]} />
        </>
      )
    }

    const withSpi = points.filter((p) => p.spiTime !== null)
    if (withSpi.length === 0) {
      return <Empty>Темп работ появится для выездов, проанализированных после обновления.</Empty>
    }
    const values = withSpi.map((p) => p.spiTime!)
    const lo = Math.min(0.6, Math.floor((Math.min(...values) - 0.05) * 10) / 10)
    const hi = Math.max(1.2, Math.ceil((Math.max(...values) + 0.05) * 10) / 10)
    const series: LinePoint[] = withSpi.map((p) => ({
      t: parseDate(p.date),
      v: p.spiTime!,
      tip: (
        <>
          <b className="font-semibold text-site-100">{fmtLong(parseDate(p.date))}</b>
          <div>
            SPI(t): <b className="font-semibold text-site-100">{fmtSpi(p.spiTime!)}</b>
          </div>
          <div className="text-site-400">{Math.round(p.spiTime! * 10)} дн. плана за 10 календарных</div>
        </>
      ),
    }))
    return (
      <>
        <Legend
          items={[
            { swatch: LINE, label: 'SPI(t) с поправкой на уверенность фазы' },
            { swatch: DASH, label: '1,0 — по графику' },
          ]}
        />
        <TimeLineChart
          label="Темп работ SPI(t) по выездам"
          points={series}
          xDomain={xDomain}
          yDomain={[lo, hi]}
          yTicks={niceTicks(lo, hi)}
          yFormat={fmtSpi}
          bands={[{ from: lo, to: 1, color: tint(C.critical, 0.07) }]}
          reference={{ v: 1, label: 'по графику' }}
          height={220}
        />
      </>
    )
  }

  const active = TABS.find((t) => t.id === tab)!

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-lg font-semibold text-site-100">Динамика отставания</h2>
        {latest && <DelayPill days={latest.delayDays!} />}
      </div>
      <div className="mt-3 flex flex-col gap-3 rounded-xl border border-site-700 bg-site-900/50 p-4">
        <div role="tablist" aria-label="Графики" className="flex flex-wrap gap-1 rounded-lg bg-site-950/60 p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={t.id === tab}
              onClick={() => selectTab(t.id)}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition focus-visible:outline-2 focus-visible:outline-safety-400 ${
                t.id === tab ? 'bg-site-700 text-site-100' : 'text-site-400 hover:text-site-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <p className="text-sm text-site-400">{active.hint}</p>
        {renderChart()}
      </div>
    </section>
  )
}
