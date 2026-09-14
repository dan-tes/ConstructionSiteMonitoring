import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'loading') {
    return (
      <div className="flex min-h-svh items-center justify-center text-site-400">
        Загрузка…
      </div>
    )
  }

  if (status === 'guest') {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

export function GuestOnlyRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'authenticated') {
    return <Navigate to="/projects" replace />
  }

  return <>{children}</>
}
