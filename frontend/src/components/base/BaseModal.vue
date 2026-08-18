<script setup lang="ts">
withDefaults(defineProps<{ title: string; closable?: boolean; wide?: boolean }>(), { closable: true, wide: false })
defineEmits<{ close: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="closable && $emit('close')">
    <section class="modal" :class="{ wide }" role="dialog" aria-modal="true" :aria-label="title">
      <header>
        <h2>{{ title }}</h2>
        <button v-if="closable" type="button" class="icon-button" aria-label="关闭" @click="$emit('close')">&times;</button>
      </header>
      <div class="modal-body">
        <slot />
      </div>
    </section>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(20, 20, 19, .55);
  z-index: 100; display: flex; align-items: center; justify-content: center; padding: 24px;
}
.modal {
  width: min(620px, 96vw); max-height: min(88vh, 900px); overflow: hidden;
  display:flex; flex-direction:column;
  background: var(--white, #fff); border:1px solid rgba(255,255,255,.45); border-radius: 16px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, .28);
}
.modal.wide { width: min(920px, 96vw); }
header {
  flex:none; padding: 17px 20px; border-bottom: 1px solid var(--border, #e8e6dc);
  display: flex; align-items: center; justify-content: space-between;
  background:var(--white,#fff); color:var(--ink,#26322d);
}
header h2 { font-size: 17px; margin: 0; }
.icon-button {
  border: 0; background: none; font-size: 21px; color: var(--stone, #87867f);
  cursor: pointer; padding: 0; line-height: 1;
}
.modal-body { min-height:0; overflow:auto; padding: 20px; scrollbar-gutter:stable; }
.modal-body::-webkit-scrollbar{width:8px}.modal-body::-webkit-scrollbar-thumb{border:2px solid transparent;border-radius:10px;background:#b9b7b0;background-clip:padding-box}
</style>
