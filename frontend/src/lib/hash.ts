const toHex = (buffer: ArrayBuffer): string =>
  Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

export const randomSaltHex = (): string => toHex(crypto.getRandomValues(new Uint8Array(16)).buffer)

/**
 * Client-side SHA-256(salt + password), hex-encoded. The server never sees the raw password.
 * This is not a replacement for server-side hashing (bcrypt/argon2) once a real backend
 * exists — the server should still hash whatever it receives before storing it.
 */
export async function hashPassword(password: string, saltHex: string): Promise<string> {
  const data = new TextEncoder().encode(saltHex + password)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return toHex(digest)
}
