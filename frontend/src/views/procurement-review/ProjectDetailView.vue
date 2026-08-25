<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseModal from '../../components/base/BaseModal.vue'
import { apiErrorMessage } from '../../api'
import { createTask, getProject, listTasks, lockFinal, retryTask, uploadRectification } from '../../api/procurement-review'
import type { Project, ReviewTask } from '../../types/procurement-review'

const route = useRoute()
const router = useRouter()
const project = ref<Project>()
const tasks = ref<ReviewTask[]>([])
const loading = ref(true)
const showCreate = ref(false)
const createStep = ref(1) // 1 = choose type, 2 = upload file
const draftMode = ref<'single' | 'dual' | 'triple'>('single')
const title = ref('')
const file = ref<File>()
const uploadError = ref('')
const pending = ref(false)
const projectId = computed(() => String(route.params.projectId))
const error = ref('')
const retryingTaskIds = ref(new Set<string>())
const taskErrors = ref<Record<string, string>>({})
const rectifyingTaskIds = ref(new Set<string>())
const retryPermissionMessage = '仅任务原经办人可重新处理，请切换到经办人账号。'
let progressTimer: number | undefined
let refreshInFlight = false

function startCreate() { createStep.value = 1; draftMode.value = 'single'; title.value = ''; file.value = undefined; uploadError.value = ''; showCreate.value = true }
function selectType(mode: 'single' | 'dual' | 'triple') { if (mode !== 'single') return; draftMode.value = mode; createStep.value = 2 }
function backToStep1() { createStep.value = 1 }

const statusLabel: Record<string, string> = {
  draft: '草稿', parsing: '解析中', reviewing: 'AI 审查中', applicability_review: '待确认适用法规',
  operator_review: '待经办处理', primary_review: '待主责复核',
  primary_recheck: '待主责再次复核', completed: '已完成',
  rectification_draft: '整改版待审查', final_locked: '终版已锁定',
  failed: '处理失败', cancelled: '已取消', queued: '排队中',
}

const activeStatuses = new Set<ReviewTask['status']>(['queued', 'parsing', 'reviewing'])
function isActiveTask(task: ReviewTask) { return activeStatuses.has(task.status) }
function taskProgress(task: ReviewTask) {
  const value = Number(task.progress)
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0
}
function progressStage(task: ReviewTask) {
  if (task.status === 'queued') return '等待解析'
  if (task.status === 'parsing') return '文档解析'
  const labels: Record<string, string> = {
    parse_documents: 'MinerU 文档解析', quality_check: '解析质量检查', structure_profile: '文档结构理解',
    build_logical_units: '逻辑单元重建', assemble_review_batches: 'Review Batch 校验', extract_candidates: '大模型分批业务理解',
    build_ledger: '全局归并与采购台账', build_scene_view: '采购主题视图', global_validation: '文件全局检查',
    match_rules: '规则与全量法规装载', agent_review: '采购文件专业审查', validate_evidence: '独立证据校验', final_report: '生成审查结果',
  }
  if (task.status === 'reviewing') return labels[task.progress_step ?? ''] || '采购文件专业审查'
  return ''
}
function stopProgressPolling() {
  if (progressTimer !== undefined) window.clearInterval(progressTimer)
  progressTimer = undefined
}
function syncProgressPolling() {
  if (!tasks.value.some(isActiveTask)) {
    stopProgressPolling()
  } else if (progressTimer === undefined) {
    progressTimer = window.setInterval(() => { void refreshTasks() }, 2000)
  }
}

async function load() {
  stopProgressPolling()
  loading.value = true; error.value = ''
  try {
    project.value = await getProject(projectId.value)
    tasks.value = await listTasks(projectId.value)
  } catch (reason) { error.value = apiErrorMessage(reason) }
  finally { loading.value = false; syncProgressPolling() }
}

async function refreshTasks() {
  if (refreshInFlight) return
  refreshInFlight = true
  try {
    tasks.value = await listTasks(projectId.value)
    error.value = ''
  } catch (reason) { error.value = apiErrorMessage(reason) }
  finally { refreshInFlight = false; syncProgressPolling() }
}

function onFile(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0]
  uploadError.value = ''
  if (!selected) return
  if (!/\.(doc|docx|pdf)$/i.test(selected.name)) {
    uploadError.value = '仅支持 .doc、.docx、.pdf 文件。'
    return
  }
  file.value = selected
}

async function submit() {
  if (!file.value) { uploadError.value = '请上传一个采购文件。'; return }
  pending.value = true
  try {
    const task = await createTask(projectId.value, title.value || `${project.value?.name}采购文件审核`, file.value)
    showCreate.value = false
    await refreshTasks()
  } catch (reason) { uploadError.value = apiErrorMessage(reason) }
  finally { pending.value = false }
}

function isRetrying(taskId: string) { return retryingTaskIds.value.has(taskId) }

async function retryFailedTask(task: ReviewTask) {
  if (task.task_role !== 'operator') {
    taskErrors.value = { ...taskErrors.value, [task.id]: retryPermissionMessage }
    return
  }
  if (isRetrying(task.id)) return
  retryingTaskIds.value = new Set(retryingTaskIds.value).add(task.id)
  const nextErrors = { ...taskErrors.value }
  delete nextErrors[task.id]
  taskErrors.value = nextErrors
  try {
    await retryTask(projectId.value, task.id)
    await refreshTasks()
  } catch (reason) {
    taskErrors.value = { ...taskErrors.value, [task.id]: apiErrorMessage(reason) }
  } finally {
    const nextRetrying = new Set(retryingTaskIds.value)
    nextRetrying.delete(task.id)
    retryingTaskIds.value = nextRetrying
  }
}

function taskAction(task: ReviewTask) {
  router.push({ name: 'procurement-workbench', params: { projectId: projectId.value, taskId: task.id } })
}

async function onRectificationFile(task: ReviewTask, event: Event) {
  const input = event.target as HTMLInputElement
  const selected = input.files?.[0]
  input.value = ''
  if (!selected || rectifyingTaskIds.value.has(task.id)) return
  rectifyingTaskIds.value = new Set(rectifyingTaskIds.value).add(task.id)
  try { await uploadRectification(projectId.value, task.id, selected); await refreshTasks() }
  catch (reason) { taskErrors.value = { ...taskErrors.value, [task.id]: apiErrorMessage(reason) } }
  finally { const next = new Set(rectifyingTaskIds.value); next.delete(task.id); rectifyingTaskIds.value = next }
}

async function lockTaskFinal(task: ReviewTask) {
  try { await lockFinal(projectId.value, task.id); await refreshTasks() }
  catch (reason) { taskErrors.value = { ...taskErrors.value, [task.id]: apiErrorMessage(reason) } }
}

function statusClass(status: string) {
  if (status === 'completed' || status === 'final_locked' || status === 'cancelled') return 'done'
  if (status === 'failed') return 'done'
  if (status === 'queued' || status === 'parsing' || status === 'reviewing') return 'doing'
  return 'todo'
}

onMounted(() => { void load() })
watch(() => route.params.projectId, () => { void load() })
onUnmounted(stopProgressPolling)
</script>

<template>
  <div class="page-head">
    <div class="crumb">
      <a href="#" @click.prevent="router.push('/projects')">审查任务</a>
      / {{ project?.project_code ?? '项目详情' }}
    </div>
    <div class="page-title-row">
      <h2>{{ project?.name ?? '项目不存在' }}</h2>
    </div>
    <p>{{ project?.project_code }} · {{ project?.handling_department }} · 项目负责人：{{ project?.project_owner }}</p>
  </div>

  <div class="page-body">
    <div v-if="loading" class="state-card">正在载入项目…</div>

    <template v-else-if="project">
      <!-- Toolbar -->
      <div class="toolbar">
        <button class="link-btn" @click="router.push('/projects')">&larr; 返回项目列表</button>
        <span class="sp"></span>
        <span class="pill doing">进行中</span>
        <button class="btn pri" @click="startCreate">+ 新建审查任务</button>
      </div>
      <div v-if="tasks.length" class="project-task-card">
        <div class="project-task-head">
          <div>
            <div class="project-task-title">子任务列表</div>
            <div class="project-task-meta">{{ project.project_code }} · {{ project.project_owner }} · 更新 {{ project.updated_at?.slice(0, 10) ?? '—' }}</div>
          </div>
          <div class="project-progress">
            <b>{{ tasks.filter(t => t.status === 'completed').length }} / {{ tasks.length }}</b>
            <span>当前完成进度</span>
          </div>
        </div>

        <div class="subtask-list subtask-list-page">
          <div class="subtask-table-head">
            <span>子任务</span>
            <span>状态</span>
            <span>风险 / 结果</span>
            <span>更新时间</span>
            <span>操作</span>
          </div>

          <div
            v-for="task in tasks"
            :key="task.id"
            class="subtask-row"
            :class="task.status === 'failed' ? 'task-rectify' : 'task-review'"
          >
            <div>
              <div class="subtask-name">{{ task.title }}</div>
              <div class="project-task-meta">{{ task.document?.file_name ?? '尚未上传文件' }} · {{ task.created_at?.slice(0, 10) }}</div>
            </div>
            <div class="subtask-status-cell">
              <span class="pill" :class="statusClass(task.status)">{{ statusLabel[task.status] ?? task.status }}</span>
              <div v-if="isActiveTask(task)" class="subtask-progress running">
                <i :style="{ width: `${taskProgress(task)}%` }"></i>
              </div>
              <div v-if="isActiveTask(task)" class="subtask-progress-meta">
                <span>{{ progressStage(task) }}</span><b>{{ taskProgress(task) }}%</b>
              </div>
            </div>
            <div class="subtask-result">
              <template v-if="task.status === 'failed'"><strong>{{ task.error?.slice(0, 40) || '处理失败' }}</strong></template>
              <template v-else-if="task.status === 'completed'">审查完成</template>
              <template v-else>—</template>
              <small v-if="taskErrors[task.id]" class="subtask-retry-error">{{ taskErrors[task.id] }}</small>
            </div>
            <div class="subtask-result">{{ task.updated_at?.slice(0, 10) ?? task.created_at?.slice(0, 10) ?? '—' }}</div>
            <div class="task-row-actions">
              <button
                class="btn"
                :class="{ pri: task.status !== 'completed' && !isActiveTask(task) }"
                :disabled="isActiveTask(task) || isRetrying(task.id)"
                :title="task.status === 'failed' && task.task_role !== 'operator' ? retryPermissionMessage : undefined"
                @click="task.status === 'failed' ? retryFailedTask(task) : taskAction(task)"
              >
                <template v-if="isActiveTask(task)">{{ statusLabel[task.status] || '处理中' }}</template>
                <template v-else-if="task.status === 'completed'">查看</template>
                <template v-else-if="task.status === 'failed'">{{ isRetrying(task.id) ? '正在重试…' : '重新处理' }}</template>
                <template v-else>进入</template>
              </button>
              <label v-if="task.status === 'completed' && task.task_role === 'operator' && !task.final_baseline" class="btn">
                {{ rectifyingTaskIds.has(task.id) ? '上传中…' : '上传整改版' }}
                <input type="file" accept=".doc,.docx,.pdf" hidden :disabled="rectifyingTaskIds.has(task.id)" @change="onRectificationFile(task, $event)" />
              </label>
              <button v-if="task.status === 'completed' && task.task_role === 'primary_supervisor' && !task.final_baseline" class="btn pri" @click="lockTaskFinal(task)">锁定终版</button>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="empty" style="min-height:300px;display:flex;flex-direction:column;align-items:center;justify-content:center;margin-top:0">
        <div style="font:18px var(--serif);color:var(--ink)">项目下暂无审查任务</div>
        <div style="margin-top:6px;color:var(--stone);font-size:12px;text-align:center">创建项目不会上传文件，请在这里创建具体审查任务。</div>
      </div>

      <div class="future-tasks">
        <span>响应文件审核 · 暂未开放</span>
        <span>合同审核 · 暂未开放</span>
        <span>整改核销 · 已接入任务版本链</span>
      </div>
    </template>

    <div v-else class="state-card error-state">资源不存在或您无权查看。</div>
  </div>

  <!-- Create Task Modal — Step 1: choose type -->
  <BaseModal v-if="showCreate && createStep === 1" title="新建审查任务" @close="showCreate = false">
    <div class="stepper" style="margin-bottom:16px">
      <div class="step-dot on">1 · 选择审查类型</div>
      <div class="step-dot">2 · 上传文件</div>
    </div>
    <div class="task-type-grid">
      <button class="task-type-option" @click="selectType('single')">
        <b>采购文件合规性审查</b>
        <span>上传采购文件，检查制度、条款和附件的合规性。</span>
        <em>单文件审查</em>
      </button>
      <button class="task-type-option locked" disabled>
        <b>响应文件完整性审查</b>
        <span>需先完成本项目采购文件审查并确认终版。</span>
        <em>暂不可创建</em>
      </button>
      <button class="task-type-option locked" disabled>
        <b>合同一致性审查</b>
        <span>审查合同与采购要求、响应承诺是否一致。</span>
        <em>暂不可创建</em>
      </button>
    </div>
    <div class="modal-foot" style="margin-top:14px">
      <button type="button" class="btn" @click="showCreate = false">取消</button>
    </div>
  </BaseModal>

  <!-- Create Task Modal — Step 2: upload file -->
  <BaseModal v-if="showCreate && createStep === 2" title="新建审查任务" @close="showCreate = false">
    <div class="stepper" style="margin-bottom:16px">
      <div class="step-dot done">1 · 选择审查类型</div>
      <div class="step-dot on">2 · 上传文件</div>
    </div>
    <form @submit.prevent="submit">
      <div class="note" style="margin:0 0 14px"><b>已选择：</b>采购文件合规性审查 — 上传采购文件后系统将自动解析并生成 AI 审查结论。</div>
      <div class="field" style="margin-bottom:12px">
        <label>任务名称</label>
        <input v-model="title" placeholder="默认使用项目名称" />
      </div>
      <div class="field" style="margin-bottom:12px">
        <label>采购文件</label>
        <label class="file-picker" :class="{ selected: file }">
          <input type="file" accept=".doc,.docx,.pdf" required @change="onFile" />
          <span class="file-icon" aria-hidden="true">↥</span>
          <span class="file-copy"><b>{{ file ? file.name : '选择采购文件' }}</b><small>{{ file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击重新选择` : '点击浏览本地文件，或将文件拖放到此处' }}</small></span>
          <em>{{ file ? '已选择' : '浏览文件' }}</em>
        </label>
        <span class="file-hint">仅上传 1 个文件 · 支持 DOC、DOCX、PDF</span>
      </div>
      <p v-if="uploadError" style="color:var(--crimson);font-size:12px;margin:0 0 12px">{{ uploadError }}</p>
      <div class="modal-foot">
        <button type="button" class="btn" @click="backToStep1">上一步</button>
        <button class="btn pri" :disabled="pending">{{ pending ? '提交中…' : '上传并开始解析' }}</button>
      </div>
    </form>
  </BaseModal>
</template>
