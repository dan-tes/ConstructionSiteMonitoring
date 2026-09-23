import { useEffect, useRef, useState } from 'react'

/** Chart colours — the same values as the site/safety tokens in index.css,
 * plus status colours reserved for delay thresholds. Kept as literals because
 * SVG attributes can't take Tailwind classes. */
export const C = {
  grid: '#1e2227', // site-800
  axis: '#2b3038', // site-700
  muted: '#5a6270', // site-500
  label: '#8a919c', // site-400
  strong: '#b8bec6', // site-300
  text: '#eef0f2', // site-100
  surface: '#14171b', // site-900
  plan: '#4a5361',
  accent: '#f89b1c', // safety-400
  accentHover: '#ffb238', // safety-300
  warning: '#e0b341',
  critical: '#e5534b',
  good: '#3fb27f',
}

/** Thresholds from services/delay's forecast_delay (warning / critical). */
export const WARNING_DAYS = 7
export const CRITICAL_DAYS = 30

export const DAY_MS = 86_400_000

/** `YYYY-MM-DD` → UTC milliseconds (the API sends plain dates). */
export const parseDate = (iso: string) => Date.parse(`${iso}T00:00:00Z`)

const shortFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const longFmt = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})
const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'short', timeZone: 'UTC' })

export const fmtShort = (t: number) => shortFmt.format(t).replace('.', '')
export const fmtLong = (t: number) => longFmt.format(t)
const fmtMonth = (t: number) => monthFmt.format(t).replace('.', '')

export const signed = (n: number) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : '0')

export const linear =
  (d0: number, d1: number, r0: number, r1: number) =>
  (v: number): number =>
    d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0)

/** Evenly spaced ticks on a "nice" step covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, maxCount = 6): number[] {
  const raw = (hi - lo) / Math.max(1, maxCount - 1)
  const mag = 10 ** Math.floor(Math.log10(raw || 1))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6))
  return out
}

/** Calendar ticks across [lo, hi] (UTC ms): weekly for short spans, monthly after that. */
export function dateTicks(lo: number, hi: number, maxCount: number): { t: number; label: string }[] {
  const spanDays = (hi - lo) / DAY_MS
  for (const days of [7, 14]) {
    if (spanDays / days <= maxCount) {
      const out = []
      for (let t = Math.ceil(lo / DAY_MS) * DAY_MS; t <= hi; t += days * DAY_MS) {
        out.push({ t, label: fmtShort(t) })
      }
      return out
    }
  }
  const months = [1, 2, 3, 6].find((m) => spanDays / (30.4 * m) <= maxCount) ?? 12
  const out = []
  // First of the month after `lo`, as a running month index (y * 12 + m).
  const start = new Date(lo)
  for (let i = start.getUTCFullYear() * 12 + start.getUTCMonth() + 1; ; i += 1) {
    const t = Date.UTC(Math.floor(i / 12), i % 12, 1)
    if (t > hi) break
    if (i % months === 0) out.push({ t, label: fmtMonth(t) })
  }
  return out
}

/** Tracks an element's rendered width so SVG charts can draw at 1:1 scale
 * (text stays readable on a phone instead of shrinking with a viewBox). */
export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}
