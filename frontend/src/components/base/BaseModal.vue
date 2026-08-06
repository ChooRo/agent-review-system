<script setup lang="ts">
defineProps<{ title: string }>()
defineEmits<{ close: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <section class="modal" role="dialog" aria-modal="true" :aria-label="title">
      <header>
        <h2>{{ title }}</h2>
        <button type="button" class="icon-button" aria-label="关闭" @click="$emit('close')">&times;</button>
      </header>
      <slot />
    </section>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(20, 20, 19, .55);
  z-index: 100; display: flex; align-items: center; justify-content: center; padding: 24px;
}
.modal {
  width: min(620px, 96vw); max-height: 88vh; overflow: auto;
  background: var(--white, #fff); border-radius: 16px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, .28);
}
header {
  padding: 17px 20px; border-bottom: 1px solid var(--border, #e8e6dc);
  display: flex; align-items: center; justify-content: space-between;
}
header h2 { font-size: 17px; margin: 0; }
.icon-button {
  border: 0; background: none; font-size: 21px; color: var(--stone, #87867f);
  cursor: pointer; padding: 0; line-height: 1;
}
</style>
