# Changelog

## 0.3.2 — 2026-11-14

按第三方复查（7.5/10）修的五项 + 一项文档整理：

- **`rbb_verify` 路径收敛**：`path` 必须在插件工作目录内且是 `.md`，`realpath` 穿透 `..` 与符号链接；越界返回 `rc=2`，不再能当任意文件读取入口。输出截断到 800/400 字符。
- **`gh` 参数注入**：`gh search repos` 的所有选项前置，检索串放在 `--` 之后；`-foo` 这类关键词不再被当成选项。两处 `repo_scan`（插件内核与随包 CLI）同步。
- **配置失败不再静默**：`plugin.toml` 缺失/解析失败会 `warning` 日志，`rbb_status.settings` 增加 `source` 与 `warning`，明确显示「正在用兜底默认值」；兜底会关闭 `enable_github_scan`、条数压到 1，不拿脏配置去检索。
- **默认值去重 + 布尔显式解析**：`plugin.toml [settings]` 为唯一默认值来源，`config.example.toml` 不再抄第二份；`"false"` / `"0"` / `"off"` 不再被 `bool()` 当成 True。
- **契约测试**：新增 `tests/fixtures/gh_search_repos.json`（录好的 `gh search repos --json` 输出），既直接测 JSON 形状，也用假 `gh` 真正跑一遍 `subprocess` 并校验 `--` 前后的 argv 顺序。
- **测试工程**：修复 pytest 以绝对 `tests` 路径收集时把仓库根 `__init__.py` 当模块导入导致的 47 项全错；`python -m pytest tests` 现为 49 passed。
- README 的版本修复记录迁到本文件。

## 0.3.1 — 2026-11-XX

- `check_deliverable` 不再因备选段出现 `Build` 就误报 `gate_line`；无结论行时仅在结论词唯一时套门禁。
- `prompt_digest.py` 登记表结构校验：缺 `id` / `versions` 非数组 / id 重复 / `prompts` 非数组 → `FAIL` + `rc=2`。
- `rbb_scan` 非数字参数回默认值；启动哈希任务保留引用避免 GC。
- 同步上游 v1.2.1 的 E741 清理。

## 0.3.0 — 同步上游 v1.2.0

- 粘贴型提示词 sha256 登记（`prompt-digests.json` + `scripts/prompt_digest.py`）。
- `host-adapters.md` 四段提示词加 `RBB:PROMPT` 锚点。
- 规则本体未动。

## 0.2.0 — 同步上游 v1.1.0

- 可改性硬闸（绿/黄/红，红档禁止 Join）。
- 稳定接入口 + ≤150 行薄适配层。
- 分支纪律：不直推 `main` / `master`。

## 0.1.1

- 市场首发版；官方 Verify / Release 工作流通过。
