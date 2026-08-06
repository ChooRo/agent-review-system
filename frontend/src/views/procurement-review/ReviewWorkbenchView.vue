<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import FindingCard from '../../components/business/FindingCard.vue'
import { confirmTask, createCollaborativeComment, getEvents, getFindings, getTask, saveDisposition, savePrimaryDecision, submitTask, updateCollaborativeComment } from '../../api/procurement-review'
import { useAuthStore } from '../../stores/auth'
import type { Finding, ReviewTask, RiskLevel } from '../../types/procurement-review'

const route = useRoute()
const auth = useAuthStore()
const task = ref<ReviewTask>()
const findings = ref<Finding[]>([])
const filter = ref<'all' | RiskLevel>('all')
const selected = ref('')
const notice = ref('')
const busy = ref(false)
const events = ref<{ id: string }[]>([])

const isReadonly = computed(() => task.value?.status === 'completed')
const mode = computed<'operator' | 'primary_supervisor' | 'collaborative_supervisor' | 'readonly'>(() => {
  if (isReadonly.value || auth.user?.roles[0]?.code === 'admin') return 'readonly'
  return (task.value?.task_role as any) ?? 'readonly'
})
const visible = computed(() => findings.value.filter((item) => filter.value === 'all' || item.risk_level === filter.value))
const rechecks = computed(() => findings.value.filter((item) => item.recheck_required && !item.primary_decision))
const selectedFinding = computed(() => findings.value.find((item) => item.id === selected.value) ?? findings.value[0])
const cardMode = (item: Finding) => task.value?.status === 'primary_recheck' && !item.recheck_required ? 'readonly' : mode.value

const riskLabel: Record<string, string> = { high: '高风险', medium: '中风险', low: '低风险', unknown: '证据不足' }
const riskColor: Record<string, string> = { high: 'var(--crimson)', medium: 'var(--ochre)', low: 'var(--green)', unknown: 'var(--stone)' }
const filterLabels: [string, string][] = [['all', '全部'], ['high', '高风险'], ['medium', '中风险'], ['low', '低风险'], ['unknown', '证据不足']]

// Clause data derived from findings' source info — one per finding, deduped by quote
const clauses = computed(() => {
  const seen = new Set<string>()
  return findings.value
    .filter((f) => f.source?.quote && !seen.has(f.source.quote))
    .map((f, i) => { seen.add(f.source.quote); return { ...f.source, findingId: f.id, index: i } })
})

async function load() {
  const projectId = String(route.params.projectId)
  const taskId = String(route.params.taskId)
  task.value = await getTask(projectId, taskId)
  if (!task.value) return
  findings.value = await getFindings(projectId, taskId)
  events.value = await getEvents(projectId, taskId)
  selected.value ||= findings.value[0]?.id ?? ''
}

function setFilter(value: string) { filter.value = value as 'all' | RiskLevel }

function message(error: unknown) {
  const status = (error as { status?: number }).status
  return status === 409 ? '数据已被他人修改，请刷新后重新确认。' : status === 403 ? '无权执行该操作。' : status === 422 ? '填写内容不符合要求。' : '操作失败，请稍后重试。'
}

async function disposition(item: Finding, action: 'accept' | 'partial_accept' | 'reject' | 'edit', comment: string) { if (!task.value) return; busy.value = true; try { await saveDisposition(task.value.project_id, task.value.id, item.id, action, comment, item.version); notice.value = '经办处置已保存。'; await load() } catch (e) { notice.value = message(e) } finally { busy.value = false } }
async function primary(item: Finding, decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined) { if (!task.value) return; busy.value = true; try { await savePrimaryDecision(task.value.project_id, task.value.id, item.id, decision, comment, risk, item.version); notice.value = '主责复核意见已保存。'; await load() } catch (e) { notice.value = message(e) } finally { busy.value = false } }
async function collaborative(item: Finding, comment: string) { if (!task.value) return; const own = item.collaborative_comments.find((e) => e.author === auth.user?.display_name); try { if (own) await updateCollaborativeComment(task.value.project_id, task.value.id, item.id, own.id, comment, own.version); else await createCollaborativeComment(task.value.project_id, task.value.id, item.id, comment, item.version); notice.value = '协同监督意见已保存。'; await load() } catch (e) { notice.value = message(e) } }
async function submit() { if (!task.value) return; busy.value = true; try { await submitTask(task.value.project_id, task.value.id); notice.value = '已提交采购部门主责监督复核。'; await load() } catch (e) { notice.value = message(e) } finally { busy.value = false } }
async function confirm() { if (!task.value) return; busy.value = true; try { await confirmTask(task.value.project_id, task.value.id); notice.value = '正式复核结果已确认，任务已锁定为只读。'; await load() } catch (e) { notice.value = message(e) } finally { busy.value = false } }

const statusLabel: Record<string, string> = { draft: '草稿', parsing: '解析中', reviewing: 'AI 审查中', operator_review: '待经办处理', primary_review: '待主责复核', primary_recheck: '待主责再次复核', completed: '已完成', failed: '处理失败', cancelled: '已取消', queued: '排队中' }

function onDisposition(action: 'accept' | 'partial_accept' | 'reject' | 'edit', comment: string) { if (selectedFinding.value) disposition(selectedFinding.value, action, comment) }
function onDecision(decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined) { if (selectedFinding.value) primary(selectedFinding.value, decision, comment, risk) }
function onOpinion(comment: string) { if (selectedFinding.value) collaborative(selectedFinding.value, comment) }

onMounted(load)
</script>

<template>
  <div class="page-head">
    <div class="crumb">审查任务 / {{ task?.project_id }} / 采购文件审核</div>
    <div class="page-title-row">
      <h2>
        {{ task?.title ?? '审查工作台' }}
        <span class="title-status">{{ isReadonly ? '已完成 · 只读' : statusLabel[task?.status ?? ''] ?? '审查进行中' }}</span>
      </h2>
    </div>
    <p>AI 输出仅为候选问题，原文、规则依据与人工处置在同一审查记录中留痕。</p>
  </div>

  <div class="page-body workbench-page">
    <div v-if="notice" class="status-banner">{{ notice }}</div>
    <div v-if="!task" class="state-card error-state">资源不存在或无权查看。</div>

    <template v-else>
      <div class="review-actions">
        <div class="ra-t"><b>当前阶段：</b>{{ isReadonly ? '正式复核已确认' : task.status === 'operator_review' ? '业务经办处置 AI 候选问题' : task.status === 'primary_recheck' ? '主责监督再次复核受影响条目' : '采购部门主责监督复核' }}</div>
        <span class="sp" />
        <button v-if="mode === 'operator'" class="btn pri" :disabled="busy || findings.some((f) => !f.operator_disposition)" @click="submit">确认审查结果</button>
        <button v-if="mode === 'primary_supervisor'" class="btn pri" :disabled="busy || (task.status === 'primary_recheck' ? rechecks.length > 0 : findings.some((f) => !f.primary_decision))" @click="confirm">确认复核结果</button>
      </div>

      <div v-if="isReadonly" class="status-banner done">此任务已完成，所有写入动作均已禁用。</div>

      <div class="wb">
        <!-- Left: Document Column -->
        <div class="wcol">
          <div class="whead"><span>采购文档</span><span class="ch-meta">{{ task.document?.file_name }}</span></div>
          <div class="wbody">
            <div class="doc-item sel">
              <div class="dt">
                <svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>
                采购文件<span class="role">主文件</span>
              </div>
              <div class="dm">{{ task.document?.file_name }} · V1</div>
            </div>
            <div
              v-for="(clause, i) in clauses"
              :key="clause.findingId"
              class="clause"
              :class="{ hot: i === 0 || clause.findingId === selectedFinding?.id }"
            >
              <div class="clabel">{{ clause.section_path.join(' · ') }}{{ clause.findingId === selectedFinding?.id ? ' · 当前定位' : '' }}</div>
              {{ clause.quote }}
            </div>
            <div v-if="!clauses.length" class="empty" style="margin-top:12px;padding:16px;font-size:11px">暂无原文数据</div>
          </div>
        </div>

        <!-- Middle: Review Findings -->
        <div class="wcol">
          <div class="whead"><span>审查结论</span><span class="ch-meta">{{ findings.length }} 项 AI 候选</span></div>
          <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding:10px 12px;border-bottom:1px solid var(--border);background:var(--ivory)">
            <span style="font-size:10px;color:var(--stone);margin-right:4px">风险筛选</span>
            <button v-for="[k, label] in filterLabels" :key="k" class="filter-btn" :class="{ on: filter === k }" @click="setFilter(k)">{{ label }}</button>
          </div>
          <div class="wbody">
            <button
              v-for="item in visible" :key="item.id"
              class="finding-nav-btn"
              :class="{ sel: selected === item.id, recheck: item.recheck_required }"
              @click="selected = item.id"
            >
              <span class="risk-dot" :style="{ background: riskColor[item.risk_level] }" />
              <span class="finding-nav-title">{{ item.title }}</span>
              <small>{{ riskLabel[item.risk_level] }}</small>
            </button>

            <FindingCard
              v-if="selectedFinding" :finding="selectedFinding" :mode="cardMode(selectedFinding)"
              @disposition="onDisposition" @decision="onDecision" @opinion="onOpinion"
            />

            <div class="audit">
              <h3>审计时间线</h3>
              <div class="audit-item"><b>AI 审查</b> 生成候选问题与证据</div>
              <div v-for="e in events.slice(0, 4)" :key="e.id" class="audit-item"><b>任务事件</b> {{ e.id }}</div>
              <div class="audit-item"><b>当前状态</b> {{ statusLabel[task.status] ?? task.status }}</div>
            </div>
          </div>
        </div>

        <!-- Right: Evidence Column -->
        <div class="wcol">
          <div class="whead"><span>证据链 / 审查依据</span></div>
          <div class="wbody">
            <div class="basis-summary">
              <b>原文证据</b>
              <span>{{ selectedFinding?.source?.section_path.join(' / ') ?? '—' }} · 第 {{ selectedFinding?.source?.page ?? '—' }} 页</span>
            </div>

            <div class="basis-group-title">命中规则 <span>{{ selectedFinding?.rule_refs.length ?? 0 }} 条</span></div>
            <div v-if="selectedFinding?.rule_refs.length">
              <div v-for="rule in selectedFinding.rule_refs" :key="rule.id" class="rule" style="padding:11px;box-shadow:none">
                <span class="rid">{{ rule.id }}</span>
                <div class="rt" style="font-size:13px">{{ rule.title }}</div>
              </div>
            </div>
            <div v-else class="empty" style="padding:13px;font-size:11px">选择左侧审查结论后可查看对应规则依据</div>

            <div class="note" style="font-size:10.5px">
              <b>原文引用：</b>{{ selectedFinding?.source?.quote ?? '—' }}
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
