# Colab / 无 GUI 环境

本项目已有 CLI，**不必另写注册脚本**。

```bash
cd grokRegister-cpa
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json：邮箱、proxy、cpa_*、chenyme_* 等

python grok_register_ttk.py cli
# 提示后输入: start
```

## 注意

- `register_mode=protocol` 仍需本机 Chrome/Chromium 做 Turnstile mint（屏外）。
- Colab 无代理时可能被 Cloudflare 拦截；需要可用出口或代理。
- 协议批量：`register_count` / `register_workers` 见 README。

## chenyme / Build 导入（可选）

```json
{
  "chenyme_grok2api_enabled": true,
  "chenyme_grok2api_base": "https://your-grok2api",
  "chenyme_grok2api_username": "admin",
  "chenyme_grok2api_password": "...",
  "g2a_build_import_file_enabled": true,
  "g2a_build_import_file": "grok2api_build_import.json",
  "g2a_build_remote_import_enabled": true,
  "cpa_auto_add": true,
  "cpa_auth_dir": "./auth"
}
```

- SSO → `web/import`（**不** convert）
- CPA Device Flow 成功后 → 本地 Build JSON + 可选 `accounts/import`（multipart `file`，每号一条）
