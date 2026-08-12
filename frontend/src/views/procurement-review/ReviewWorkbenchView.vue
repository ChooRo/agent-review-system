<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import FindingCard from '../../components/business/FindingCard.vue'
import {
  confirmTask, createCollaborativeComment, getFindings, getTask,
  saveDisposition, savePrimaryDecision, submitTask, updateCollaborativeComment,
} from '../../api/procurement-review'
import { useAuthStore } from '../../stores/auth'
import { workbenchMode } from '../../policies/permissions'
import type { Finding, ReviewTask, RiskLevel } from '../../types/procurement-review'
import { evidenceText } from '../../utils/evidence-text'

const route = useRoute()
const auth = useAuthStore()
const task = ref<ReviewTask>()
const findings = ref<Finding[]>([])
const filter = ref<'all' | RiskLevel>('all')
const selected = ref('')
const activeClause = ref('')
const notice = ref('')
const busy = ref(false)
const basisExpanded = ref(false)
const applicabilityExpanded = ref(false)

const isReadonly = computed(() => task.value?.status === 'completed')
const mode = computed(() => workbenchMode(auth.user, task.value))
const visible = computed(() => findings.value.filter((item) => filter.value === 'all' || item.risk_level === filter.value))
const rechecks = computed(() => findings.value.filter((item) => item.recheck_required && !item.primary_decision))
const selectedFinding = computed(() => findings.value.find((item) => item.id === selected.value) ?? visible.value[0] ?? findings.value[0])
const cardMode = (item: Finding) => task.value?.status === 'primary_recheck' && !item.recheck_required ? 'readonly' : mode.value

const filterLabels: [string, string][] = [
  ['all', '全部'], ['high', '不一致（高）'], ['medium', '不一致（中）'], ['low', '低风险'], ['unknown', '无法判断'],
]
const clauses = computed(() => findings.value.map((finding) => ({
  findingId: finding.id,
  path: finding.source.section_path.length ? finding.source.section_path.join(' · ') : `第 ${finding.source.page || '—'} 页`,
  quote: evidenceText(finding.source.quote, 520),
})).filter((item) => item.quote))

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
const frozenApplicableLaws = computed(() => {
  const freezes = task.value?.legal_context_freeze?.length
    ? task.value.legal_context_freeze
    : applicableLaws.value.map((item) => item.source_freeze)
  return [...new Map(freezes.map((freeze) => [freeze.document_key, freeze])).values()]
})
function freezeLabel(documentKey: string) {
  const freeze = frozenApplicableLaws.value.find((item) => item.document_key === documentKey)
  return freeze ? `元数据版本 ${freeze.metadata_version} · 内容已冻结` : '未返回冻结版本信息'
}
function freezeDetail(documentKey: string) {
  const freeze = frozenApplicableLaws.value.find((item) => item.document_key === documentKey)
  return freeze ? `冻结版本：元数据 v${freeze.metadata_version}；内容指纹 ${freeze.content_fingerprint || '未返回'}` : '后端未返回冻结版本信息。'
}
const allRules = computed(() => {
  const rules = new Map<string, { id: string; title: string }>()
  findings.value.flatMap((finding) => finding.rule_refs ?? []).forEach((rule) => rules.set(rule.id, rule))
  return [...rules.values()]
})
const currentRules = computed(() => selectedFinding.value?.rule_refs ?? [])
const currentLegalSources = computed(() => {
  const sources = new Map<string, Set<string>>()
  ;(selectedFinding.value?.legal_refs ?? []).forEach((ref) => {
    const articles = sources.get(ref.document_title) ?? new Set<string>()
    if (ref.article_no) articles.add(ref.article_no)
    sources.set(ref.document_title, articles)
  })
  return [...sources].map(([title, articles]) => ({ title, articles: [...articles] }))
})

async function load() {
  const projectId = String(route.params.projectId)
  const taskId = String(route.params.taskId)
  task.value = await getTask(projectId, taskId)
  findings.value = await getFindings(projectId, taskId)
  selected.value ||= findings.value[0]?.id ?? ''
  activeClause.value ||= findings.value[0]?.id ?? ''
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
  activeClause.value = item.id
  await nextTick()
  const target = document.getElementById(`clause-${item.id}`)
  const scroller = target?.closest('.wbody')
  if (target && scroller) scroller.scrollTo({ top: Math.max(0, target.offsetTop - (scroller as HTMLElement).offsetTop - 20), behavior: 'smooth' })
}

const statusLabel: Record<string, string> = {
  draft: '草稿', queued: '排队中', parsing: '解析中', reviewing: 'AI 审查中', operator_review: '待业务经办初审',
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
          <div class="whead"><span>采购文档</span><span class="ch-meta">1 / 3 份参与</span></div>
          <div class="wbody">
            <div class="doc-item sel"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>采购文件<span class="role">基准</span></div><div class="dm">{{ task.document?.file_name }} · V1</div></div>
            <div class="doc-item dim" aria-disabled="true"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>响应文件<span class="role">未参与</span></div><div class="dm">当前为采购文件单文件审核</div></div>
            <div class="doc-item dim" aria-disabled="true"><div class="dt"><svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>拟签合同<span class="role">未参与</span></div><div class="dm">当前为采购文件单文件审核</div></div>
            <div v-for="clause in clauses" :id="`clause-${clause.findingId}`" :key="clause.findingId" class="clause" :class="{ hot: activeClause === clause.findingId }">
              <div class="clabel">采购文件 · {{ clause.path }}<span v-if="activeClause === clause.findingId"> · 当前定位</span></div>
              {{ clause.quote }}
            </div>
            <div v-if="!clauses.length" class="empty">暂无可定位的采购文件原文</div>
          </div>
        </div>

        <div class="wcol">
          <div class="whead"><span>AI 审查结果 · 单文件合规</span><div class="whead-actions"><button v-if="mode === 'operator'" class="btn pri" :disabled="busy || findings.some((finding) => !finding.operator_disposition)" @click="submit">确认审查结果</button><button v-if="mode === 'primary_supervisor'" class="btn pri" :disabled="busy || (task.status === 'primary_recheck' ? rechecks.length > 0 : findings.some((finding) => !finding.primary_decision))" @click="confirm">确认复核结果</button></div></div>
          <div class="wbody">
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
              <b>本次适用法规</b>
              <span v-if="legalApplicability">正式适用 {{ applicableLaws.length }} 份 · 待确认 {{ potentialLaws.length }} 份 · 事实不足 {{ insufficientFactsLaws.length }} 份</span>
              <span v-else>任务尚未返回适用法规匹配结果</span><i aria-hidden="true">{{ applicabilityExpanded ? '⌃' : '⌄' }}</i>
            </button>
            <div v-if="applicabilityExpanded" class="applicability-detail">
              <template v-if="legalApplicability">
                <div class="basis-group-title">正式审查依据 <span>{{ applicableLaws.length }} 份</span></div>
                <template v-if="applicableLaws.length">
                  <div v-for="item in applicableLaws" :key="item.document_key" class="basis-source-row applicable-law">
                    <div><b>{{ item.document_key }}</b><small>正式适用 · {{ freezeLabel(item.document_key) }}</small></div>
                    <details><summary>匹配说明与冻结版本</summary><p v-if="item.reasons.length">{{ item.reasons.join('；') }}</p><p v-else>后端未返回匹配理由。</p><p v-if="item.evidence.task_facts.length">任务事实：{{ item.evidence.task_facts.join('；') }}</p><p>{{ freezeDetail(item.document_key) }}</p></details>
                  </div>
                </template>
                <div v-else class="basis-empty">本次未匹配到正式适用法规。</div>

                <div v-if="potentialLaws.length" class="basis-group-title">可能适用 / 待确认 <span>{{ potentialLaws.length }} 份</span></div>
                <div v-for="item in potentialLaws" :key="item.document_key" class="applicability-notice potential-law"><b>{{ item.document_key }} · 待确认，不作为当前结论依据</b><details><summary>查看匹配理由</summary><p>{{ item.reasons.length ? item.reasons.join('；') : '后端未返回待确认理由。' }}</p></details></div>

                <div v-if="insufficientFactsLaws.length" class="basis-group-title">事实不足 <span>{{ insufficientFactsLaws.length }} 份</span></div>
                <div v-for="item in insufficientFactsLaws" :key="item.document_key" class="applicability-notice insufficient-law"><b>{{ item.document_key }} · 事实不足，不作为当前结论依据</b><details><summary>查看缺失事实</summary><p>{{ item.missing_facts.length ? item.missing_facts.join('；') : '后端未返回缺失事实。' }}</p></details></div>

                <p v-if="frozenApplicableLaws.length" class="applicability-freeze">正式依据已冻结 {{ frozenApplicableLaws.length }} 份；展开对应“匹配说明”可查看版本信息。</p>
              </template>
              <div v-else class="basis-empty">后端尚未返回本任务的适用法规匹配数据。</div>
            </div>

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
              <div v-for="source in currentLegalSources" :key="source.title" class="basis-source-row"><span>{{ source.title }}</span><small>{{ source.articles.length ? source.articles.join('、') : '法规文档' }}</small></div>
            </template>
            <div v-else class="basis-empty">当前结论未关联法规文档依据</div>

            <div class="basis-group-title">已发布执行规则 <span>{{ currentRules.length }} 条</span></div>
            <template v-if="currentRules.length">
              <div v-for="rule in currentRules" :key="rule.id" class="rule current-rule"><span class="rid">{{ rule.id }}</span><div class="rt">{{ rule.title }}</div></div>
            </template>
            <div v-else class="basis-empty">当前结论未命中已发布执行规则</div>

            <div class="note evidence-note"><b>文档层</b>展示解析法规文档形成的引用；<b>规则层</b>仅展示已发布且真实命中的执行规则；未形成真实经验案例关联时不展示案例数量。</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
