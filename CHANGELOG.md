# Changelog

## 0.3.5 — 2026-11-14

按第三轮复查（8.3 / 10）补的三处 + 新问题：

- **`gh` 白名单只剩 `gh` / `gh.exe`**：`resolve_gh_executable` 不再接受 `.bat` / `.cmd` 或任意路径；Windows 下 `.bat` 会被 `cmd.exe` 接管，关键词里的 `&`、`%` 可能被解释。配置与直调两条路径现在用同一份白名单。
- **登录检查不再吃光总预算**：`auth_ok` 只拿 `min(10s, 总预算 × 40%)`，剩余时间留给关键词；auth 超时/失败统一返回 `code=2`，并明确写「`gh auth status` 用完了总时限」，不再把超时误报成 `code=3` 的「gh 坏了」。
- **脱敏补齐**：`parse_search_output` 的坏 JSON 片段、`verify_localized` 的 stdout、`canonical_hash` 的 stdout 都过 `redact_text`；正则新增 `Bearer`、`api_key`、`access_token`、`Authorization`。
- **直调参数夹取**：`stars` 夹到 0..1,000,000，`limit` 夹到 1..10，`updated_days` 夹到 1..3650；`updated_since` 必须是 `YYYY-MM-DD`，非法时回退最近 N 天并写进 `note`。
- **`pushedAt` 非字符串**：`activity_points` 先判 `isinstance(str)`，并捕获 `AttributeError`，数字/列表不再让 `.replace` 崩。
- **Windows 只有 `py` 启动器**：`resolve_python` 按 `sys.executable` → `python` → `python3` → `py` 的顺序找解释器，`canonical_hash` / `verify_localized` 共用。
- **随包 CLI**：同一套参数校验（`--stars` / `--limit` / `--updated-since`）与脱敏；主仓 `scripts/repo_scan.py` 同步。
- 测试：66 passed（离线 54 + SDK 桩 9 + 市场 smoke 3），`ruff check .` 全绿。
- 规则本体、契约哈希 `fa0f991f…bc93`、四段提示词 sha256 未动。

## 0.3.4 — 2026-11-14

按第二轮第三方复查（8 条问题 + 3 条优先项）修的加固：

- **异常不漏**：`gh_json` / `auth_ok` / `canonical_hash` / `verify_localized` 统一捕获 `OSError`（含 `PermissionError`），`_scan_one` 捕获 `OSError` + `RuntimeError`；子进程启动失败不再穿透到工具层。
- **`scan_timeout` 真正生效**：整次 `rbb_scan` 共享一份总预算（登录检查 + 所有关键词），每个关键词只拿到剩余时间；哈希/校验也走同一个超时参数，不再写死 30/60 秒。
- **`gh_binary` 收敛**：配置只允许 `gh` / `gh.exe`，其它值回退 `gh` 并在 `settings.warning` 说明；库调用仍可注入文件名必须是 gh 系列的测试替身，非法名字直接按「gh 不可用」处理。
- **`extra` 白名单**：`verify_localized` 只接受 `--quiet`，`--canonical` 等可跳过声明检查的开关不能由模型侧注入。
- **结论词边界**：`check_deliverable` 的 ASCII 结论词改整词匹配，`rebuild` / `building` 不再误命中 `Build`。
- **去重键**：`url` 与 `full_name` 都空的行直接丢弃，不再把互不相关的仓合并成一条。
- **无时区时间**：`activity_points` 把 `now - pushed` 一起纳入 `try`，`naive` 时间抛 `TypeError` 时返回 0 而不是崩。
- **stderr 脱敏**：回给模型的 `reason` 经 `redact_text()` 去掉 token/口令与本机绝对路径，并截断。
- **坏 JSON**：`parse_search_output` 只保留 dict 行，非 dict 输出不再让 `row.get` 崩。
- 测试：新增 OSError 穿透、总预算、非法 `gh_binary`、`extra` 白名单、`rebuild` 词边界、空键去重、无时区时间、stderr 脱敏、非 dict JSON 的离线回归；59 passed，ruff check 全绿。
- 规则本体、契约哈希 `fa0f991f…bc93`、四段提示词 sha256 未动。

## 0.3.3 — 2026-11-14

- `short_description` 压到市场展示字段 255 字上限（当前 112 字），避免「编辑展示信息」保存时被 `String should have at most 255 characters` 拒绝。
- README 移除 v0.1.1 历史验证段与已过期的「市场登记未完成」说明，改为通用发布流程；历史继续留在本文件。
- 规则本体、契约哈希、测试数（49 passed）均未变。

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
