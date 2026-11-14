# Changelog

## 0.3.9 — 2026-11-14

按《rbb 补深审》逐项核对 v0.3.7 报告与当前实现，只修改 N.E.K.O 适配版：

- **交付契约补齐**：完整档兼容正典英文括号后缀、`Alternatives and risks` 与中文「我要加的东西」；必须给出可改性档位和结构化 `verdict / modifiability / lane`，红、黄档 Join 明确拒绝。Build 门禁只认首个非空行且原因不得为空；快速档支持中英 `Reused / Change / Risk`、`复用 / 改动 / 风险`。
- **默认提示不再漏硬约束**：`rbb_rules` 与流程直接抽取正典正文，包含六维评分、可改性硬闸、不可信仓库内容、分支纪律、薄适配层与逐次公开写确认；四段模板、四个 worked examples 与中英文粘贴提示词均可通过自家检查。
- **本地化校验加固**：代码内置正典与契约 SHA-256 基线，随包英文加语言标记也不能冒充翻译版；契约新增六项全局锚点，支持 `~~~` 围栏，导入模块不再改宿主 stdout/stderr 编码。
- **进程与预算加固**：Windows PATH 结果再次拒绝相对路径和当前目录，优先 `gh.exe`；gh 子进程隐藏控制台窗口；限流只在剩余预算内退避 2 秒并重试一次；找不到 Python 时不再回退裸名。
- **测试与发布工程**：新增深审负例回归；随包测试改名 `test_skill_pack.py`，修复独立收集、SDK 桩与断网问题；新增独立 Offline regression tests 工作流，官方 managed `verify.yml` / `release.yml` 保持原样。
- **元数据与文档**：版本升至 0.3.9，短描述移除更新说明，收敛宽泛触发关键词；同步 README、使用说明、中英文规则与 vendor 文档。
- 测试：160 passed（另有 20 个子测试），Ruff 全绿；核心不变块哈希 `fa0f991f…bc93` 未动。

## 0.3.8 — 2026-11-14

按复查截图第一组 `rbb（快门）` 的四项建议，只修改 N.E.K.O 适配版：

- **脱敏只剩一份实现**：新增随包 `scripts/redaction.py`，`rbb_core.py` 与 `repo_scan.py` 都按文件位置加载同一模块；URL userinfo、Cookie / Authorization 头、GitHub token、Bearer、口令与 home 路径共用同一组规则和契约测试，不再各自维护正则。
- **PATH 未命中不再退回裸 `gh`**：`command_path()` 只返回 `which_path_only()` 解析出的绝对路径；找不到时 `gh_json()` / `auth_ok()` 在启动子进程前就返回不可用，Windows 不会借机执行当前目录的 `gh.exe`。v0.3.7 已接受的合法绝对路径仍然可用。
- **版本定义去重**：`verify_localized.py` 删除旧的 `__version__ = "1.2.6"`，只保留与上游技能本体对应的 `1.2.7`；N.E.K.O 插件版本独立升为 `0.3.8`。
- **可复现 vendor 发布**：新增 `scripts/vendor_release.py`，固定 ZIP 文件顺序、时间戳、权限和压缩级别；tag 必须与 `plugin.toml` 版本一致。ZIP 内附 `VENDOR-SHA256SUMS.txt`，Release 另附覆盖插件包、vendor ZIP、校验工具与证明的 `SHA256SUMS.txt`。
- **接入文档**：新增 `docs/vendor.md`，明确只用于本 N.E.K.O 仓库，给出固定 tag、下载校验、目标目录 `plugin/plugins/reuse_before_build`、复制后复检与升级约定。
- **发布顺序**：官方 managed `release.yml` 保持原样；独立 `Vendor Source Release` 工作流通过 `workflow_run` 等待官方 Release 完整成功，再从同一发布提交构建、复检并上传附件，不影响市场发布门禁。
- **回归测试**：覆盖两个调用方脱敏一致性、PATH 未命中不启动 gh、合法绝对路径、单一版本定义、重复构建字节一致、文件篡改、额外文件、路径穿越、复制后漂移与 Release 哈希不匹配。
- 测试：100 passed（另有 4 个脱敏子测试），Ruff 全绿；规则本体与契约哈希 `fa0f991f…bc93` 未动。

## 0.3.7 — 2026-11-14

修一个会让 `rbb_scan` 在本机永远返回 `code=2` 的白名单自锁：

- **根因**：`search_candidates()` 先用 `resolve_gh_path()` 把 `gh` 解析成绝对路径，再传给 `auth_ok()` / `_scan_one()`；但 `auth_ok()` 内部重新走 `command_path()`，而当时 `command_path()` 只接受字面量 `gh` / `gh.exe`，于是把自家刚解析出的绝对路径判为非法，返回“gh_binary 非法：只允许 gh / gh.exe”。默认配置也会中招，和用户是否把 gh 装在 PATH 里无关。
- **修法**：`command_path()` 现在接受“由当前 PATH 解析出的同一个绝对路径”，并仍拒绝任意路径：basename 必须是 `gh` / `gh.exe`，且 `realpath` 必须与 `which_path_only()` 的结果一致；其他绝对路径（包括指向 `curl.exe` 等）继续拒绝。
- **回归测试**：新增 `test_command_path_accepts_its_own_resolved_absolute_gh`、`test_command_path_rejects_unrelated_absolute_gh`，以及不 mock `auth_ok` 的 `test_search_candidates_absolute_gh_path_survives_auth`，防止只 mock 登录检查掩盖真实链路。
- **实测**：修复前 `search_candidates(['react'])` 返回 `code=2 / gh_binary 非法`；修复后 `rbb_scan` 真实返回 `Ok(code=0, count=3)`，并列出 `react/react` 等 3 个候选。技能仓 `repo_scan.py` 不受影响，同参数本地跑出 3 个候选、退出码 0。
- 测试：83 passed（离线 70 + SDK 桩 10 + 市场 smoke 3），`ruff check .` 全绿。
- 规则本体与契约哈希 `fa0f991f…bc93` 未动。

## 0.3.6 — 2026-11-14

按第四轮深审（7.5 / 10）修的 A/B、中 1–7 与轻量项：

- **A · 中文判决词不再全文子串匹配**：`check_deliverable` 只从 `结论：Join/Extend/Fork/Build`（含 `加入/扩展/分叉/新建`）这类结论行取判决；正文出现「新建文件」「加入上游」不再被当成结论。没有结论行时只对 ASCII 判决词做整词退化判定，中文常见词不做全文扫描。
- **B · 预算耗尽单独报 `code=4`**：`skipped` 与 `failed` 分开记录；全部关键词因总预算用完未检索时返回 `code=4` + `BUDGET_HINT`，不再误报 `code=3`「全部检索失败」，也不再输出「0 命中」提示。部分跳过时 `code=0` 并在 note 里说明。
- **中 1 · Build 门禁识别 Markdown 装饰**：`**不可复用原因：**`、`> 不可复用原因`、`## 不可复用原因` 现在都能命中；门禁仍只认行首。
- **中 2 · 多结论行取最后一条**：备选段先写 `结论：Build` 不会再盖掉最终的 `结论：Join`。
- **中 3 · 四段标题按标题行匹配**：`## 找到了什么`、`**找到了什么**`、`### 1. 找到了什么`、`> ### 备选与风险` 都算；正文里包含同名短语不算。
- **中 4 · 关键词校验**：拒绝空、>200 字符、NUL/控制字符与 `stars:/pushed:/archived:/user:/org:` 等限定符；`_scan_one` / `gh_json` 把 `ValueError` 转成可读错误。
- **中 5 · 超时预算**：`rbb_scan` 新增 `timeout`（5–300 秒）并透传到内核，外层 `wait_for(total + 5)`；`rbb_status` / `self_check` 的哈希、校验、gh 检查改为并行，不再串行等三份超时。
- **中 6 · `resolve_python` 不认嵌入式宿主**：只有当 `sys.executable` 的文件名像 Python（`python*` / `py`）时才使用，否则从 PATH 找真解释器。
- **中 7 · gh 绝对路径**：新增 `which_path_only()`，过滤 PATH 空条目与当前目录，检索只把解析出的绝对路径交给 subprocess；`stars:` / `pushed:` 改为 `>=` 闭区间。
- **轻量项**：`rbb_scan` 的 `stars=0` / `limit=0` 不再被 `or` 当成没传；`on_shutdown` 取消后台哈希任务；`rbb_rules` 在规则块为空时返回 Err；`updated_since` 只接受严格 `YYYY-MM-DD`（`20250101` 会回退并写 note）；围栏解析统一复用随包 `verify_localized.py`，不再维护第二份。
- **文档**：删除「哈希防篡改」的暗示，明确哈希只覆盖不变块，保留锚点的重写仍需人读 `kept / changed`。
- **同步上游 v1.2.7**：`SKILL.md`、`references/repo-triage.md` 与三份 `scripts/` 对齐已发布的 v1.2.7（判定表、Untrusted repository content、契约锁、`--timeout` 等）。
- 测试：80 passed（离线 67 + SDK 桩 10 + 市场 smoke 3），`ruff check .` 全绿。新增结论行/正文误命中、Markdown 门禁、多结论、标题行、NUL/限定符/超长关键词、预算跳过、绝对 gh 路径、`ValueError`、紧凑日期、嵌入式 Python、`stars=0` 与工具 timeout 透传等回归。
- 规则本体与契约哈希 `fa0f991f…bc93` 未动。

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
