import { type ReactNode, useState } from 'react'
import { C, dateTicks, linear, useElementWidth } from './chartKit'

export interface LinePoint {
  /** x — UTC ms */
  t: number
  v: number
  tip: ReactNode
}

export interface Band {
  from: number
  to: number
  color: string
}

interface Props {
  points: LinePoint[]
  xDomain: [number, number]
  yDomain: [number, number]
  yTicks: number[]
  yFormat: (v: number) => string
  bands?: Band[]
  reference?: { v: number; label?: string }
  height?: number
  label: string
}

const M = { l: 58, r: 44, t: 12, b: 28 }

/** One series over calendar time: thin line, emphasised last point with a
 * direct label, dashed reference line, optional threshold bands, and a
 * crosshair tooltip snapping to the nearest point. */
export function TimeLineChart({
  points,
  xDomain,
  yDomain,
  yTicks,
  yFormat,
  bands = [],
  reference,
  height = 240,
  label,
}: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const W = Math.max(width, 280)
  const H = height
  const x = linear(xDomain[0], xDomain[1], M.l, W - M.r)
  const y = linear(yDomain[0], yDomain[1], H - M.b, M.t)
  const clampY = (v: number) => Math.min(H - M.b, Math.max(M.t, y(v)))
  const pts = points.map((p) => [x(p.t), clampY(p.v)] as const)
  const path = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join('')
  const last = pts[pts.length - 1]

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect()
    const mx = e.clientX - box.left
    let best = 0
    pts.forEach(([px], i) => {
      if (Math.abs(px - mx) < Math.abs(pts[best][0] - mx)) best = i
    })
    setHover(best)
  }

  const hovered = hover !== null ? pts[hover] : null
  const tipLeft = hovered ? (hovered[0] + 220 > W ? hovered[0] - 14 : hovered[0] + 14) : 0

  return (
    <div ref={ref} className="relative w-full">
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label={label} className="block overflow-visible">
          {bands.map((b) => (
            <rect
              key={`${b.from}-${b.to}`}
              x={M.l}
              width={W - M.l - M.r}
              y={clampY(b.to)}
              height={Math.max(0, clampY(b.from) - clampY(b.to))}
              fill={b.color}
            />
          ))}
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} stroke={C.grid} />
              <text x={M.l - 8} y={y(v) + 4} textAnchor="end" fontSize={11} fill={C.label}>
                {yFormat(v)}
              </text>
            </g>
          ))}
          {dateTicks(xDomain[0], xDomain[1], Math.floor((W - M.l - M.r) / 64)).map(({ t, label: l }) => (
            <text key={t} x={x(t)} y={H - 8} textAnchor="middle" fontSize={11} fill={C.label}>
              {l}
            </text>
          ))}
          {reference && (
            <g>
              <line
                x1={M.l}
                x2={W - M.r}
                y1={y(reference.v)}
                y2={y(reference.v)}
                stroke={C.strong}
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
              {reference.label && (
                <text x={W - M.r} y={y(reference.v) - 6} textAnchor="end" fontSize={11} fill={C.strong}>
                  {reference.label}
                </text>
              )}
            </g>
          )}

          {pts.length > 1 && (
            <path d={path} fill="none" stroke={C.accent} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          )}
          {last && (
            <g>
              <circle cx={last[0]} cy={last[1]} r={5} fill={C.accent} stroke={C.surface} strokeWidth={2} />
              <text x={last[0] + 9} y={last[1] + 4} fontSize={11} fontWeight={600} fill={C.text}>
                {yFormat(points[points.length - 1].v)}
              </text>
            </g>
          )}

          {hovered && (
            <g pointerEvents="none">
              <line x1={hovered[0]} x2={hovered[0]} y1={M.t} y2={H - M.b} stroke={C.muted} />
              <circle cx={hovered[0]} cy={hovered[1]} r={5} fill={C.accentHover} stroke={C.surface} strokeWidth={2} />
            </g>
          )}
          <rect
            x={M.l}
            y={M.t}
            width={W - M.l - M.r}
            height={H - M.t - M.b}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute z-10 whitespace-nowrap rounded-lg border border-site-600 bg-site-800 px-2.5 py-2 text-xs text-site-200 shadow-lg shadow-black/40"
          style={{
            left: tipLeft,
            top: Math.max(0, hovered[1] - 12),
            transform: `translate(${hovered[0] + 220 > W ? '-100%' : '0'}, -100%)`,
          }}
        >
          {points[hover].tip}
        </div>
      )}
    </div>
  )
}
