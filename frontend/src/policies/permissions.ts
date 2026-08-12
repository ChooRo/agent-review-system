import type { User } from '../types/auth'
import type { ReviewTask, TaskRole } from '../types/procurement-review'

/** UI visibility only. Every API action remains subject to backend authorization. */
export function hasRole(user: User | null | undefined, role: User['roles'][number]['code']) {
  return user?.roles.some((item) => item.code === role) ?? false
}

export function canViewKnowledge(user: User | null | undefined) { return Boolean(user) }
export function canMaintainKnowledge(user: User | null | undefined) { return hasRole(user, 'admin') }
export function canMaintainRules(user: User | null | undefined) { return hasRole(user, 'admin') }

function taskScopeAllows(user: User | null | undefined, task: ReviewTask) {
  if (!task.module_scope?.length || !user?.module_scope?.length) return true
  return task.module_scope.some((module) => user.module_scope?.includes(module))
}

export function workbenchMode(user: User | null | undefined, task: ReviewTask | undefined): TaskRole | 'readonly' {
  if (!task || task.status === 'completed' || hasRole(user, 'admin')) return 'readonly'
  if (task.task_role === 'operator' && hasRole(user, 'operator')) return 'operator'
  if (task.task_role === 'primary_supervisor' && hasRole(user, 'supervisor') && taskScopeAllows(user, task)) return 'primary_supervisor'
  if (task.task_role === 'collaborative_supervisor' && hasRole(user, 'supervisor') && taskScopeAllows(user, task)) return 'collaborative_supervisor'
  return 'readonly'
}
