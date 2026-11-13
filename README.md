# 先搜后建 · N.E.K.O 适配版

把 [reuse-before-build](https://github.com/1-dcjiang/reuse-before-build) 的规则装进 N.E.K.O。

## 它解决什么

N.E.K.O 里的 AI 接到「帮我做个 X」时，最容易干的事是直接写一版脚手架。这个插件把顺序掰回来：

> 先假设已经有人做过 → 三轮检索证明 → 默认加入上游当贡献者 → 只有证明不可复用才许新建。

规则本体不是插件作者的一句话，而是随包冻结的一段文本：带 `<!-- RBB:INVARIANTS -->` 标记和 sha256。删条、改编号、丢门禁会被随包校验器当场报错——对 AI，也对维护这个插件的人。

## v0.2.0 新增（同步上游 v1.1.0）

- **可改性硬闸**：Join 之前先判屎山——绿（优先优化上游）/ 黄（只 Extend，隔适配层不碰内核）/ 红（**总分 ≥ 8 也不许 Join**，走 Fork/Build 并写 `不可复用原因：代码可改性`），附 7 步证据化检测（`references/repo-triage.md`）。
- **稳定接入口 + 薄适配层**：新增 `references/integration-seam.md`——接入口按稳定性排序（插件/钩子 > 公开 API > CLI/配置 > 事件，私有函数与 monkey-patch 不作交付），所有上游调用收进一个约 ≤150 行只做翻译的适配层，配契约测试与四条验收。
- **分支纪律**：禁止直推 `main`/`master`（自己的仓库也一样）；「主干有可优化的地方」只改变动哪些文件，不改这条规则，修主干同样走分支 + PR。

三条都在不变块**之外**，五条核心规则与块哈希（`fa0f991f…bc93`）不变。

## 五个工具

| 工具 | 何时被调 | 返回 |
|---|---|---|
| `rbb_rules` | 用户说「帮我写一个…」「从零实现」时先调 | 核心规则原文（不可变块）/ 四步流程 / 整份 SKILL.md |
| `rbb_scan` | Discovery 阶段证明「有没有现成的」 | 去重候选表 + 结构化候选；`code=2/3` 明确表示**没查成** |
| `rbb_check` | 回复用户之前自检格式 | 四段是否齐、结论词、Build 时的 `不可复用原因` 门禁行 |
| `rbb_verify` | 把规则改写成别的宿主格式之后 | 不变块哈希 / 规则条数 1..5 / 逐条锚点 / 声明行 |
| `rbb_status` | 排查「哈希没算出来」「gh 不可用」 | 规则哈希、随包技能自检、gh 状态、当前设置 |

## 装法

1. 下载 Release 里的 `reuse_before_build.neko-plugin`；
2. N.E.K.O → 插件 → 安装本地插件包；
3. 插件设置里确认 `enable_github_scan`（默认开）与 `gh_binary`（默认 `gh`）；
4. 点一次「自检规则完整性」，看到规则哈希与 `PASS … verbatim` 即可。

没有 `gh` 也能装：检索会报 `code=2` 并给网页检索串，规则、格式检查、校验器照常可用。

## 目录

```
plugin.toml                        清单（id / entry / 设置项 / ui guide）
config.example.toml                可复制的运行配置示例（可变默认值放这里，不放 plugin.toml）
pyproject.toml                     最小工程声明（市场 CLI 的可选件，补上可消掉一条警告）
__init__.py                        插件类：5 个 @llm_tool + 生命周期 + 自检 action
rbb_core.py                        宿主无关纯逻辑（检索/格式检查/配置解析/校验包装），可离线测试
skills/reuse-before-build/          规则本体（规则文本与上游逐字一致）
  SKILL.md                         正典，含 RBB:INVARIANTS 冻结块
  localization.contract.json       块标记 + sha256 + 规则条数/锚点 + 声明格式
  references/                      检索手册 / 六维打分+可改性硬闸 / 贡献协议+分支纪律 / 接入口与薄适配层 / 宿主适配 / 中文完整版
  scripts/repo_scan.py             无宿主时的 CLI 检索（退出码 0/2/3）
  scripts/verify_localized.py      本地化校验器（四道闸）
tests/test_offline.py              23 条离线自检（不需要 N.E.K.O SDK）
tests/test_plugin_runtime.py       9 条运行时自检（跑在最小 SDK 桩上）
tests/test_smoke.py                市场模板要求的清单/入口 smoke test（函数式，pytest 收集 3 条）
.vscode/                           市场模板要求的 settings.json / tasks.json
docs/guide.md                      插件面板里的使用说明
.github/workflows/                 managed-template（Verify / Release）
```

> 说明：`skills/` 下的**规则文本**与上游逐字一致（sha256 `fa0f991f…bc93`，由 `verify_localized.py` 验证）。两个随包脚本为通过市场 CI 的 ruff 规则做过**纯格式微调**（局部变量重命名、导入方式调整），逻辑与退出码未变。


## 市场合规

仓库按 N.E.K.O 市场 CLI 的 `check -r`（严格模式）自查通过所要求的脚手架：

- `plugin.toml` 符合 `[plugin]` 允许键集；`entry` 指向 `plugin.plugins.reuse_before_build`；
- `config.example.toml` 承接可变默认值（不含 `[plugin]`），消掉「legacy runtime configuration」警告；
- `tests/test_smoke.py`、`.vscode/settings.json`、`.vscode/tasks.json`、`pyproject.toml`、`.gitignore` 齐备；
- `.github/workflows/{verify,release}.yml` 复用官方 `plugin-market-{verify,release}.yml@main`；
- 发版 tag 必须与 `[plugin].version` 相同（当前 `0.2.0` → tag `v0.2.0`）。

## 验过什么

本版（v0.2.0）：

- **本地**：32 项 `unittest`（离线 + SDK 桩）、随包 skill **34** 项（新增 4 条文档级回归：可改性硬闸 / 适配层链接 / 分支纪律 / 不 Join 红与不 import 上游内部）、双正典 `verify_localized.py` 均 `PASS`（`SKILL.md` verbatim、`zh-guide.md` translated(zh)）。
- **CI**：Verify / Release 的证据见本版 Release 的 `market-evidence.json`。

v0.1.1 的历史记录（跑的是官方 `plugin-market-verify.yml` / `plugin-market-release.yml`，不是本地模拟）：

- **Verify**（push 与 tag 各一次）：ruff（`E4,E7,E9,F,I` / py311 / 120 列）→ `sync --clean` → `check --release` → `pytest`（35 项：本仓 32 + 随包 skill 3 条 smoke）→ 打包并校验「包里恰好一个 `manifest.toml`」→ 生成 `market-evidence.json`。
- **Release**：`v0.1.1` 资产已产出 `reuse_before_build.neko-plugin`（81,181 字节 / sha256 `955425…5578` / payload_hash `8cc6d1…4ba1`），`market-evidence.json` 记录 `payload_hash_verified=true`、`tests=passed`、版本与 tag 一致（`source_commit` = `b803594`，`neko_commit` = `09e5160`）。
- **本地**：32 项 `unittest`（离线 + SDK 桩）、随包 skill 30 项、`verify_localized.py` 输出 `PASS verbatim rules=[1,2,3,4,5]/5`。

> CI 里只剩两条环境性提示：`plugin source directory does not have its own git repository`（CI 把源目录拷出来构建导致），以及 Node 20 弃用提示（由官方复用工作流的 action 版本决定，非本仓可控）。

## 市场登记（唯一未自动完成的一步）

官方 `neko-plugin publish` 的最后一步是向 `market.project-neko.cn/api/v1/release-publications` POST Release URL。本仓试过，市场回了：

```
400 {"detail":"GitHub release 未绑定到唯一的已通过插件","code":"release_repo_mismatch"}
```

意思是仓库还没在市场侧「绑定到插件记录」。这一步要走市场网页的 **Upload plugin → Continue to authorize**，用作者本人的 N.E.K.O 账号授权后才建立绑定；没有账号授权就没法自动完成。绑定后重新 POST 同一个 Release URL 即可（重复提交会重新挂到同一次核验）。

## 边界（写在这里，也写进规则里）

- **只读**：本插件不创建 issue、不提 PR、不发评论、不推送任何远端变更。公开写一律要用户逐次确认。
- **不虚构**：检索失败时返回的是「没查成」，不是「没有」；插件不会替你编仓库名和星数。
- **能抓什么**：删条、改编号、丢规则、缺声明、块内改字冒充逐字版。
- **抓不住什么**：保留全部关键词但把语气写软（「应当考虑」替掉「必须」）。这一层只能人读 `kept / changed`。

## 许可

MIT。规则本体与上游同源，改动请回上游提 PR，而不是只改这个副本。
