<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { Finding, RiskLevel } from '../../types/procurement-review'

const props = defineProps<{ finding: Finding; mode: 'operator' | 'primary_supervisor' | 'collaborative_supervisor' | 'readonly'; selected?: boolean }>()
const emit = defineEmits<{
  select: []; locate: []
  disposition: [action: 'accept' | 'partial_accept' | 'reject' | 'edit', comment: string]
  decision: [decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined]
  opinion: [comment: string]
}>()

type OperatorAction = 'accept' | 'partial_accept' | 'reject' | 'edit'
const pendingAction = ref<OperatorAction | null>(null)
const editingOpinion = ref(false)
const note = ref('')
const decisionRisk = ref<RiskLevel>(props.finding.risk_level)
const riskClass: Record<RiskLevel, string> = { high: 'high', medium: 'mid', low: 'pass', unknown: 'unknown' }
const riskLabel: Record<RiskLevel, string> = { high: '不一致（高）', medium: '不一致（中）', low: '低风险', unknown: '无法判断' }
const actionLabel: Record<OperatorAction, string> = { accept: '采纳', partial_accept: '部分采纳', reject: '不采纳', edit: '修改意见' }
const operatorStatus = computed(() => {
  const action = props.finding.operator_disposition?.action
  return action ? `经办已${actionLabel[action]}` : '经办未处置'
})
const operatorStatusClass = computed(() => {
  const action = props.finding.operator_disposition?.action
  return action === 'accept' ? 'accept' : action === 'partial_accept' || action === 'edit' ? 'partial' : action === 'reject' ? 'reject' : ''
})
const primaryStatus = computed(() => {
  const decision = props.finding.primary_decision?.decision
  return decision ? `主责已${({ receive: '接收', adjust: '调整', reject: '拒绝' } as const)[decision]}` : '待主责复核'
})
const primaryStatusClass = computed(() => props.finding.primary_decision?.decision === 'receive' ? 'accept' : props.finding.primary_decision?.decision === 'adjust' ? 'adjust' : props.finding.primary_decision?.decision === 'reject' ? 'reject' : '')
const matchText = computed(() => props.finding.rule_refs.length ? '匹配方式：执行规则精确命中' : props.finding.legal_refs?.length ? '匹配方式：法规文档语义关联' : '匹配方式：采购文件原文语义审查')

async function choose(action: OperatorAction) {
  if (action === 'accept') { emit('disposition', action, ''); return }
  pendingAction.value = action
  note.value = props.finding.operator_disposition?.action === action ? props.finding.operator_disposition.comment : ''
  await nextTick()
  document.getElementById(`reason-${props.finding.id}`)?.focus()
}
function saveReason() {
  if (!pendingAction.value || !note.value.trim()) return
  emit('disposition', pendingAction.value, note.value.trim())
  pendingAction.value = null
}
</script>

<template>
  <div class="card appear" :class="riskClass[finding.risk_level]" @click="emit('select')">
    <div class="ctop"><span class="risk" :class="riskClass[finding.risk_level]">{{ riskLabel[finding.risk_level] }}</span><span class="ctitle">{{ finding.title }}</span></div>
    <div class="cbody">{{ finding.description }}<template v-if="finding.suggestion"><br>建议：{{ finding.suggestion }}</template></div>
    <button class="link-btn locate-original" type="button" @click.stop="emit('locate')">定位原文 →</button>
    <div class="depth" :class="{ weak: !finding.rule_refs.length }">{{ finding.rule_refs.length ? '◉' : '◔' }} {{ matchText }}</div>
    <div class="trace">
      <div class="tl">证据链 / 依据</div>
      <div class="ti"><span class="ico">▱</span>采购文件 · 第 {{ finding.source.page || '—' }} 页 · {{ finding.source.section_path.join(' / ') || '未识别章节' }}</div>
      <div v-for="(ref, index) in finding.legal_refs ?? []" :key="`${ref.document_title}-${ref.article_no}-${index}`" class="ti"><span class="ico">⚖</span>{{ ref.document_title }} {{ ref.article_no }}</div>
      <div v-for="rule in finding.rule_refs" :key="rule.id" class="ti"><span class="ico">◇</span>{{ rule.id }}《{{ rule.title }}》</div>
    </div>

    <div class="anno-bar" :class="{ 'collab-recheck': finding.recheck_required }" @click.stop>
      <span class="anno-label">{{ mode === 'readonly' ? '审查留痕' : mode === 'operator' ? '批注处理' : mode === 'collaborative_supervisor' ? '协同复核' : '经办处置' }}</span>
      <span class="anno-status" :class="operatorStatusClass">{{ operatorStatus }}</span>
      <span v-if="mode !== 'operator'" class="anno-status" :class="primaryStatusClass">{{ primaryStatus }}</span>
      <span class="anno-sp" />

      <template v-if="mode === 'operator'">
        <button v-for="action in (['accept','partial_accept','reject','edit'] as OperatorAction[])" :key="action" class="btn anno-btn" :class="{ on: finding.operator_disposition?.action === action && !pendingAction }" type="button" @click="choose(action)">{{ actionLabel[action] }}</button>
        <div v-if="pendingAction" class="anno-note"><b>{{ actionLabel[pendingAction] }}说明（必填）</b><textarea :id="`reason-${finding.id}`" v-model="note" class="inline-reason" placeholder="请填写采纳范围、事实依据或业务原因" /><div class="anno-reason-actions"><button class="btn anno-btn" type="button" @click="pendingAction = null">取消</button><button class="btn pri anno-btn" type="button" :disabled="!note.trim()" @click="saveReason">确认{{ actionLabel[pendingAction] }}</button></div></div>
        <div v-else-if="finding.operator_disposition?.comment" class="anno-note"><b>经办说明：</b>{{ finding.operator_disposition.comment }}</div>
      </template>

      <template v-else-if="mode === 'primary_supervisor'">
        <button class="btn anno-btn" :class="{ on: finding.primary_decision?.decision === 'receive' }" type="button" @click="$emit('decision', 'receive', '', undefined)">接收</button>
        <button class="btn anno-btn" :class="{ on: finding.primary_decision?.decision === 'adjust' }" type="button" @click="pendingAction = 'edit'">调整</button>
        <button class="btn anno-btn" :class="{ on: finding.primary_decision?.decision === 'reject' }" type="button" @click="pendingAction = 'reject'">拒绝</button>
        <div v-if="pendingAction" class="anno-note"><b>{{ pendingAction === 'edit' ? '调整意见' : '拒绝原因' }}（必填）</b><textarea v-model="note" class="inline-reason" placeholder="请填写主责监督意见" /><select v-if="pendingAction === 'edit'" v-model="decisionRisk" class="inline-risk"><option value="high">高风险</option><option value="medium">中风险</option><option value="low">低风险</option><option value="unknown">无法判断</option></select><div class="anno-reason-actions"><button class="btn anno-btn" type="button" @click="pendingAction = null">取消</button><button class="btn pri anno-btn" type="button" :disabled="!note.trim()" @click="$emit('decision', pendingAction === 'edit' ? 'adjust' : 'reject', note, pendingAction === 'edit' ? decisionRisk : undefined); pendingAction = null">确认</button></div></div>
        <div v-if="finding.operator_disposition?.comment" class="anno-note"><b>经办意见：</b>{{ finding.operator_disposition.comment }}</div>
        <div v-if="finding.primary_decision?.comment" class="anno-note"><b>主责监督意见：</b>{{ finding.primary_decision.comment }}</div>
      </template>

      <template v-else-if="mode === 'collaborative_supervisor'">
        <span class="anno-status">非强制</span><button class="btn anno-btn" type="button" @click="editingOpinion = true">{{ finding.collaborative_comments.length ? '修改意见' : '填写意见' }}</button>
        <div v-if="editingOpinion" class="anno-note"><b>协同复核意见</b><textarea v-model="note" class="inline-reason" placeholder="填写本部门专业意见" /><div class="anno-reason-actions"><button class="btn anno-btn" type="button" @click="editingOpinion = false">取消</button><button class="btn pri anno-btn" type="button" :disabled="!note.trim()" @click="$emit('opinion', note); editingOpinion = false">保存意见</button></div></div>
      </template>

      <span v-else class="accept-tag">只读</span>
      <div v-if="finding.collaborative_comments.length" class="collab-list"><div v-for="item in finding.collaborative_comments" :key="item.id" class="collab-opinion"><b>{{ item.department }}</b><span>{{ item.comment }}</span><small>{{ item.author }}</small></div></div>
    </div>
  </div>
</template>
