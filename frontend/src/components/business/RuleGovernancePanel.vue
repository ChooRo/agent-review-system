<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiErrorMessage } from '../../api'
import { confirmRule, createRule, expireRule, getRule, listRules, listRuleVersions, reactivateRule, updateRule } from '../../api/rules'
import BaseModal from '../base/BaseModal.vue'
import { useAuthStore } from '../../stores/auth'
import { canMaintainRules } from '../../policies/permissions'
import type { GovernanceRule, RuleListFilters, RulePayload, RuleRiskLevel, RuleSourceType, RuleStatus, RuleVersion } from '../../types/rules'

const auth = useAuthStore()
const rules = ref<GovernanceRule[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const filters = ref<RuleListFilters>({ keyword: '', status: '', module: '', department: '' })
const selected = ref<GovernanceRule>()
const versions = ref<RuleVersion[]>([])
const detailLoading = ref(false)
const editRule = ref<GovernanceRule>()
const editorOpen = ref(false)
const expireTarget = ref<GovernanceRule>()
const expireReason = ref('')

const statuses: { value: RuleStatus; label: string }[] = [
  { value: 'draft', label: '草稿' },
  { value: 'pending_confirmation', label: '待确认' },
  { value: 'published', label: '已发布' },
  { value: 'expired', label: '已失效' },
]
const riskLabels: Record<RuleRiskLevel, string> = { mandatory: '强制规则', general: '一般规则' }
const sourceLabels: Record<RuleSourceType, string> = { manual: '人工维护', ai_candidate: 'AI 候选', legal_extraction: '法规抽取候选' }
const modules = computed(() => [...new Set(rules.value.map((rule) => rule.module).filter(Boolean))].sort())
const departments = computed(() => [...new Set(rules.value.map((rule) => rule.department).filter(Boolean))].sort())
const isAdmin = computed(() => canMaintainRules(auth.user))

function statusLabel(status: RuleStatus) { return statuses.find((item) => item.value === status)?.label ?? status }
function riskLabel(value: RuleRiskLevel) { return riskLabels[value] }
function sourceLabel(value: RuleSourceType) { return sourceLabels[value] }
function statusClass(status: RuleStatus) { return status === 'published' ? 'done' : status === 'expired' ? 'muted' : status === 'pending_confirmation' ? 'todo' : 'doing' }
function formatTime(value?: string) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '未记录' }
function canEdit(rule?: GovernanceRule) { return isAdmin.value && (!rule || rule.status !== 'expired') }
function canCreate() { return isAdmin.value }
function canConfirm(rule: GovernanceRule) { return isAdmin.value && rule.status === 'pending_confirmation' }
function canExpire(rule: GovernanceRule) { return isAdmin.value && rule.status === 'published' }
function canReactivate(rule: GovernanceRule) { return isAdmin.value && rule.status === 'expired' }

async function load() {
  loading.value = true
  error.value = ''
  try { rules.value = await listRules(filters.value) }
  catch (reason) { error.value = apiErrorMessage(reason) }
  finally { loading.value = false }
}

async function openDetail(rule: GovernanceRule) {
  selected.value = rule
  versions.value = []
  detailLoading.value = true
  try {
    const [detail, history] = await Promise.all([getRule(rule.id), listRuleVersions(rule.id)])
    selected.value = detail
    versions.value = history
  } catch (reason) {
    notice.value = apiErrorMessage(reason)
  } finally { detailLoading.value = false }
}

function newPayload(rule?: GovernanceRule): RulePayload {
  return {
    title: rule?.title ?? '', description: rule?.description ?? '', decision_criteria: rule?.decision_criteria ?? '', risk_level: rule?.risk_level ?? 'general',
    module: 'procurement', department: rule?.department ?? '',
    tags: rule?.tags ?? [], source_type: rule?.source_type ?? 'manual', legal_document_key: rule?.legal_document_key ?? '', legal_unit_ids: rule?.legal_unit_ids ?? [],
  }
}
const form = ref<RulePayload>(newPayload())
function openEditor(rule?: GovernanceRule) { editRule.value = rule; form.value = newPayload(rule); editorOpen.value = true }

async function saveRule() {
  if (!form.value.title.trim() || !form.value.description.trim() || !form.value.decision_criteria.trim() || !form.value.module.trim() || !form.value.department.trim() || !form.value.source_type.trim()) {
    notice.value = '请完整填写规则标题、说明、判定条件、模块、部门和来源类型。'
    return
  }
  busy.value = true
  try {
    if (editRule.value) {
      const { module: _module, department: _department, source_type: _sourceType, ...updatable } = form.value
      await updateRule(editRule.value.id, { ...updatable, version: editRule.value.version })
    }
    else await createRule(form.value)
    editorOpen.value = false
    notice.value = '规则已保存，已刷新规则治理列表。'
    await load()
  } catch (reason) { await handleActionError(reason) } finally { busy.value = false }
}

async function runAction(action: () => Promise<GovernanceRule>, success: string) {
  busy.value = true
  try { await action(); selected.value = undefined; expireTarget.value = undefined; notice.value = success; await load() }
  catch (reason) { await handleActionError(reason) } finally { busy.value = false }
}
async function handleActionError(reason: unknown) {
  notice.value = apiErrorMessage(reason)
  if ((reason as { status?: number }).status === 409) await load()
}
function splitValues(value: string) { return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean) }

onMounted(load)
</script>

<template>
  <section class="rule-governance" aria-label="执行规则治理">
    <div v-if="notice" class="status-banner">{{ notice }}</div>
    <div class="lifecycle-strip" aria-label="规则状态统计">
      <div v-for="item in statuses" :key="item.value" class="lifecycle-cell"><b>{{ rules.filter((rule) => rule.status === item.value).length }}</b><span>{{ item.label }}</span></div>
    </div>

    <form class="toolbar rule-filters" @submit.prevent="load">
      <input v-model.trim="filters.keyword" class="rule-keyword" placeholder="搜索规则标题、判定条件" aria-label="关键词" />
      <select v-model="filters.status" aria-label="规则状态"><option value="">全部状态</option><option v-for="item in statuses" :key="item.value" :value="item.value">{{ item.label }}</option></select>
      <select v-model="filters.module" aria-label="规则模块"><option value="">全部模块</option><option v-for="module in modules" :key="module" :value="module">{{ module }}</option></select>
      <select v-model="filters.department" aria-label="归属部门"><option value="">全部部门</option><option v-for="department in departments" :key="department" :value="department">{{ department }}</option></select>
      <button class="btn" type="submit">筛选</button>
      <span class="sp" />
      <button v-if="canCreate()" class="btn pri" type="button" @click="openEditor()">+ 新建规则</button>
    </form>

    <div v-if="loading" class="state-card">正在加载执行规则…</div>
    <div v-else-if="error" class="state-card error-state">{{ error }} <button class="btn" style="margin-left:12px" @click="load">重试</button></div>
    <div v-else-if="!rules.length" class="empty rule-empty">
      <div><strong>暂无匹配的执行规则</strong><p>可调整筛选条件；具备权限的人员可新建待治理规则。</p></div>
    </div>
    <div v-else class="rule-list">
      <article v-for="rule in rules" :key="rule.id" class="rule" :class="{ 'record-muted': rule.status === 'expired' }">
        <div class="rule-topline"><span class="rid">{{ rule.id }}</span><span class="pill" :class="statusClass(rule.status)">{{ statusLabel(rule.status) }}</span><span class="risk-badge">{{ riskLabel(rule.risk_level) }}</span><span class="rule-version">v{{ rule.version }}</span><span class="sp" /><button class="btn rule-action" @click="openDetail(rule)">查看详情</button></div>
        <h3 class="rt">{{ rule.title }}</h3>
        <p class="rd">{{ rule.description }}</p>
        <div class="rmeta"><span class="chip">{{ rule.module }}</span><span class="chip">{{ rule.department }}</span><span class="chip">来源：{{ sourceLabel(rule.source_type) }}</span><span v-for="tag in rule.tags" :key="tag" class="chip">{{ tag }}</span></div>
        <div class="rule-actions">
          <span>更新：{{ formatTime(rule.updated_at || rule.created_at) }} · {{ rule.updated_by || rule.created_by || '未记录' }}</span><span class="sp" />
          <button v-if="canEdit(rule)" class="btn rule-action" @click="openEditor(rule)">编辑</button>
          <button v-if="canConfirm(rule)" class="btn pri rule-action" :disabled="busy" @click="runAction(() => confirmRule(rule.id, rule.version), '规则已确认发布。')">确认发布</button>
          <button v-if="canExpire(rule)" class="btn rule-action" :disabled="busy" @click="expireTarget = rule; expireReason = ''">标记失效</button>
          <button v-if="canReactivate(rule)" class="btn pri rule-action" :disabled="busy" @click="runAction(() => reactivateRule(rule.id, rule.version), '规则已重新启用，已进入待确认状态。')">重新启用</button>
        </div>
      </article>
    </div>

    <BaseModal v-if="selected" :title="`${selected.id} · 执行规则详情`" @close="selected = undefined">
      <div v-if="detailLoading" class="state-card">正在加载规则详情和版本记录…</div>
      <template v-else>
        <div class="rmeta" style="margin-top:0"><span class="pill" :class="statusClass(selected.status)">{{ statusLabel(selected.status) }}</span><span class="chip">{{ selected.module }}</span><span class="chip">{{ selected.department }}</span><span class="chip">v{{ selected.version }}</span></div>
        <section class="detail-section"><h3>规则正文</h3><p>{{ selected.description }}</p></section>
        <section class="detail-section"><h3>判定条件</h3><p>{{ selected.decision_criteria }}</p></section>
        <section class="detail-section"><h3>来源依据</h3><p>来源类型：{{ sourceLabel(selected.source_type) }}</p><p>法规文档：{{ selected.legal_document_key || '未关联' }}</p><p>法规条款单元：{{ selected.legal_unit_ids.length ? selected.legal_unit_ids.join('、') : '未关联' }}</p><p v-if="selected.expiry_reason">失效原因：{{ selected.expiry_reason }}</p></section>
        <section class="detail-section"><h3>版本记录</h3><ol v-if="versions.length" class="version-timeline"><li v-for="item in versions" :key="item.snapshot_id"><b>v{{ item.version }}</b><span>{{ item.event }}</span><small>{{ formatTime(item.recorded_at) }} · {{ item.updated_by || item.created_by || '未记录' }}</small></li></ol><p v-else class="detail-empty">暂无版本记录。</p></section>
      </template>
    </BaseModal>

    <BaseModal v-if="editorOpen" :title="editRule ? '编辑执行规则' : '新建执行规则'" @close="editorOpen = false">
      <form class="form-grid" @submit.prevent="saveRule">
        <div class="field full"><label>规则标题</label><input v-model.trim="form.title" required /></div>
        <div class="field full"><label>规则说明</label><textarea v-model.trim="form.description" required /></div>
        <div class="field full"><label>判定条件</label><textarea v-model.trim="form.decision_criteria" required /></div>
        <div class="field"><label>规则级别</label><select v-model="form.risk_level"><option value="mandatory">强制规则</option><option value="general">一般规则</option></select></div>
        <div class="field"><label>来源类型</label><select v-model="form.source_type" :disabled="Boolean(editRule)"><option value="manual">人工维护</option><option value="ai_candidate">AI 候选</option><option value="legal_extraction">法规抽取候选</option></select></div>
        <div class="field"><label>规则模块</label><input v-model.trim="form.module" required readonly /></div>
        <div class="field"><label>归属部门</label><input v-model.trim="form.department" required :readonly="Boolean(editRule)" /></div>
        <div class="field full"><label>标签（逗号分隔）</label><input :value="form.tags.join(', ')" @input="form.tags = splitValues(($event.target as HTMLInputElement).value)" /></div>
        <div class="field"><label>来源法规文档键</label><input v-model.trim="form.legal_document_key" /></div>
        <div class="field"><label>来源法规条款单元（逗号分隔）</label><input :value="form.legal_unit_ids.join(', ')" @input="form.legal_unit_ids = splitValues(($event.target as HTMLInputElement).value)" /></div>
        <div class="modal-foot"><button class="btn" type="button" @click="editorOpen = false">取消</button><button class="btn pri" :disabled="busy">保存</button></div>
      </form>
    </BaseModal>

    <BaseModal v-if="expireTarget" title="标记规则失效" @close="expireTarget = undefined">
      <p class="note" style="margin-top:0">失效后规则不再用于后续审查；历史任务引用和版本记录保留。</p>
      <div class="field"><label>失效原因</label><textarea v-model.trim="expireReason" required placeholder="请填写到期、被替代或适用范围变化等原因" /></div>
      <div class="modal-foot"><button class="btn" @click="expireTarget = undefined">取消</button><button class="btn pri" :disabled="busy || !expireReason" @click="runAction(() => expireRule(expireTarget!.id, expireTarget!.version, expireReason), '规则已标记失效。')">确认失效</button></div>
    </BaseModal>
  </section>
</template>

<style scoped>
.lifecycle-strip { display:grid; grid-template-columns:repeat(4,1fr); margin-bottom:14px; border:1px solid var(--border); border-radius:12px; overflow:hidden; background:var(--white); box-shadow:rgba(0,0,0,.04) 0 3px 16px; }
.lifecycle-cell { padding:12px 14px; border-right:1px solid var(--border); }.lifecycle-cell:last-child { border-right:0; }.lifecycle-cell b { display:block; font:600 23px var(--serif); color:var(--ink); }.lifecycle-cell span { color:var(--stone); font-size:10.5px; }
.rule-filters { flex-wrap:wrap; }.rule-filters select,.rule-keyword { height:34px; border:1px solid var(--border2); border-radius:7px; background:var(--ivory); color:var(--ink2); padding:0 10px; font:500 11.5px var(--sans); }.rule-keyword { min-width:205px; background:var(--white); }.rule-list { display:grid; gap:11px; }.rule { margin-bottom:0; }.rule-topline,.rule-actions { display:flex; align-items:center; gap:7px; flex-wrap:wrap; }.risk-badge { font-size:10px; color:var(--crimson); background:var(--crimson-soft); padding:2px 8px; border-radius:20px; font-weight:700; }.rule-version,.rule-actions > span:first-child { color:var(--stone); font-size:10.5px; }.rule-actions { margin-top:11px; padding-top:9px; border-top:1px dashed var(--border2); }.rule-action { padding:3px 10px; font-size:11px; }.rule-empty { min-height:280px; text-align:center; }.rule-empty strong { display:block; font:18px var(--serif); color:var(--ink); }.rule-empty p { margin-top:7px; }.detail-section { margin-top:16px; padding-top:14px; border-top:1px solid var(--border); }.detail-section h3 { font-size:13px; }.detail-section p { margin-top:6px; color:var(--olive); font-size:12px; line-height:1.7; }.version-timeline { list-style:none; margin-top:9px; display:grid; gap:9px; }.version-timeline li { display:grid; grid-template-columns:38px 1fr; gap:3px 8px; padding-left:12px; border-left:2px solid var(--terra-soft); font-size:12px; }.version-timeline small { grid-column:2; color:var(--stone); }.detail-empty { color:var(--stone); }.muted { background:var(--sand); color:var(--olive); }
@media (max-width:1000px) { .lifecycle-strip { grid-template-columns:repeat(2,1fr); }.lifecycle-cell:nth-child(2) { border-right:0; }.lifecycle-cell:nth-child(-n+2) { border-bottom:1px solid var(--border); }.rule-filters .sp { display:none; }.rule-filters .btn.pri { margin-left:auto; } }
@media (max-width:620px) { .lifecycle-strip { grid-template-columns:1fr; }.lifecycle-cell,.lifecycle-cell:nth-child(2) { border-right:0; border-bottom:1px solid var(--border); }.lifecycle-cell:last-child { border-bottom:0; }.rule-keyword { width:100%; }.rule-filters select { flex:1; min-width:130px; } }
</style>
