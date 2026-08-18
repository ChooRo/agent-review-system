import { idempotencyKey, request } from './index'
import type { CreateProjectPayload, Finding, Project, ReviewTask } from '../types/procurement-review'

export interface AssignableUser { id: number; username: string; display_name: string; department: string }

export function listAssignableUsers(): Promise<AssignableUser[]> {
  return request('/auth/users')
}

const taskPath = (projectId: string, taskId = '') =>
  `/projects/${projectId}/procurement-review-tasks${taskId ? `/${taskId}` : ''}`

export function listProjects(): Promise<Project[]> {
  return request('/projects')
}

export function createProject(payload: CreateProjectPayload): Promise<Project> {
  return request('/projects', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
    body: JSON.stringify(payload),
  })
}

export function getProject(id: string): Promise<Project> {
  return request(`/projects/${id}`)
}

export function listTasks(projectId: string): Promise<ReviewTask[]> {
  return request(taskPath(projectId))
}

export function getTask(projectId: string, taskId: string): Promise<ReviewTask> {
  return request(taskPath(projectId, taskId))
}

export function confirmLegalApplicability(
  projectId: string, taskId: string, documentKey: string,
  decision: 'confirmed' | 'rejected' | 'needs_more_facts', version: number, comment = '',
): Promise<ReviewTask> {
  return request(`${taskPath(projectId, taskId)}/legal-applicability/${encodeURIComponent(documentKey)}/confirmation`, {
    method: 'PUT', body: JSON.stringify({ decision, comment, version }),
  })
}

export function retryTask(projectId: string, taskId: string): Promise<ReviewTask> {
  return request(`${taskPath(projectId, taskId)}/start`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}

export async function uploadRectification(projectId: string, taskId: string, file: File): Promise<ReviewTask> {
  const data = new FormData()
  data.append('file', file)
  const task = await request<ReviewTask>(`${taskPath(projectId, taskId)}/rectification-document`, { method: 'POST', body: data })
  return request(`${taskPath(projectId, taskId)}/start`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() } })
}

export function lockFinal(projectId: string, taskId: string): Promise<ReviewTask> {
  return request(`${taskPath(projectId, taskId)}/lock-final`, { method: 'POST' })
}

export function createTask(projectId: string, title: string, file: File): Promise<ReviewTask> {
  return request<ReviewTask>(taskPath(projectId), {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
    body: JSON.stringify({ title }),
  }).then(async (task) => {
    const data = new FormData()
    data.append('file', file)
    await request(`${taskPath(projectId, task.id)}/document`, { method: 'POST', body: data })
    await request(`${taskPath(projectId, task.id)}/start`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey() },
    })
    return task
  })
}

export function getEvents(projectId: string, taskId: string, after?: string): Promise<{ id: string }[]> {
  return request(`${taskPath(projectId, taskId)}/events${after ? `?after=${encodeURIComponent(after)}` : ''}`)
}

export interface DebugTrace {
  task_id: string
  run_id: string | null
  status: string
  state?: Record<string, unknown>
  llm_calls: { id: string; step: string; skill?: string; request?: Record<string, unknown>; response?: string; error?: string }[]
  tool_calls: { timestamp?: string; tool?: string; agent?: string; status?: string; duration_ms?: number; argument_names?: string[]; message?: string }[]
  events: { timestamp?: string; level?: string; step?: string; event?: string; message?: string; duration_seconds?: number }[]
  stage_results: DebugStage[]
}

export interface DebugBatch {
  file?: string; batch_no?: number; purpose?: string; coverage_strategy?: string
  accepted_count?: number; evidence_pending_count?: number; rejected_count?: number; accepted?: any[]; rejected?: unknown[]
  status?: string; primary_block_count?: number; candidate_estimate?: number; table_row_count?: number
  request_tokens?: number; output_characters?: number
  heading?: string; page_range?: number[]; block_count?: number; character_count?: number; block_ids?: string[]
  unit_ids?: string[]; primary_block_ids?: string[]; blocks?: { block_id: string; role: string; type: string; page?: number; text?: string }[]
  token_estimate?: number; oversized?: boolean
}

export interface DebugStage {
  key: string; title: string; kind: 'ai' | 'deterministic'; data: Record<string, any>
  validation?: { status?: string; issues?: Record<string, any>[]; primary_block_count?: number; batch_count?: number }
  llm_calls?: { id: string; step?: string; skill?: string; request?: unknown; response?: unknown; error?: string }[]
  tools?: { key: string; title: string; triggered: boolean; data: any }[]
  batches?: DebugBatch[]
}

export function getDebugTraces(projectId: string, taskId: string): Promise<DebugTrace> {
  return request(`${taskPath(projectId, taskId)}/debug-traces`)
}

export async function getFindings(projectId: string, taskId: string): Promise<Finding[]> {
  const findings = await request<Finding[]>(`${taskPath(projectId, taskId)}/findings`)
  // Older task records were created before legal_refs was introduced.
  return findings.map((finding) => ({
    ...finding,
    risk_level: (['high', 'medium', 'low', 'pending', 'unknown'].includes(finding.risk_level) ? finding.risk_level : 'pending') as Finding['risk_level'],
    legal_refs: finding.legal_refs ?? [],
    rule_refs: finding.rule_refs ?? [],
    collaborative_comments: finding.collaborative_comments ?? [],
  }))
}

export function saveDisposition(
  projectId: string, taskId: string, findingId: string,
  action: 'accept' | 'partial_accept' | 'reject', comment: string, version: number,
): Promise<Finding> {
  return request(`${taskPath(projectId, taskId)}/findings/${findingId}/operator-disposition`, {
    method: 'PUT',
    body: JSON.stringify({ action, comment, version }),
  })
}

export function savePrimaryDecision(
  projectId: string, taskId: string, findingId: string,
  decision: 'receive' | 'adjust' | 'reject', comment: string, riskLevel: string | undefined, version: number,
): Promise<Finding> {
  return request(`${taskPath(projectId, taskId)}/findings/${findingId}/primary-decision`, {
    method: 'PUT',
    body: JSON.stringify({ decision, comment, risk_level: riskLevel, version }),
  })
}

export function createCollaborativeComment(
  projectId: string, taskId: string, findingId: string, comment: string, version: number,
): Promise<Finding> {
  return request(`${taskPath(projectId, taskId)}/findings/${findingId}/collaborative-comments`, {
    method: 'POST',
    body: JSON.stringify({ comment, version }),
  })
}

export function updateCollaborativeComment(
  projectId: string, taskId: string, findingId: string,
  commentId: string, comment: string, version: number,
): Promise<Finding> {
  return request(`${taskPath(projectId, taskId)}/findings/${findingId}/collaborative-comments/${commentId}`, {
    method: 'PUT',
    body: JSON.stringify({ comment, version }),
  })
}

export function submitTask(projectId: string, taskId: string): Promise<void> {
  return request(`${taskPath(projectId, taskId)}/operator-submit`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}

export function confirmTask(projectId: string, taskId: string): Promise<void> {
  return request(`${taskPath(projectId, taskId)}/primary-confirm`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}
