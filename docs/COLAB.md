# Colab / 无 GUI 环境

本项目已有 CLI，**不必另写注册逻辑**。推荐用 notebook 一键跑。

## 方式 A：Notebook（推荐）

仓库内：[`colab/grokRegister_cpa_colab.ipynb`](../colab/grokRegister_cpa_colab.ipynb)

1. 打开 [Google Colab](https://colab.research.google.com/)
2. **文件 → 上传笔记本**，选 `colab/grokRegister_cpa_colab.ipynb`（不必云盘）
3. 或：**文件 → 打开笔记本 → GitHub** → 填你的仓库 → 选该文件
4. 按单元格从上到下运行：装 Chrome/Xvfb → clone → 上传/写 `config.json` → `cli start` → 下载结果

启动命令等价于：

```bash
DISPLAY=:99 python grok_register_ttk.py cli start
```

（notebook 会先起 Xvfb；CLI 的 `start` 会跳过交互输入。）

## 方式 B：纯命令

```bash
%cd /content
!rm -rf /content/grokRegister-cpa
!git clone --depth 1 -b main https://github.com/你的用户名/grokRegister-cpa.git /content/grokRegister-cpa
%cd /content/grokRegister-cpa
!pip install -q -r requirements.txt
# 上传或写入 config.json 后：
!Xvfb :99 -screen 0 1280x900x24 -ac >/tmp/xvfb.log 2>&1 &
!DISPLAY=:99 python grok_register_ttk.py cli start
```

本地/服务器（有显示时）：

```bash
cd grokRegister-cpa
pip install -r requirements.txt
# 编辑 config.json
python grok_register_ttk.py cli start
```

## 注意

- `register_mode=protocol` 仍需 Chrome 做 Turnstile mint（屏外 / 虚拟显示）
- Colab **无代理**时可能被 Cloudflare 拦截（`Attention Required`）；可换 Runtime 或配置 `proxy`
- 协议批量：`register_count` / `register_workers` 见 README

## chenyme / Build 导入（可选）

```json
{
  "chenyme_grok2api_enabled": true,
  "chenyme_grok2api_base": "https://your-grok2api",
  "chenyme_grok2api_username": "admin",
  "chenyme_grok2api_password": "...",
  "g2a_build_import_file_enabled": true,
  "g2a_build_import_file": "exports/grok2api_build_import.json",
  "g2a_build_remote_import_enabled": true,
  "cpa_auto_add": true,
  "cpa_auth_dir": "./auth"
}
```

- SSO → `web/import`（**不** convert）
- CPA Device Flow 成功后 → `exports/grok2api_build_import.json` + 可选 `accounts/import`（multipart `file`，每号一条）

## 下载产物

常见路径：

- `accounts_*.txt`
- `exports/grok2api_build_import.json`
- `auth/xai-*.json`
- `log/`
