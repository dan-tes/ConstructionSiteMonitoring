import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function Field({
  label,
  htmlFor,
  error,
  children,
}: {
  label: string
  htmlFor: string
  error?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-xs font-semibold tracking-wide text-site-300 uppercase">
        {label}
      </label>
      {children}
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  )
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={
        'w-full rounded-lg border border-site-600 bg-site-800/80 px-3.5 py-2.5 text-site-100 outline-none placeholder:text-site-500 transition focus:border-safety-400 focus:ring-2 focus:ring-safety-400/30 ' +
        (props.className ?? '')
      }
    />
  )
}

export function PrimaryButton({
  children,
  isLoading,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { isLoading?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || isLoading}
      className={
        'relative inline-flex items-center justify-center gap-2 rounded-lg bg-safety-500 px-4 py-2.5 font-semibold text-site-950 shadow-[0_4px_0_0_var(--color-safety-600)] transition active:translate-y-0.5 active:shadow-[0_2px_0_0_var(--color-safety-600)] hover:bg-safety-400 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none disabled:active:translate-y-0 ' +
        (props.className ?? '')
      }
    >
      {isLoading ? 'Секунду…' : children}
    </button>
  )
}

export function GhostButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={
        'inline-flex items-center justify-center gap-2 rounded-lg border border-site-600 px-4 py-2.5 font-medium text-site-200 transition hover:border-safety-400 hover:text-safety-300 ' +
        (props.className ?? '')
      }
    />
  )
}
