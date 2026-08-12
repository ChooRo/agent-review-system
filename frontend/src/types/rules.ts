export type RuleStatus = 'draft' | 'pending_confirmation' | 'published' | 'expired'
export type RuleRiskLevel = 'mandatory' | 'general'
export type RuleSourceType = 'manual' | 'ai_candidate' | 'legal_extraction'

export interface GovernanceRule {
  id: string
  title: string
  description: string
  decision_criteria: string
  risk_level: RuleRiskLevel
  module: string
  department: string
  tags: string[]
  status: RuleStatus
  source_type: RuleSourceType
  legal_document_key?: string | null
  legal_unit_ids: string[]
  version: number
  created_at?: string
  created_by?: number
  updated_at?: string
  updated_by?: number
  published_at?: string | null
  published_by?: number | null
  expired_at?: string
  expired_by?: number | null
  expiry_reason?: string | null
}

export interface RuleListFilters {
  keyword?: string
  status?: RuleStatus | ''
  module?: string
  department?: string
}

export interface RulePayload {
  title: string
  description: string
  decision_criteria: string
  risk_level: RuleRiskLevel
  module: 'procurement'
  department: string
  tags: string[]
  source_type: RuleSourceType
  legal_document_key?: string | null
  legal_unit_ids: string[]
}

export interface RuleUpdatePayload {
  title?: string
  description?: string
  decision_criteria?: string
  risk_level?: RuleRiskLevel
  tags?: string[]
  legal_document_key?: string | null
  legal_unit_ids?: string[]
  version: number
}

export interface RuleVersion extends GovernanceRule {
  snapshot_id: string
  recorded_at: string
  event: string
}
