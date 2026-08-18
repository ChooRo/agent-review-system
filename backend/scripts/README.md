# 数据清理脚本

所有删除脚本默认先预览；只有增加 `--confirm` 才会执行。执行前会把 JSON 数据或法规目录备份到 `backend/data/backups/`。

```powershell
cd backend

# 查看项目 / 删除项目（可重复传入 --project-id）
python scripts/delete_projects.py --list
python scripts/delete_projects.py --project-id <项目ID>
python scripts/delete_projects.py --project-id <项目ID> --confirm

# 查看任务 / 删除任务（会级联删除 findings、comments、events、audit、idempotency）
python scripts/delete_tasks.py --list
python scripts/delete_tasks.py --task-id <任务ID>
python scripts/delete_tasks.py --task-id <任务ID> --confirm

# 查看已上传法规 / 删除法规
python scripts/delete_legal_documents.py --list
python scripts/delete_legal_documents.py --document-key <法规key>
python scripts/delete_legal_documents.py --document-key <法规key> --confirm
```

正在运行的项目任务或任务默认拒绝删除；仅测试数据可使用 `--force`。

备份目录名分别以 `delete_projects_`、`delete_tasks_`、`delete_legal_` 开头。恢复时停止后端服务，将备份中的 JSON 或法规目录移回原位置即可。
