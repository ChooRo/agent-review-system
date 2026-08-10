export type ProjectStatus = 'draft' | 'active' | 'completed'
export type ReviewStatus = 'draft' | 'queued' | 'parsing' | 'reviewing' | 'operator_review' | 'primary_review' | 'primary_recheck' | 'completed' | 'failed' | 'cancelled'
export type RiskLevel = 'high' | 'medium' | 'low' | 'unknown'
export type TaskRole = 'operator' | 'primary_supervisor' | 'collaborative_supervisor'

export interface Project {
  id: string
  name: string
  project_code: string
  handling_department: string
  project_owner: string
  status: ProjectStatus
  created_at: string
  updated_at: string
}
export interface Finding {
  id: string; task_id: string; risk_level: RiskLevel; category: string; title: string
  description: string; suggestion: string
  source: { page: number; section_path: string[]; quote: string; block_id: string }
  /** 法规文档解析出的引用，仅作法规依据展示，不等同于执行规则。 */
  legal_refs?: { document_title: string; article_no: string; quote: string }[]
  /** 已发布且命中的执行规则；当前任务可能为空。 */
  rule_refs: { id: string; title: string }[]
  operator_disposition: { action: 'accept' | 'partial_accept' | 'reject' | 'edit'; comment: string } | null
  primary_decision: { decision: 'receive' | 'adjust' | 'reject'; comment: string; risk_level?: RiskLevel } | null
  collaborative_comments: { id: string; department: string; author: string; comment: string; updated_at: string; version: number }[]
  recheck_required: boolean
  version: number
}
export interface ReviewTask {
  id: string; project_id: string; title: string; status: ReviewStatus
  document: { id: string; file_name: string; content_type: string; size: number; sha256: string } | null
  finding_summary: { total: number; high: number; medium: number; low: number; pending: number }
  created_at: string; updated_at: string; progress: number; parse_quality?: 'passed' | 'degraded' | 'unreliable'
  task_role: TaskRole
  module_scope?: string[]
  error?: string
}
export interface CreateProjectPayload { name: string; project_code: string; handling_department: string; project_owner: string }
