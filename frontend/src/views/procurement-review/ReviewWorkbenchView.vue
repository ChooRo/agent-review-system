<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FindingCard from '../../components/business/FindingCard.vue'
import BaseModal from '../../components/base/BaseModal.vue'
import {
  confirmLegalApplicability, confirmTask, createCollaborativeComment, getFindings, getTask,
  saveDisposition, savePrimaryDecision, submitTask, updateCollaborativeComment,
} from '../../api/procurement-review'
import { useAuthStore } from '../../stores/auth'
import { workbenchMode } from '../../policies/permissions'
import type { Finding, ReviewTask, RiskLevel } from '../../types/procurement-review'
import { evidenceText } from '../../utils/evidence-text'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const task = ref<ReviewTask>()
const findings = ref<Finding[]>([])
const filter = ref<'all' | RiskLevel>('all')
const selected = ref('')
const activeClause = ref('')
const notice = ref('')
const busy = ref(false)
const sourceCursor = ref<Record<string, number>>({})
const basisExpanded = ref(false)
const applicabilityExpanded = ref(false)

const isReadonly = computed(() => ['completed', 'final_locked'].includes(task.value?.status ?? ''))
const isApplicabilityGate = computed(() => task.value?.status === 'applicability_review')
const mode = computed(() => workbenchMode(auth.user, task.value))
const visible = computed(() => findings.value.filter((item) => filter.value === 'all' || item.risk_level === filter.value))
const systemWarnings = computed(() => task.value?.system_warnings ?? [])
const coverageMatrix = computed(() => task.value?.coverage_matrix ?? [])
const rechecks = computed(() => findings.value.filter((item) => item.recheck_required && !item.primary_decision))
const selectedFinding = computed(() => findings.value.find((item) => item.id === selected.value) ?? visible.value[0] ?? findings.value[0])
const cardMode = (item: Finding) => task.value?.status === 'primary_recheck' && !item.recheck_required ? 'readonly' : mode.value

const filterLabels: [string, string][] = [
  ['all', '全部'], ['high', '不一致（高）'], ['medium', '不一致（中）'], ['low', '低风险'], ['pending', '待人工定级'], ['unknown', '识别质量不足'],
]
const clauses = computed(() => {
  const finding = selectedFinding.value
  if (!finding) return []
  const sources = finding.sources?.length ? finding.sources : [finding.source]
  return sources.map((source, index) => ({
    key: `${finding.id}-${index}`, findingId: finding.id, index,
    path: source.section_path?.length ? source.section_path.join(' · ') : `第 ${source.page || '—'} 页`,
    page: source.page, blockId: source.block_id, bbox: source.bbox,
    quote: evidenceText(source.quote), tableHtml: safeTableHtml(source.quote),
  })).filter((item) => item.quote)
})

const legalSources = computed(() => {
  const sources = new Map<string, { title: string; articles: Set<string> }>()
  findings.value.flatMap((finding) => finding.legal_refs ?? []).forEach((ref) => {
    const source = sources.get(ref.document_title) ?? { title: ref.document_title, articles: new Set<string>() }
    if (ref.article_no) source.articles.add(ref.article_no)
    sources.set(ref.document_title, source)
  })
  return [...sources.values()].map((source) => ({ ...source, articles: [...source.articles] }))
})
const legalApplicability = computed(() => task.value?.legal_applicability)
const applicableLaws = computed(() => (legalApplicability.value ?? []).filter((item) => item.status === 'applicable'))
const potentialLaws = computed(() => (legalApplicability.value ?? []).filter((item) => item.status === 'potential'))
const insufficientFactsLaws = computed(() => (legalApplicability.value ?? []).filter((item) => item.status === 'insufficient_facts'))
const lawsRequiringConfirmation = computed(() => (legalApplicability.value ?? []).filter((item) => ['applicable', 'potential', 'insufficient_facts'].includes(item.status)))
const unconfirmedLawCount = computed(() => lawsRequiringConfirmation.value.filter((item) => !task.value?.legal_applicability_confirmations?.[item.document_key]).length)
const allRules = computed(() => {
  const rules = new Map<string, { id: string; title: string }>()
  findings.value.flatMap((finding) => finding.rule_refs ?? []).forEach((rule) => rules.set(rule.id, rule))
  return [...rules.values()]
})
const currentRules = computed(() => selectedFinding.value?.rule_refs ?? [])
const currentLegalSources = computed(() => {
  const sources = new Map<string, Finding['legal_refs']>()
  ;(selectedFinding.value?.legal_refs ?? []).forEach((ref) => {
    const refs = sources.get(ref.document_title) ?? []
    if (!refs.some((item) => item.article_no === ref.article_no && item.quote === ref.quote)) refs.push(ref)
    sources.set(ref.document_title, refs)
  })
  return [...sources].map(([title, refs]) => ({ title, refs: refs ?? [] }))
})

function isApplicabilityMetaFinding(finding: Finding) {
  if (finding.review_scope === 'applicability_gate') return true
  return !finding.legal_refs?.length && /法规适用性待确认|适用法律体系待确认/.test(finding.title)
}
function lawTitle(item: { title?: string; document_key: string }) { return item.title || item.document_key }
function reasonText(reason: unknown) {
  if (typeof reason === 'string') return reason
  const item = reason as { field?: string; expected?: unknown; actual?: unknown; outcome?: string }
  const result = item.outcome === 'match' ? '匹配' : item.outcome === 'mismatch' ? '不匹配' : item.outcome === 'insufficient' ? '事实不足' : '待确认'
  return `${factLabel(item.field)}：项目识别为“${factValue(item.actual)}”，法规要求“${factValue(item.expected)}”（${result}）`
}
const factLabels: Record<string, string> = {
  project_type: '项目类型', procurement_method: '采购方式', is_government_procurement: '是否属于政府采购',
  is_engineering_related: '是否与工程建设有关', is_mandatory_tender: '是否属于依法必须招标', region: '项目地域', review_stage: '审查阶段', applicability: '法规适用条件',
}
const factValues: Record<string, string> = {
  yes: '是', no: '否', unknown: '尚未识别', engineering: '工程', goods: '货物', services: '服务',
  open_tender: '公开招标', invited_tender: '邀请招标', inquiry: '询比/询价', procurement_document_review: '采购文件审查',
  not_available: '未配置', 'explicit project predicate': '明确的项目适用条件',
}
function factLabel(value?: string) { return factLabels[value ?? ''] || value || '适用条件' }
function factValue(value: unknown) { return factValues[String(value ?? '')] || String(value ?? '未知') }
function uniqueReasons(item: { reasons: unknown[] }) { return [...new Set(item.reasons.map(reasonText))] }
function sourceFactValue(value: unknown) {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const source = value as { quote?: string; text?: string; page?: number; page_no?: number }
    const excerpt = source.quote || source.text
    if (excerpt) return `${source.page ?? source.page_no ? `第 ${source.page ?? source.page_no} 页：` : ''}${excerpt}`
  }
  return factValue(value)
}
function clauseTable(quote: string) {
  const rows = quote.split(/\r?\n/).map((line) => line.split('|').map((cell) => cell.trim())).filter((cells) => cells.length >= 3)
  if (rows.length < 2) return null
  const width = Math.max(...rows.map((cells) => cells.length))
  return rows.map((cells) => [...cells, ...Array(width - cells.length).fill('')])
}
function safeTableHtml(value: unknown) {
  const document = new DOMParser().parseFromString(String(value || ''), 'text/html')
  document.querySelectorAll('script,style,iframe,object,embed').forEach((node) => node.remove())
  document.querySelectorAll('*').forEach((node) => [...node.attributes].forEach((attribute) => {
    if (!['rowspan', 'colspan'].includes(attribute.name.toLowerCase())) node.removeAttribute(attribute.name)
  }))
  return document.querySelector('table')?.outerHTML || ''
}
function taskFacts(item: { evidence?: { task_facts?: Record<string, unknown[]> } }) {
  return Object.entries(item.evidence?.task_facts ?? {}).flatMap(([field, values]) => (values?.length ? [`${factLabel(field)}：${values.map(sourceFactValue).join('；')}`] : []))
}
function confirmation(documentKey: string) { return task.value?.legal_applicability_confirmations?.[documentKey] }
const confirmationLabel = { confirmed: '人工确认适用', rejected: '人工确认不适用', needs_more_facts: '待补充事实' } as const
async function confirmLaw(documentKey: string, decision: 'confirmed' | 'rejected' | 'needs_more_facts') {
  if (!task.value) return
  busy.value = true
  try {
    task.value = await confirmLegalApplicability(task.value.project_id, task.value.id, documentKey, decision, task.value.version)
    notice.value = '法规适用性人工确认已保存。'
    if (task.value.status === 'reviewing') {
      await router.push({ name: 'procurement-progress', params: { projectId: task.value.project_id, taskId: task.value.id } })
    }
  } catch (error) { notice.value = message(error) } finally { busy.value = false }
}

async function load() {
  const projectId = String(route.params.projectId)
  const taskId = String(route.params.taskId)
  task.value = await getTask(projectId, taskId)
  findings.value = (await getFindings(projectId, taskId)).filter((finding) => !isApplicabilityMetaFinding(finding))
  selected.value ||= findings.value[0]?.id ?? ''
  activeClause.value ||= findings.value[0] ? `${findings.value[0].id}-0` : ''
}
function setFilter(value: string) { filter.value = value as 'all' | RiskLevel }
function message(error: unknown) {
  const status = (error as { status?: number }).status
  return status === 409 ? '数据已被其他人修改，请刷新后重新确认。' : status === 403 ? '无权执行该操作。' : status === 422 ? '填写内容不符合要求。' : '操作失败，请稍后重试。'
}
async function disposition(item: Finding, action: 'accept' | 'partial_accept' | 'reject', comment: string) {
  if (!task.value) return
  busy.value = true
  try { await saveDisposition(task.value.project_id, task.value.id, item.id, action, comment, item.version); notice.value = '经办处置已保存。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}
async function primary(item: Finding, decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined) {
  if (!task.value) return
  busy.value = true
  try { await savePrimaryDecision(task.value.project_id, task.value.id, item.id, decision, comment, risk, item.version); notice.value = '主责复核意见已保存。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}
async function collaborative(item: Finding, comment: string) {
  if (!task.value) return
  const own = item.collaborative_comments.find((entry) => entry.author === auth.user?.display_name)
  try {
    if (own) await updateCollaborativeComment(task.value.project_id, task.value.id, item.id, own.id, comment, own.version)
    else await createCollaborativeComment(task.value.project_id, task.value.id, item.id, comment, item.version)
    notice.value = '协同监督意见已保存。'; await load()
  } catch (error) { notice.value = message(error) }
}
async function submit() {
  if (!task.value) return
  busy.value = true
  try { await submitTask(task.value.project_id, task.value.id); notice.value = '已提交采购部门主责监督复核。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}
async function confirm() {
  if (!task.value) return
  busy.value = true
  try { await confirmTask(task.value.project_id, task.value.id); notice.value = '正式复核结果已确认，任务已转为只读。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}
async function locate(item: Finding) {
  selected.value = item.id
  const count = item.sources?.length || 1
  const index = sourceCursor.value[item.id] ?? 0
  activeClause.value = `${item.id}-${index}`
  sourceCursor.value[item.id] = (index + 1) % count
  await nextTick()
  const target = document.getElementById(`clause-${activeClause.value}`)
  const scroller = target?.closest('.wbody')
  if (target && scroller) scroller.scrollTo({ top: Math.max(0, target.offsetTop - (scroller as HTMLElement).offsetTop - 20), behavior: 'smooth' })
}

const statusLabel: Record<string, string> = {
  draft: '草稿', queued: '排队中', parsing: '解析中', reviewing: 'AI 审查中', operator_review: '待业务经办初审',
  applicability_review: '等待审查前法规确认',
  primary_review: '待采购监督复核', primary_recheck: '待采购监督再次复核', completed: '已完成', failed: '处理失败', cancelled: '已取消',
}
onMounted(load)
</script>

<template>
  <div class="page-head workbench-head">
    <div class="crumb">审查任务 / {{ task?.project_id }} / 采购文件审核</div>
    <div class="page-title-row">
      <h2>{{ task?.title ?? '审查工作台' }}<span class="title-status">{{ statusLabel[task?.status ?? ''] ?? '审查进行中' }}</span></h2>
    </div>
    <p>AI 输出仅作为候选问题，原文、审查依据与人工处置在同一审查记录中留痕。</p>
  </div>

  <div class="page-body workbench-page">
    <div v-if="notice" class="status-banner">{{ notice }}</div>
    <div v-if="!task" class="state-card error-state">资源不存在或无权查看。</div>
    <template v-else>
      <div v-if="isReadonly" class="review-actions"><div class="ra-t">原审查结果已提交，当前为<b>只读查看</b></div></div>

      <div class="wb">
        <div class="wcol">
          <div class="whead"><span>原文阅读</span><span class="ch-meta">采购文件 · 1 / 3 份参与</span></div>
          <div class="wbody">
            <div class="doc-item sel"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>采购文件<span class="role">基准</span></div><div class="dm">{{ task.document?.file_name }} · V1</div></div>
            <div class="doc-item dim" aria-disabled="true"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>响应文件<span class="role">未参与</span></div><div class="dm">当前为采购文件单文件审核</div></div>
            <div class="doc-item dim" aria-disabled="true"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>拟签合同<span class="role">未参与</span></div><div class="dm">当前为采购文件单文件审核</div></div>
            <div v-if="selectedFinding" class="source-scope">当前风险原文 · {{ clauses.length }} 处</div>
            <div v-for="clause in clauses" :id="`clause-${clause.key}`" :key="clause.key" class="clause" :class="{ hot: activeClause === clause.key }">
              <div class="clabel">采购文件 · {{ clause.path }} · 原文 {{ clause.index + 1 }} / {{ clauses.length }}<span v-if="activeClause === clause.key"> · 当前定位</span></div>
              <div class="clause-location"><code>{{ clause.blockId || 'Block ID 未返回' }}</code><span>第 {{ clause.page || '—' }} 页</span><span v-if="clause.bbox?.length">坐标 {{ clause.bbox.join(', ') }}</span></div>
              <div v-if="clause.tableHtml" class="clause-table-html" v-html="clause.tableHtml"></div>
              <table v-else-if="clauseTable(clause.quote)" class="clause-table">
                <thead><tr><th v-for="(cell, index) in clauseTable(clause.quote)![0]" :key="index">{{ cell || `字段 ${index + 1}` }}</th></tr></thead>
                <tbody><tr v-for="(row, rowIndex) in clauseTable(clause.quote)!.slice(1)" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody>
              </table>
              <div v-else class="clause-quote">{{ clause.quote }}</div>
            </div>
            <div v-if="!clauses.length" class="empty">暂无可定位的采购文件原文</div>
          </div>
        </div>

        <div class="wcol">
          <div class="whead"><span>AI 审查结果 · 单文件合规</span><div class="whead-actions"><button v-if="mode === 'operator'" class="btn pri" :disabled="busy || unconfirmedLawCount > 0 || findings.some((finding) => !finding.operator_disposition)" :title="unconfirmedLawCount ? `还有 ${unconfirmedLawCount} 份法规匹配结果待人工确认` : ''" @click="submit">确认审查结果</button><button v-if="mode === 'primary_supervisor'" class="btn pri" :disabled="busy || (task.status === 'primary_recheck' ? rechecks.length > 0 : findings.some((finding) => !finding.primary_decision))" @click="confirm">确认复核结果</button></div></div>
          <div class="wbody">
            <details v-if="systemWarnings.length" class="quality-warning-panel">
              <summary>文档识别与系统质量提示 · {{ systemWarnings.length }} 条</summary>
              <div v-for="(warning, index) in systemWarnings" :key="`${warning.title}-${index}`" class="quality-warning-item">
                <b>{{ warning.title }}</b><span>{{ warning.description }}</span>
              </div>
            </details>
            <div class="review-filter"><label class="select-filter"><span>状态</span><select :value="filter" @change="setFilter(($event.target as HTMLSelectElement).value)"><option v-for="[key, label] in filterLabels" :key="key" :value="key">{{ label }}</option></select></label></div>
            <FindingCard v-for="item in visible" :key="item.id" :finding="item" :mode="cardMode(item)" :selected="selectedFinding?.id === item.id" @select="selected = item.id" @locate="locate(item)" @disposition="(action, comment) => disposition(item, action, comment)" @decision="(decision, comment, risk) => primary(item, decision, comment, risk)" @opinion="(comment) => collaborative(item, comment)" />
            <div v-if="!visible.length" class="empty">当前筛选条件下没有结果</div>
          </div>
        </div>

        <div class="wcol">
          <div class="whead"><span>证据链 / 审查依据</span></div>
          <div class="wbody">
            <div class="sec-label evidence-label">审查前关口</div>
            <button class="basis-summary basis-summary-button applicability-summary" type="button" :aria-expanded="applicabilityExpanded" @click="applicabilityExpanded = !applicabilityExpanded">
              <b>审查对照法规</b>
              <span v-if="legalApplicability">已列入对照 {{ applicableLaws.length }} 份 · 待确认 {{ potentialLaws.length }} 份 · 待补充事实 {{ insufficientFactsLaws.length }} 份</span>
              <span v-else>任务尚未返回对照法规</span><i aria-hidden="true">{{ applicabilityExpanded ? '⌃' : '⌄' }}</i>
            </button>
            <div v-if="applicabilityExpanded" class="applicability-detail">
              <template v-if="legalApplicability">
                <div class="basis-group-title">已列入审查对照 <span>{{ applicableLaws.length }} 份</span></div>
                <template v-if="applicableLaws.length">
                  <div v-for="item in applicableLaws" :key="item.document_key" class="basis-source-row applicable-law">
                    <div><b>{{ lawTitle(item) }}</b><small v-if="confirmation(item.document_key)" class="human-confirmed">{{ confirmationLabel[confirmation(item.document_key)!.decision] }} · {{ confirmation(item.document_key)!.by_name || '经办人' }}</small></div>
                  </div>
                </template>
                <div v-else class="basis-empty">当前没有已列入审查对照的法规。</div>

                <div v-if="potentialLaws.length" class="basis-group-title">可能适用 / 待确认 <span>{{ potentialLaws.length }} 份</span></div>
                <div v-for="item in potentialLaws" :key="item.document_key" class="applicability-notice potential-law"><b>{{ lawTitle(item) }} · 系统无法自动确定</b><small v-if="confirmation(item.document_key)">{{ confirmationLabel[confirmation(item.document_key)!.decision] }} · {{ confirmation(item.document_key)!.by_name || '经办人' }}</small><details><summary>查看匹配理由</summary><p v-for="(reason, index) in item.reasons" :key="index">{{ reasonText(reason) }}</p></details></div>

                <div v-if="insufficientFactsLaws.length" class="basis-group-title">事实不足 <span>{{ insufficientFactsLaws.length }} 份</span></div>
                <div v-for="item in insufficientFactsLaws" :key="item.document_key" class="applicability-notice insufficient-law"><b>{{ lawTitle(item) }} · 系统判断项目事实不足</b><small v-if="confirmation(item.document_key)">{{ confirmationLabel[confirmation(item.document_key)!.decision] }} · {{ confirmation(item.document_key)!.by_name || '经办人' }}</small><details><summary>查看缺失事实</summary><p>{{ item.missing_facts.length ? item.missing_facts.join('；') : '后端未返回缺失事实。' }}</p></details></div>

              </template>
              <div v-else class="basis-empty">后端尚未返回本任务的对照法规数据。</div>
            </div>

            <div class="sec-label evidence-label">七类业务强制覆盖</div>
            <div v-if="coverageMatrix.length" class="coverage-matrix">
              <div v-for="item in coverageMatrix" :key="item.topic" class="coverage-row">
                <b>{{ item.topic }}</b><span :class="item.coverage_status">{{ item.coverage_status === 'reviewed' ? '已核验' : '证据不足' }}</span><small>事实 {{ item.fact_count ?? 0 }} · 法规条款 {{ item.legal_unit_count ?? 0 }}</small>
              </div>
            </div>
            <div v-else class="basis-empty">尚未形成七类业务覆盖矩阵。</div>

            <div class="sec-label evidence-label">三层审查依据</div>
            <button class="basis-summary basis-summary-button" type="button" :aria-expanded="basisExpanded" @click="basisExpanded = !basisExpanded">
              <b>系统关联 {{ legalSources.length }} 份法规文档、{{ allRules.length }} 条已发布执行规则</b>
              <span>依据本任务真实审查结果汇总；点击{{ basisExpanded ? '收起' : '展开' }}明细</span><i aria-hidden="true">{{ basisExpanded ? '⌃' : '⌄' }}</i>
            </button>
            <div v-if="basisExpanded" class="basis-task-links">
              <div class="basis-group-title">任务关联法规文档 <span>{{ legalSources.length }} 份</span></div>
              <template v-if="legalSources.length"><div v-for="source in legalSources" :key="source.title" class="basis-source-row"><span>{{ source.title }}</span><small>{{ source.articles.length ? source.articles.join('、') : '涉及条款未标注' }}</small></div></template>
              <div v-else class="basis-empty">本任务未关联法规文档</div>
              <div class="basis-group-title">任务关联已发布执行规则 <span>{{ allRules.length }} 条</span></div>
              <template v-if="allRules.length"><div v-for="rule in allRules" :key="rule.id" class="rule current-rule"><span class="rid">{{ rule.id }}</span><div class="rt">{{ rule.title }}</div></div></template>
              <div v-else class="basis-empty">本任务未使用已发布执行规则</div>
            </div>

            <div class="basis-group-title current-basis-title">当前结论关联 <span>{{ selectedFinding ? selectedFinding.title : '未选中审查项' }}</span></div>
            <div class="basis-group-title">法规依据 <span>{{ currentLegalSources.length }} 份</span></div>
            <template v-if="currentLegalSources.length">
              <div v-for="source in currentLegalSources" :key="source.title" class="basis-source-row legal-source-detail"><b>{{ source.title }}</b><div v-for="(ref, index) in source.refs" :key="`${ref.article_no}-${index}`" class="legal-provision"><strong>{{ ref.article_no || '相关条文' }}</strong><p>{{ ref.quote || '未返回条文原文。' }}</p><small v-if="ref.page">法规原文第 {{ ref.page }} 页</small></div></div>
            </template>
            <div v-else class="basis-empty">当前结论未关联法规文档依据</div>

            <div class="basis-group-title">已发布执行规则 <span>{{ currentRules.length }} 条</span></div>
            <template v-if="currentRules.length">
              <div v-for="rule in currentRules" :key="rule.id" class="rule current-rule"><span class="rid">{{ rule.id }}</span><div class="rt">{{ rule.title }}</div></div>
            </template>
            <div v-else class="basis-empty">当前结论未命中已发布执行规则</div>

          </div>
        </div>
      </div>

      <BaseModal v-if="isApplicabilityGate" title="审查前法规适用性确认" :closable="false" wide>
        <div class="gate-modal-intro">
          <div><span class="gate-kicker">专业审查尚未开始</span><h3>确认本项目适用法规</h3><p>请根据项目实际情况逐份判断。全部完成且不存在“需补充事实”后，系统会自动继续专业审查。</p></div>
          <b>{{ unconfirmedLawCount }}<small>份待确认</small></b>
        </div>
        <div class="gate-law-list">
          <article v-for="item in lawsRequiringConfirmation" :key="item.document_key" class="gate-law-card">
            <header><div><h4>{{ lawTitle(item) }}</h4><span class="system-judgement">系统判断：{{ item.status === 'applicable' ? '适用' : item.status === 'potential' ? '可能适用' : '项目信息不足' }}</span></div><span v-if="confirmation(item.document_key)" class="human-decision">{{ confirmationLabel[confirmation(item.document_key)!.decision] }}</span></header>
            <div class="match-facts"><div v-for="reason in uniqueReasons(item)" :key="reason">{{ reason }}</div><div v-for="fact in taskFacts(item)" :key="fact"><b>原文识别依据</b>{{ fact }}</div><div v-if="item.missing_facts?.length"><b>尚缺信息</b>{{ item.missing_facts.map(factLabel).join('、') }}</div></div>
            <div v-if="mode === 'operator'" class="law-confirm-actions"><button class="btn pri" :disabled="busy" @click="confirmLaw(item.document_key, 'confirmed')">确认适用</button><button class="btn" :disabled="busy" @click="confirmLaw(item.document_key, 'rejected')">确认不适用</button><button class="btn" :disabled="busy" @click="confirmLaw(item.document_key, 'needs_more_facts')">需补充信息</button></div>
          </article>
        </div>
        <div class="gate-modal-foot"><span>确认结果将写入审计记录，并决定 Agent 可使用的法规范围。</span><b v-if="busy">正在保存并检查…</b></div>
      </BaseModal>
    </template>
  </div>
</template>
