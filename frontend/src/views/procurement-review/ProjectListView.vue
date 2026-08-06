<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import BaseModal from '../../components/base/BaseModal.vue'
import { apiErrorMessage } from '../../api'
import { createProject, listProjects } from '../../api/procurement-review'
import type { Project } from '../../types/procurement-review'

const router = useRouter()
const projects = ref<Project[]>([])
const loading = ref(true)
const showCreate = ref(false)
const error = ref('')
const pending = ref(false)
const form = ref({ name: '', project_code: '', handling_department: '采购业务部', project_owner: '' })

async function load() {
  loading.value = true; error.value = ''
  try { projects.value = await listProjects() }
  catch (reason) { error.value = apiErrorMessage(reason) }
  finally { loading.value = false }
}

async function submit() {
  pending.value = true
  try {
    const project = await createProject(form.value)
    showCreate.value = false
    router.push({ name: 'procurement-project', params: { projectId: project.id } })
  } catch (reason) { error.value = apiErrorMessage(reason) }
  finally { pending.value = false }
}

onMounted(load)
</script>

<template>
  <!-- Page Head -->
  <div class="page-head">
    <div class="crumb">AI 一期 · 任务中心</div>
    <div class="page-title-row">
      <h2>审查任务</h2>
    </div>
    <p>以采购项目为单位查看和管理审查任务。进入项目后可查看该项目下的全部子任务。</p>
  </div>

  <!-- Page Body -->
  <div class="page-body">
    <p v-if="loading" class="state-card">正在加载项目…</p>
    <div v-else-if="error" class="state-card error-state">
      {{ error }}
      <button class="btn" style="margin-left:12px" @click="load">重试</button>
    </div>
    <div v-else-if="!projects.length" class="empty" style="min-height:420px;display:flex;flex-direction:column;align-items:center;justify-content:center">
      <span style="font-size:38px;opacity:.4">📋</span>
      <div style="margin-top:16px;font:20px var(--serif);color:var(--ink)">暂无采购项目</div>
      <div style="max-width:460px;margin-top:8px;color:var(--stone);font-size:12px;line-height:1.8;text-align:center">先建立项目，再在项目内发起采购文件审查子任务。</div>
      <button class="btn pri" style="margin-top:20px" @click="showCreate = true">+ 新建项目</button>
    </div>

    <div v-else>
      <div class="toolbar">
        <span style="font-size:11.5px;color:var(--olive)">共 {{ projects.length }} 个采购项目</span>
        <span class="sp"></span>
        <button class="btn pri" @click="showCreate = true">+ 新建项目</button>
      </div>

      <div class="project-list">
        <div
          v-for="project in projects"
          :key="project.id"
          class="project-list-row"
          @click="router.push({ name: 'procurement-project', params: { projectId: project.id } })"
        >
          <div>
            <div class="project-task-title">{{ project.name }}</div>
            <div class="project-task-meta">{{ project.project_code }} · {{ project.project_owner }}</div>
          </div>
          <div class="project-list-cell">
            <b>进行中</b>
            <span>当前项目状态</span>
          </div>
          <div class="project-list-cell">
            <b>—</b>
            <span>子任务已完成</span>
          </div>
          <button
            class="btn pri"
            @click.stop="router.push({ name: 'procurement-project', params: { projectId: project.id } })"
          >
            进入项目
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- Create Modal -->
  <BaseModal v-if="showCreate" title="新建采购项目" @close="showCreate = false">
    <form class="form-grid" @submit.prevent="submit">
      <div class="field full"><label>项目名称</label><input v-model.trim="form.name" required /></div>
      <div class="field"><label>项目编号</label><input v-model.trim="form.project_code" required /></div>
      <div class="field"><label>经办部门</label><input v-model.trim="form.handling_department" required /></div>
      <div class="field"><label>项目负责人</label><input v-model.trim="form.project_owner" required /></div>
      <div class="modal-foot">
        <button type="button" class="btn" @click="showCreate = false">取消</button>
        <button class="btn pri" :disabled="pending">{{ pending ? '创建中…' : '创建项目' }}</button>
      </div>
    </form>
  </BaseModal>
</template>
