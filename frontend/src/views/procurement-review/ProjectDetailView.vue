<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseModal from '../../components/base/BaseModal.vue'
import { apiErrorMessage } from '../../api'
import { createTask, getProject, listTasks } from '../../api/procurement-review'
import type { Project, ReviewTask } from '../../types/procurement-review'

const route = useRoute()
const router = useRouter()
const project = ref<Project>()
const tasks = ref<ReviewTask[]>([])
const loading = ref(true)
const showCreate = ref(false)
const title = ref('')
const file = ref<File>()
const uploadError = ref('')
const pending = ref(false)
const projectId = computed(() => String(route.params.projectId))
const error = ref('')

const statusLabel: Record<string, string> = {
  draft: '草稿', parsing: '解析中', reviewing: 'AI 审查中',
  operator_review: '待经办处理', primary_review: '待主责复核',
  primary_recheck: '待主责再次复核', completed: '已完成',
  failed: '处理失败', cancelled: '已取消', queued: '排队中',
}

async function load() {
  loading.value = true; error.value = ''
  try {
    project.value = await getProject(projectId.value)
    tasks.value = await listTasks(projectId.value)
  } catch (reason) { error.value = apiErrorMessage(reason) }
  finally { loading.value = false }
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
    router.push({ name: 'procurement-progress', params: { projectId: projectId.value, taskId: task.id } })
  } catch (reason) { uploadError.value = apiErrorMessage(reason) }
  finally { pending.value = false }
}

function taskAction(task: ReviewTask) {
  if (task.status === 'parsing' || task.status === 'reviewing') {
    router.push({ name: 'procurement-progress', params: { projectId: projectId.value, taskId: task.id } })
  } else {
    router.push({ name: 'procurement-workbench', params: { projectId: projectId.value, taskId: task.id } })
  }
}

function statusClass(status: string) {
  if (status === 'completed' || status === 'cancelled') return 'done'
  if (status === 'parsing' || status === 'reviewing') return 'doing'
  return 'todo'
}

onMounted(load)
</script>

<template>
  <div class="page-head">
    <div class="crumb">
      <a href="#" @click.prevent="router.push('/projects')">审查任务</a>
      / {{ project?.project_code ?? '项目详情' }}
    </div>
    <div class="page-title-row">
      <h2>{{ project?.name ?? '项目不存在' }}</h2>
      <div class="page-title-actions">
        <button class="btn pri" :disabled="!project" @click="showCreate = true">新建采购文件审查</button>
      </div>
    </div>
    <p>{{ project?.project_code }} · {{ project?.handling_department }} · 项目负责人：{{ project?.project_owner }}</p>
  </div>

  <div class="page-body">
    <div v-if="loading" class="state-card">正在载入项目…</div>

    <template v-else-if="project">
      <div v-if="tasks.length" class="project-task-card">
        <div class="project-task-head">
          <div>
            <div class="project-task-title">子任务列表</div>
            <div class="project-task-meta">{{ project.project_code }} · {{ project.project_owner }}</div>
          </div>
          <div class="project-progress">
            <b>{{ tasks.filter(t => t.status === 'completed').length }} / {{ tasks.length }}</b>
            <span>当前完成进度</span>
          </div>
        </div>

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
        >
          <div>
            <div class="subtask-name">{{ task.title }}</div>
            <div class="project-task-meta">{{ task.document?.file_name ?? '尚未上传文件' }} · {{ task.created_at?.slice(0, 10) }}</div>
          </div>
          <div>
            <span class="pill" :class="statusClass(task.status)">{{ statusLabel[task.status] ?? task.status }}</span>
          </div>
          <div class="subtask-result">{{ task.status === 'completed' ? '审查完成' : '—' }}</div>
          <div class="subtask-result">{{ task.created_at?.slice(0, 10) ?? '—' }}</div>
          <div class="task-row-actions">
            <button class="btn" @click="taskAction(task)">查看</button>
          </div>
        </div>
      </div>

      <div v-else class="empty" style="min-height:300px;display:flex;flex-direction:column;align-items:center;justify-content:center;margin-top:0">
        <span style="font-size:32px;opacity:.4">📄</span>
        <div style="margin-top:12px;font:18px var(--serif);color:var(--ink)">项目下暂无审查任务</div>
        <div style="margin-top:6px;color:var(--stone);font-size:12px;text-align:center">创建项目不会上传文件，请在这里创建具体审查任务。</div>
      </div>

      <div class="future-tasks">
        <span>响应文件审核 · 暂未开放</span>
        <span>合同审核 · 暂未开放</span>
        <span>整改核销 · 暂未开放</span>
      </div>
    </template>

    <div v-else class="state-card error-state">资源不存在或您无权查看。</div>
  </div>

  <!-- Create Task Modal -->
  <BaseModal v-if="showCreate" title="新建采购文件审查任务" @close="showCreate = false">
    <form class="form-grid" @submit.prevent="submit">
      <div class="field full"><label>任务名称</label><input v-model="title" placeholder="默认使用项目名称" /></div>
      <div class="field full">
        <label>采购文件</label>
        <input type="file" accept=".doc,.docx,.pdf" required @change="onFile" />
        <span style="font-size:10px;color:var(--stone);margin-top:4px">仅上传一个文件，支持 DOC、DOCX、PDF。</span>
      </div>
      <p v-if="uploadError" style="color:var(--crimson);font-size:12px;margin:0">{{ uploadError }}</p>
      <div class="modal-foot" style="grid-column:1/-1;padding:0;border:0">
        <button type="button" class="btn" @click="showCreate = false">取消</button>
        <button class="btn pri" :disabled="pending">{{ pending ? '提交中…' : '上传并开始解析' }}</button>
      </div>
    </form>
  </BaseModal>
</template>
