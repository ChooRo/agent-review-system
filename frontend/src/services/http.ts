const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

export function apiErrorMessage(error: unknown): string {
  const status = (error as { status?: number })?.status
  if (status === 403) return '无权访问或执行该操作。'
  if (status === 404) return '资源不存在或您无权获知其存在。'
  if (status === 409) return '数据已被他人修改，请刷新后重新确认。'
  if (status === 422) return '提交内容不符合要求，请检查标记字段。'
  if (status === 500) return `服务异常，请稍后重试${(error as { request_id?: string })?.request_id ? `（请求标识：${(error as { request_id: string }).request_id}）` : ''}。`
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('access_token')
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '服务暂时不可用' }))
    const message = Array.isArray(error.detail) ? error.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join('；') : error.detail
    const requestId = response.headers.get('X-Request-ID')
    const typed = new Error(message ?? '请求失败') as Error & { status?: number; request_id?: string }
    typed.status = response.status; typed.request_id = requestId ?? undefined
    if (response.status === 401) { localStorage.removeItem('access_token'); if (location.pathname !== '/login') location.assign('/login') }
    throw typed
  }
  return response.json() as Promise<T>
}
