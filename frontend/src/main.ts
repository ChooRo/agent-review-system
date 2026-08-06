import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/index.css'
import './styles/login.css'
import './styles/demo-system.css'
import './styles/workbench.css'

createApp(App).use(createPinia()).use(router).mount('#app')
