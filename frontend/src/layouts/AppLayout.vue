<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/base/AppIcon.vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore(); const router = useRouter(); const route = useRoute()
const nav = [
  { section: '审查中心', label: '审查任务', icon: 'tasks', to: '/projects', enabled: true },
  { section: '审查中心', label: '审查工作台', icon: 'search', to: '/projects', enabled: false },
  { section: '项目闭环', label: '整改核销', icon: 'refresh', to: '/rectification', enabled: false },
  { section: '项目闭环', label: '项目档案', icon: 'folder', to: '/projects', enabled: false },
  { section: '知识与治理', label: '知识库', icon: 'book', to: '/knowledge', enabled: false },
  { section: '知识与治理', label: '经验案例库', icon: 'shield', to: '/cases', enabled: false },
  { section: '知识与治理', label: '强制纠偏', icon: 'wrench', to: '/corrections', enabled: false },
] as const
const sections = ['审查中心', '项目闭环', '知识与治理'] as const
onMounted(async () => { if (!(await auth.restore())) router.replace('/login') })
function logout() { auth.logout(); router.push('/login') }
</script>
<template><div class="app-shell"><header class="topbar"><div class="brand-mark" aria-hidden="true">智</div><div class="brand-copy"><strong>厦门烟草采购管理智能辅助平台</strong><small>PROCUREMENT REVIEW · PHASE I</small></div><span class="phase-tag">采购文件单文件审核</span><div class="user-box"><i class="avatar">{{ auth.user?.display_name?.slice(0, 1) ?? '·' }}</i><span>{{ auth.user?.display_name ?? '正在验证身份' }}</span><small>{{ auth.user?.department }} · {{ auth.user?.roles[0]?.name ?? '身份验证中' }}</small><button type="button" @click="logout">退出</button></div></header><aside class="sidebar" aria-label="主导航"><template v-for="section in sections" :key="section"><p class="nav-title">{{ section }}</p><component :is="item.enabled ? 'RouterLink' : 'button'" v-for="item in nav.filter((entry) => entry.section === section)" :key="item.label" :to="item.enabled ? item.to : undefined" class="nav-item" :class="{ active: item.enabled && route.path.startsWith(item.to), locked: !item.enabled }" :disabled="!item.enabled"><AppIcon :name="item.icon" :size="17" /><span>{{ item.label }}</span><small v-if="!item.enabled">暂未开放</small></component></template><template v-if="auth.user?.roles.some((role) => role.code === 'admin')"><p class="nav-title">系统管理</p><button class="nav-item locked" disabled><AppIcon name="users" :size="17" /><span>用户与权限</span><small>暂未开放</small></button></template></aside><main class="main-content"><RouterView /></main></div></template>
