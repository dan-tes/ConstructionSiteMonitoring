import { useState } from 'react'
import type { TimelinePhase, TimelinePoint } from '../../types/project'
import { C, DAY_MS, dateTicks, fmtLong, fmtShort, linear, parseDate, useElementWidth } from './chartKit'

const SHORT_NAMES: Record<string, string> = {
  Preconstruction: 'Precon.',
  'Site Preparation': 'Site Prep.',
  'Structural Frame': 'Frame',
  'External Works': 'External',
  Commissioning: 'Commiss.',
}

const ROW = 26

interface Props {
  phases: TimelinePhase[]
  points: TimelinePoint[]
  xDomain: [number, number]
}

/** Graph C — each phase's planned window as a bar, every analysed visit as a
 * dot in the row of the phase it was read as (brighter = more confident),
 * and a tick where the phase most likely started (the delay forecast's own
 * estimate). Dots right of their bar mean the phase is overrunning. */
export function PhaseGantt({ phases, points, xDomain }: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const W = Math.max(width, 280)
  const narrow = W < 560
  const M = { l: narrow ? 84 : 132, r: 16, t: 8, b: 28 }
  const H = M.t + M.b + ROW * phases.length
  const x = linear(xDomain[0], xDomain[1], M.l, W - M.r)
  const rowOf = new Map(phases.map((p, i) => [p.phase.trim().toLowerCase(), i]))
  const rowY = (i: number) => M.t + i * ROW

  const dots = points.flatMap((p, i) => {
    const row = rowOf.get(p.phase.trim().toLowerCase())
    if (row === undefined) return []
    return [{ i, p, cx: x(parseDate(p.date)), cy: rowY(row) + ROW / 2, phase: phases[row] }]
  })
  const today = points.length ? parseDate(points[points.length - 1].date) : null
  const hovered = dots.find((d) => d.i === hover)

  return (
    <div ref={ref} className="relative w-full">
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label="План и факт по фазам" className="block overflow-visible">
          {dateTicks(xDomain[0], xDomain[1], Math.floor((W - M.l - M.r) / 64)).map(({ t, label }) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={M.t} y2={H - M.b} stroke={C.grid} />
              <text x={x(t)} y={H - 8} textAnchor="middle" fontSize={11} fill={C.label}>
                {label}
              </text>
            </g>
          ))}

          {phases.map((p, i) => {
            const y = rowY(i)
            const s = p.plannedStart ? x(parseDate(p.plannedStart)) : null
            const e = p.plannedEnd ? x(parseDate(p.plannedEnd)) : null
            return (
              <g key={p.phase}>
                <text
                  x={M.l - 10}
                  y={y + ROW / 2 + 4}
                  textAnchor="end"
                  fontSize={11}
                  fill={p.status === 'In Progress' ? C.text : C.strong}
                  fontWeight={p.status === 'In Progress' ? 600 : 400}
                >
                  {narrow ? (SHORT_NAMES[p.phase] ?? p.phase) : p.phase}
                </text>
                {s !== null && e !== null && (
                  <rect x={s + 1} y={y + 6} width={Math.max(2, e - s - 2)} height={ROW - 12} rx={3} fill={C.plan} />
                )}
                {p.estimatedStart && (
                  <line
                    x1={x(parseDate(p.estimatedStart))}
                    x2={x(parseDate(p.estimatedStart))}
                    y1={y + 3}
                    y2={y + ROW - 3}
                    stroke={C.accent}
                    strokeWidth={2}
                    strokeLinecap="round"
                  />
                )}
              </g>
            )
          })}

          {today !== null && (
            <g>
              <line x1={x(today)} x2={x(today)} y1={M.t - 4} y2={H - M.b} stroke={C.strong} strokeWidth={1.5} />
              <text x={x(today) + 5} y={H - M.b - 6} fontSize={11} fill={C.strong}>
                последний выезд
              </text>
            </g>
          )}

          {dots.map(({ i, p, cx, cy }) => (
            <g key={p.entryId}>
              <circle
                cx={cx}
                cy={cy}
                r={hover === i ? 6 : 5}
                fill={hover === i ? C.accentHover : C.accent}
                fillOpacity={0.35 + 0.65 * Math.min(1, Math.max(0, ((p.phaseConfidence ?? 0.6) - 0.5) / 0.45))}
                stroke={C.surface}
                strokeWidth={2}
              />
              <circle
                cx={cx}
                cy={cy}
                r={11}
                fill="transparent"
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
              />
            </g>
          ))}
        </svg>
      )}
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 whitespace-nowrap rounded-lg border border-site-600 bg-site-800 px-2.5 py-2 text-xs text-site-200 shadow-lg shadow-black/40"
          style={{
            left: hovered.cx + (hovered.cx + 240 > W ? -14 : 14),
            top: hovered.cy - 10,
            transform: `translate(${hovered.cx + 240 > W ? '-100%' : '0'}, -100%)`,
          }}
        >
          <b className="font-semibold text-site-100">{fmtLong(parseDate(hovered.p.date))}</b> —{' '}
          {hovered.p.phase}
          {hovered.p.phaseConfidence !== null && ` (${Math.round(hovered.p.phaseConfidence * 100)}%)`}
          {hovered.phase.plannedStart && hovered.phase.plannedEnd && (
            <div className="text-site-400">
              план фазы: {fmtShort(parseDate(hovered.phase.plannedStart))} –{' '}
              {fmtShort(parseDate(hovered.phase.plannedEnd))}
            </div>
          )}
          {hovered.phase.plannedEnd &&
            (() => {
              const over = Math.round(
                (parseDate(hovered.p.date) - parseDate(hovered.phase.plannedEnd)) / DAY_MS,
              )
              return over > 0 ? (
                <div>
                  фаза идёт дольше плана на <b className="font-semibold text-site-100">{over} дн.</b>
                </div>
              ) : null
            })()}
        </div>
      )}
    </div>
  )
}
