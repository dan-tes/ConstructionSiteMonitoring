import { randomSaltHex } from '../lib/hash'
import { type AuthApi, AuthApiError, type Session, type User } from './types'

interface StoredUser {
  id: string
  username: string
  salt: string
  passwordHash: string
  createdAt: string
}

const USERS_KEY = 'csm.mock.users'
const SESSIONS_KEY = 'csm.mock.sessions'
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000 // 7 days

const delay = (ms = 350) => new Promise((resolve) => setTimeout(resolve, ms))

function readUsers(): StoredUser[] {
  try {
    return JSON.parse(localStorage.getItem(USERS_KEY) ?? '[]')
  } catch {
    return []
  }
}

function writeUsers(users: StoredUser[]) {
  localStorage.setItem(USERS_KEY, JSON.stringify(users))
}

function readSessions(): Record<string, { userId: string; expiresAt: string }> {
  try {
    return JSON.parse(localStorage.getItem(SESSIONS_KEY) ?? '{}')
  } catch {
    return {}
  }
}

function writeSessions(sessions: Record<string, { userId: string; expiresAt: string }>) {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions))
}

function toPublicUser(u: StoredUser): User {
  return { id: u.id, username: u.username, createdAt: u.createdAt }
}

function createSessionFor(user: StoredUser): Session {
  const token = crypto.randomUUID()
  const expiresAt = new Date(Date.now() + SESSION_TTL_MS).toISOString()
  const sessions = readSessions()
  sessions[token] = { userId: user.id, expiresAt }
  writeSessions(sessions)
  return { token, user: toPublicUser(user), expiresAt }
}

const normalize = (username: string) => username.trim().toLowerCase()

export const mockAuthApi: AuthApi = {
  async getSalt(username) {
    await delay(150)
    const users = readUsers()
    const existing = users.find((u) => u.username === normalize(username))
    // Unknown users still get a stable-looking salt so the API doesn't leak existence.
    return existing?.salt ?? randomSaltHex()
  },

  async register(username, passwordHash, salt) {
    await delay()
    const normalized = normalize(username)
    if (normalized.length < 3) {
      throw new AuthApiError('Ник должен быть не короче 3 символов', 'VALIDATION')
    }
    const users = readUsers()
    if (users.some((u) => u.username === normalized)) {
      throw new AuthApiError('Такой ник уже занят', 'USERNAME_TAKEN')
    }
    const user: StoredUser = {
      id: crypto.randomUUID(),
      username: normalized,
      salt,
      passwordHash,
      createdAt: new Date().toISOString(),
    }
    writeUsers([...users, user])
    return createSessionFor(user)
  },

  async login(username, passwordHash) {
    await delay()
    const normalized = normalize(username)
    const users = readUsers()
    const user = users.find((u) => u.username === normalized)
    if (!user || user.passwordHash !== passwordHash) {
      throw new AuthApiError('Неверный ник или пароль', 'INVALID_CREDENTIALS')
    }
    return createSessionFor(user)
  },

  async logout(token) {
    await delay(100)
    const sessions = readSessions()
    delete sessions[token]
    writeSessions(sessions)
  },

  async getSession(token) {
    await delay(100)
    const sessions = readSessions()
    const entry = sessions[token]
    if (!entry) return null
    if (new Date(entry.expiresAt).getTime() < Date.now()) {
      delete sessions[token]
      writeSessions(sessions)
      return null
    }
    const user = readUsers().find((u) => u.id === entry.userId)
    if (!user) return null
    return { token, user: toPublicUser(user), expiresAt: entry.expiresAt }
  },
}
