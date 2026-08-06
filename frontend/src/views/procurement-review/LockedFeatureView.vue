<script setup lang="ts">
defineProps<{ title: string; detail: string }>()
</script>

<template>
  <div class="page-head">
    <div class="crumb">平台能力 / 暂未开放</div>
    <h2>{{ title }}</h2>
    <p>{{ detail }}</p>
  </div>

  <div class="page-body">
    <div class="locked-feature">
      <div class="lf-ic">⌁</div>
      <div class="lf-t">{{ title }}暂未开放</div>
      <div class="lf-s">当前一期仅开放采购文件单文件审核。此入口保留用于后续平滑接入，不会创建任务或调用业务接口。</div>
    </div>
  </div>
</template>
