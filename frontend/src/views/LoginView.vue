<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const username = ref('operator')
const password = ref('ChangeMe123!')
const error = ref('')
const pending = ref(false)
const auth = useAuthStore()
const router = useRouter()

async function submit() {
  pending.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/projects')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '登录失败'
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-intro">
      <span class="eyebrow">XIAMEN TOBACCO · AI REVIEW</span>
      <h1>让每一条采购要求<br />都留下可核验的依据</h1>
      <p>当前阶段仅开放采购文件单文件审核。AI 提供候选问题与证据，正式结论仍由业务人员确认。</p>
    </section>
    <form class="login-card" @submit.prevent="submit">
      <span class="seal">智审</span>
      <h2>登录开发环境</h2>
      <label>用户名<input v-model="username" autocomplete="username" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" /></label>
      <p v-if="error" class="form-error">{{ error }}</p>
      <button class="primary-button" :disabled="pending">{{ pending ? '登录中…' : '进入系统' }}</button>
      <small>初始账号：operator / supervisor / admin</small>
    </form>
  </main>
</template>
