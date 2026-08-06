import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '../services/http'
import type { LoginResponse, User } from '../types/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)

  async function login(username: string, password: string) {
    const result = await api<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('access_token', result.access_token)
    user.value = result.user
  }

  async function restore() {
    if (!localStorage.getItem('access_token')) return false
    try {
      user.value = await api<User>('/auth/me')
      return true
    } catch {
      logout()
      return false
    }
  }

  function logout() {
    localStorage.removeItem('access_token')
    user.value = null
  }

  return { user, login, restore, logout }
})
