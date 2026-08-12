import { request } from './index'
import type { GovernanceRule, RuleListFilters, RulePayload, RuleUpdatePayload, RuleVersion } from '../types/rules'

export function listRules(filters: RuleListFilters = {}): Promise<GovernanceRule[]> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value)
  const suffix = query.size ? `?${query}` : ''
  return request(`/rules${suffix}`)
}

export function getRule(id: string): Promise<GovernanceRule> { return request(`/rules/${encodeURIComponent(id)}`) }
export function createRule(payload: RulePayload): Promise<GovernanceRule> { return request('/rules', { method: 'POST', body: JSON.stringify(payload) }) }
export function updateRule(id: string, payload: RuleUpdatePayload): Promise<GovernanceRule> { return request(`/rules/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }) }
export function confirmRule(id: string, version: number): Promise<GovernanceRule> { return request(`/rules/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: JSON.stringify({ version }) }) }
export function expireRule(id: string, version: number, reason: string): Promise<GovernanceRule> { return request(`/rules/${encodeURIComponent(id)}/expire`, { method: 'POST', body: JSON.stringify({ version, reason }) }) }
export function reactivateRule(id: string, version: number): Promise<GovernanceRule> { return request(`/rules/${encodeURIComponent(id)}/reactivate`, { method: 'POST', body: JSON.stringify({ version }) }) }
export function listRuleVersions(id: string): Promise<RuleVersion[]> { return request(`/rules/${encodeURIComponent(id)}/versions`) }
