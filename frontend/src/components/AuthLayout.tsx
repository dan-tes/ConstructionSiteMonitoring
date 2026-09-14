import { HardHat } from 'lucide-react'
import type { ReactNode } from 'react'

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle: string
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <div className="flex min-h-svh items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-safety-500 text-site-950">
            <HardHat className="h-5.5 w-5.5" strokeWidth={2.25} />
          </span>
          <span className="font-display text-xl font-bold tracking-tight text-site-100">
            СтройМонитор
          </span>
        </div>

        <div className="overflow-hidden rounded-2xl border border-site-700 bg-site-900/80 shadow-2xl shadow-black/40 backdrop-blur">
          <div className="hazard-stripes h-1.5 w-full" />
          <div className="p-7 sm:p-8">
            <h1 className="font-display text-2xl font-bold text-site-100">{title}</h1>
            <p className="mt-1.5 text-sm text-site-400">{subtitle}</p>
            <div className="mt-6">{children}</div>
          </div>
        </div>

        <p className="mt-5 text-center text-sm text-site-400">{footer}</p>
      </div>
    </div>
  )
}
