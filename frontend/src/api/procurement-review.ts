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

export function retryTask(projectId: string, taskId: string): Promise<ReviewTask> {
  return request(`${taskPath(projectId, taskId)}/start`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
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

export async function getFindings(projectId: string, taskId: string): Promise<Finding[]> {
  const findings = await request<Finding[]>(`${taskPath(projectId, taskId)}/findings`)
  // Older task records were created before legal_refs was introduced.
  return findings.map((finding) => ({
    ...finding,
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
