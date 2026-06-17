/**
 * Method-aware API proxy helper.
 *
 * This module is the single source of truth for talking to the upstream
 * backend (Django on :8000 or rust_twin on :8080) from Next.js server-side
 * code — route handlers, server components, and server actions. The client
 * continues to use the `/api/...` rewrites defined in `next.config.js`;
 * this helper exists for cases where the proxy rewrites are not sufficient
 * (e.g. when we need to call the upstream from inside a server action
 * that wants to stream the body back as-is).
 *
 * The proxy is intentionally transparent: bytes in = bytes out, no
 * transformation. Cookies and auth headers are forwarded in both directions.
 */

export type ApiMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface ApiRequestOptions {
  method: ApiMethod
  /** Path on the upstream backend, e.g. `/api/v1/projects`. Must start with `/`. */
  path: string
  /** Request body. Will be JSON-serialized unless `body` is a string/Buffer/stream. */
  body?: unknown
  /** Extra headers to add to the request (Content-Type for JSON is added automatically). */
  headers?: Record<string, string>
  /** Raw `Cookie` header from the incoming request. Forwarded verbatim. */
  cookies?: string
  /** Query string parameters. Merged with any existing query string in `path`. */
  query?: Record<string, string | number | boolean>
  /** Optional AbortSignal to cancel the upstream request. */
  signal?: AbortSignal
  /** Optional pre-resolved upstream base. Defaults to the same logic as `next.config.js`. */
  baseUrl?: string
}

const DEFAULT_BASE = 'http://localhost:8000'

function resolveDefaultBase(): string {
  const raw =
    process.env.NEXT_PUBLIC_API_BASE ||
    process.env.INTERNAL_API_BASE ||
    (process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || '')
      .replace(/\/api\/v\d+\/?$/, '') ||
    DEFAULT_BASE
  return raw.replace(/\/+$/, '')
}

function buildUrl(path: string, query: ApiRequestOptions['query'], baseUrl: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const base = baseUrl || resolveDefaultBase()
  const fullUrl = `${base}${normalizedPath}`

  if (!query || Object.keys(query).length === 0) {
    return fullUrl
  }

  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  if (!qs) return fullUrl
  return fullUrl.includes('?') ? `${fullUrl}&${qs}` : `${fullUrl}?${qs}`
}

function isFormData(body: unknown): body is FormData {
  return typeof FormData !== 'undefined' && body instanceof FormData
}

function isBlob(body: unknown): body is Blob {
  return typeof Blob !== 'undefined' && body instanceof Blob
}

function isArrayBuffer(body: unknown): body is ArrayBuffer {
  return body instanceof ArrayBuffer
}

function isReadableStream(body: unknown): body is ReadableStream {
  return typeof ReadableStream !== 'undefined' && body instanceof ReadableStream
}

function isBuffer(body: unknown): body is Buffer {
  return typeof Buffer !== 'undefined' && Buffer.isBuffer(body)
}

function serializeBody(body: unknown): { body: BodyInit | null; contentType?: string } {
  if (body === undefined || body === null) {
    return { body: null }
  }
  if (typeof body === 'string') {
    return { body, contentType: 'text/plain;charset=UTF-8' }
  }
  if (isFormData(body)) {
    // Let fetch set the boundary.
    return { body }
  }
  if (isBlob(body) || isArrayBuffer(body) || isReadableStream(body) || isBuffer(body)) {
    return { body: body as BodyInit }
  }
  return { body: JSON.stringify(body), contentType: 'application/json' }
}

const FORWARDED_REQUEST_HEADERS = new Set([
  'accept',
  'accept-language',
  'accept-encoding',
  'content-language',
  'content-type',
  'user-agent',
  'x-requested-with',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
  'x-real-ip',
  'authorization',
  'cookie',
])

const FORWARDED_RESPONSE_HEADERS = new Set([
  'content-type',
  'content-length',
  'content-encoding',
  'cache-control',
  'etag',
  'last-modified',
  'set-cookie',
  'location',
  'x-request-id',
  'x-trace-id',
])

/**
 * Proxy a request to the upstream backend and return the raw `Response`.
 *
 * The returned `Response` is intentionally a fresh `Response` that re-exposes
 * the upstream body, status, and a curated set of response headers (including
 * `Set-Cookie`). Callers can return it directly from a route handler.
 */
export async function apiProxy(opts: ApiRequestOptions): Promise<Response> {
  const { method, path, body, headers, cookies, query, signal, baseUrl } = opts

  const url = buildUrl(path, query, baseUrl || resolveDefaultBase())

  const upstreamHeaders: Record<string, string> = {}
  if (headers) {
    for (const [k, v] of Object.entries(headers)) {
      if (typeof v === 'string' && v.length > 0) {
        upstreamHeaders[k] = v
      }
    }
  }
  if (cookies && cookies.length > 0) {
    upstreamHeaders['cookie'] = cookies
  }
  if (method !== 'GET' && body !== undefined && body !== null) {
    const serialized = serializeBody(body)
    if (serialized.body !== null && !upstreamHeaders['content-type'] && serialized.contentType) {
      upstreamHeaders['content-type'] = serialized.contentType
    }
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers: upstreamHeaders,
      body: method === 'GET' ? undefined : serializeBody(body).body,
      signal,
      // Don't auto-follow redirects; let the caller decide.
      redirect: 'manual',
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'upstream request failed'
    return new Response(
      JSON.stringify({ error: 'upstream_unreachable', message }),
      {
        status: 502,
        headers: { 'content-type': 'application/json' },
      },
    )
  }

  const responseHeaders = new Headers()
  response.headers.forEach((value, key) => {
    if (FORWARDED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      responseHeaders.append(key, value)
    }
  })

  // Buffer the upstream body so the consumer can safely read it from the
  // returned Response. This is acceptable for the API surface this helper
  // targets (JSON + small binary); large streaming responses should use a
  // dedicated passthrough.
  const upstreamBody = await response.arrayBuffer()
  return new Response(upstreamBody, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  })
}

/**
 * Helper that picks the headers from a Next.js request that should be
 * forwarded to the upstream backend. Exported for use by route handlers
 * that want to call `apiProxy` from inside Next.js without manually
 * filtering headers.
 */
export function pickForwardedRequestHeaders(source: Headers): Record<string, string> {
  const out: Record<string, string> = {}
  source.forEach((value, key) => {
    if (FORWARDED_REQUEST_HEADERS.has(key.toLowerCase())) {
      out[key] = value
    }
  })
  return out
}
