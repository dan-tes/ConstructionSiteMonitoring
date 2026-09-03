import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from 'react'
import { authApi } from '../api/authApi'
import type { User } from '../api/types'
import { hashPassword } from '../lib/hash'

const TOKEN_KEY = 'csm.session.token'

interface AuthContextValue {
  user: User | null
  status: 'loading' | 'authenticated' | 'guest'
  register: (username: string, password: string) => Promise<void>
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<'loading' | 'authenticated' | 'guest'>('loading')

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      setStatus('guest')
      return
    }
    authApi.getSession(token).then((session) => {
      if (session) {
        setUser(session.user)
        setStatus('authenticated')
      } else {
        localStorage.removeItem(TOKEN_KEY)
        setStatus('guest')
      }
    })
  }, [])

  const register = async (username: string, password: string) => {
    const salt = await authApi.getSalt(username)
    const passwordHash = await hashPassword(password, salt)
    const session = await authApi.register(username, passwordHash, salt)
    localStorage.setItem(TOKEN_KEY, session.token)
    setUser(session.user)
    setStatus('authenticated')
  }

  const login = async (username: string, password: string) => {
    const salt = await authApi.getSalt(username)
    const passwordHash = await hashPassword(password, salt)
    const session = await authApi.login(username, passwordHash)
    localStorage.setItem(TOKEN_KEY, session.token)
    setUser(session.user)
    setStatus('authenticated')
  }

  const logout = async () => {
    const token = localStorage.getItem(TOKEN_KEY)
    localStorage.removeItem(TOKEN_KEY)
    setUser(null)
    setStatus('guest')
    if (token) await authApi.logout(token)
  }

  const value = useMemo(() => ({ user, status, register, login, logout }), [user, status])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
