# 先搜后建 · N.E.K.O 适配版

把 [reuse-before-build](https://github.com/1-dcjiang/reuse-before-build) 的规则装进 N.E.K.O。

## 它解决什么

N.E.K.O 里的 AI 接到「帮我做个 X」时，最容易干的事是直接写一版脚手架。这个插件把顺序掰回来：

> 先假设已经有人做过 → 三轮检索证明 → 默认加入上游当贡献者 → 只有证明不可复用才许新建。

规则本体不是插件作者的一句话，而是随包冻结的一段文本：带 `<!-- RBB:INVARIANTS -->` 标记和 sha256。删条、改编号、丢门禁会被随包校验器当场报错——对 AI，也对维护这个插件的人。

## 版本记录

完整修复与新增记录见 [CHANGELOG.md](CHANGELOG.md)。`0.3.2` 修的是复查里的五项：`rbb_verify` 路径收敛、`gh` 参数注入、配置失败可见、默认值去重与布尔显式解析、录好的 `gh` JSON 契约测试；`0.3.3` 清理 README 历史段并把市场短描述压到 255 字上限；`0.3.4` 按第二轮复查修异常穿透、检索总预算、`gh_binary` 收敛、`extra` 白名单、结论词边界、空键去重、无时区时间与 stderr 脱敏；`0.3.5` 按第三轮复查去掉 `.bat/.cmd`、给登录检查留预算、补齐 stdout/Bearer 脱敏、夹取直调参数并兼容 Windows `py` 启动器。

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
3. 插件设置里确认 `enable_github_scan`（默认开）与 `gh_binary`（默认 `gh`，只允许 `gh` / `gh.exe`）；
4. 点一次「自检规则完整性」，看到规则哈希与 `PASS … verbatim` 即可。

没有 `gh` 也能装：检索会报 `code=2` 并给网页检索串，规则、格式检查、校验器照常可用。

## 目录

```
plugin.toml                        清单（id / entry / 设置项 / ui guide）
config.example.toml                可复制的运行配置示例（`[settings]` 默认值只在 plugin.toml 写一份）
pyproject.toml                     最小工程声明（市场 CLI 的可选件，补上可消掉一条警告）
__init__.py                        插件类：5 个 @llm_tool + 生命周期 + 自检 action
rbb_core.py                        宿主无关纯逻辑（检索/格式检查/配置解析/校验包装），可离线测试
skills/reuse-before-build/          规则本体（规则文本与上游逐字一致）
  SKILL.md                         正典，含 RBB:INVARIANTS 冻结块
  localization.contract.json       块标记 + sha256 + 规则条数/锚点 + 声明格式
  references/                      检索手册 / 六维打分+可改性硬闸 / 贡献协议+分支纪律 / 接入口与薄适配层 / 宿主适配 / 中文完整版
  prompt-digests.json              粘贴型提示词登记表（id / 版本 / sha256 / 字符数）
  scripts/repo_scan.py             无宿主时的 CLI 检索（退出码 0/2/3）
  scripts/prompt_digest.py         粘贴提示词反查（--check / --verify / --digest / --emit）
  scripts/verify_localized.py      本地化校验器（四道闸）
tests/test_offline.py              54 条离线自检（不需要 N.E.K.O SDK）
tests/test_plugin_runtime.py       9 条运行时自检（跑在最小 SDK 桩上）
tests/test_smoke.py                市场模板要求的清单/入口 smoke test（函数式，pytest 收集 3 条）
tests/fixtures/gh_search_repos.json 录好的 gh 检索输出（契约测试用）
CHANGELOG.md                       版本修复记录
.vscode/                           市场模板要求的 settings.json / tasks.json
docs/guide.md                      插件面板里的使用说明
.github/workflows/                 managed-template（Verify / Release）
```

> 说明：`skills/` 下的**规则文本**与上游逐字一致（sha256 `fa0f991f…bc93`，由 `verify_localized.py` 验证）。两个随包脚本为通过市场 CI 的 ruff 规则做过**纯格式微调**（局部变量重命名、导入方式调整），逻辑与退出码未变。


## 市场合规

仓库按 N.E.K.O 市场 CLI 的 `check -r`（严格模式）自查通过所要求的脚手架：

- `plugin.toml` 符合 `[plugin]` 允许键集；`entry` 指向 `plugin.plugins.reuse_before_build`；
- `config.example.toml` 承接运行配置模板（不含 `[plugin]`），`[settings]` 默认值不重复抄写，消掉「legacy runtime configuration」警告；
- `tests/test_smoke.py`、`.vscode/settings.json`、`.vscode/tasks.json`、`pyproject.toml`、`.gitignore` 齐备；
- `.github/workflows/{verify,release}.yml` 复用官方 `plugin-market-{verify,release}.yml@main`；
- 发版 tag 必须与 `[plugin].version` 相同（当前 `0.3.5` → tag `v0.3.5`）。

## 验过什么

本版（v0.3.5）：

- **本地**：`python -m pytest tests` **66 passed**（离线 54 + SDK 桩 9 + 市场 smoke 3）；`ruff check .` 全绿；官方 `check -r` 通过（`tests=passed`、`payload_hash_verified=True`）。
- **随包 skill**：`verify_localized.py` 对 `SKILL.md` 输出 `PASS verbatim`，`zh-guide.md` 输出 `translated(zh)`；规则块哈希 `fa0f991f…bc93` 未变。
- **CI**：Verify / Release 的证据见本版 Release 的 `market-evidence.json`。

## 发布与市场

- 流程：打 tag → GitHub Release 产出 `reuse_before_build.neko-plugin` 与 `market-evidence.json` → `neko-plugin publish market <release-url>`。
- 市场展示信息（简介、展示页 README）由市场侧独立维护，发版不会覆盖；发布后在开发者中心「编辑展示信息 / 编辑展示页面」同步。

## 边界（写在这里，也写进规则里）

- **只读**：本插件不创建 issue、不提 PR、不发评论、不推送任何远端变更。公开写一律要用户逐次确认。
- **不虚构**：检索失败时返回的是「没查成」，不是「没有」；插件不会替你编仓库名和星数。
- **能抓什么**：删条、改编号、丢规则、缺声明、块内改字冒充逐字版。
- **抓不住什么**：保留全部关键词但把语气写软（「应当考虑」替掉「必须」）。这一层只能人读 `kept / changed`。

## 许可

MIT。规则本体与上游同源，改动请回上游提 PR，而不是只改这个副本。
