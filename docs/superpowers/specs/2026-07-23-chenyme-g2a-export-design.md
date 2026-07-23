# grokRegister-cpa：chenyme SSO + grok2api Build 导入（无 convert）

**日期:** 2026-07-23  
**状态:** 已批准，实施中  
**原则:** 不动核心注册 / Device Flow / CPA 语义；仅后处理可开关。

## 功能

1. chenyme Web SSO 自动导入：`POST /api/admin/v1/accounts/web/import`（multipart 纯 SSO 行），**永不** convert-to-build  
2. 本地累加 `grok2api_build_import.json`（CPA token → Build 条目）  
3. 远程 Build 导入：`POST /api/admin/v1/accounts/import`，multipart 字段名 `file`，**每号一文件**（单 account）  
4. Colab：文档说明用现有 CLI，不重写脚本  

## 配置

```json
{
  "chenyme_grok2api_enabled": false,
  "chenyme_grok2api_base": "",
  "chenyme_grok2api_username": "",
  "chenyme_grok2api_password": "",
  "g2a_build_import_file_enabled": false,
  "g2a_build_import_file": "exports/grok2api_build_import.json",
  "g2a_build_remote_import_enabled": false
}
```

## Build 导出行（最小 + 可选）

必填倾向：`provider=grok_build`, `name/email`, `client_id=b1a00492-...`（OAuth 常量或 JWT claim）, `access_token`, `refresh_token`, `id_token`, `token_type`, `expires_at`/`expires_in`  
可选：`user_id`/`principal_id`（JWT sub）  
不写：`team_id`

## 挂钩

`add_sso_to_cpa` 成功得到 record 后：本地累加 + 可选远程 import；  
另：有 SSO 时可选 chenyme web/import。失败只日志。
