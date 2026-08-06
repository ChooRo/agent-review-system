<script setup lang="ts">defineProps<{ title: string; detail: string }>()</script>
<template><section class="page-head"><p class="crumb">平台能力 / 暂未开放</p><h1>{{ title }}</h1><p>{{ detail }}</p></section><section class="page-body"><article class="locked-feature"><div class="locked-mark">⌁</div><h2>{{ title }}暂未开放</h2><p>当前一期仅开放采购文件单文件审核。此入口保留用于后续平滑接入，不会创建任务或调用业务接口。</p></article></section></template>
