# 宿主适配：把同一套规则装进不同 AI

> 本文件是双语的：表格以路径与文件名为准（语言无关），提示词中英各一份。英文 canonical 正文是 `SKILL.md`，中文完整版是 [`zh-guide.md`](zh-guide.md)。

本 skill 的规则是"意图 + 流程"，不依赖特定工具。任何宿主只决定两件事：**放在哪里**、**有多少字数预算**。

## 目录

[四类落位](#先认清四类落位) · [dsh 落位](#a-类补充deepseek-harnessdsh) · [规则文件对照](#b-类规则文件对照) · [聊天平台入口](#c-类聊天平台落位) · [字数预算](#字数预算建议) · [中英提示](#精简版中文约-300-字可直接粘贴) · [硬门禁](#严格版补充团队自动化代理用) · [能力受限](#能力受限时)

## 先认清四类落位

| 类型 | 典型宿主 | 怎么装 |
|---|---|---|
| **A. Skill 目录**（能读多文件的 Agent） | Claude（Skills / Claude Code）、DeepSeek Harness（`dsh`）、支持 Agent Skills 规范的宿主 | 整个 `reuse-before-build/` 目录放进 Skills 目录，自动读 `SKILL.md` 与按需读 `references/` |
| **B. 仓库规则文件**（编码代理，读项目内约定文件） | Codex CLI、Cursor、Copilot、Gemini CLI、Windsurf、Cline、Aider、OpenHands、Trae、Qoder、Kiro、JetBrains Junie | 把「严格版」写进仓库根的 `AGENTS.md`（通用标准），或对应宿主的规则文件 |
| **C. 聊天自定义指令**（对话式 AI，只有提示词框） | ChatGPT、Claude.ai、Gemini、Grok、Copilot Chat、Perplexity、Le Chat、豆包、DeepSeek、Kimi、通义、文心、元宝、智谱、星火、MiniMax | 粘贴「精简版」到自定义指令/系统提示；完整版可作为知识文件或项目说明上传 |
| **D. 自建 Agent 系统提示** | LangChain / LlamaIndex / CrewAI / AutoGen / Dify / Coze / n8n / 自研 Agent | 放在 system prompt 第一段，并在工具说明里给出 `search` / `fetch` / `gh` 能力 |

## A 类补充：DeepSeek Harness（`dsh`）

`dsh`（DeepSeek Harness）自带 `@deepseek-ai/dsh-skill-filesystem`，按 Agent Skills 格式扫描本地目录，所以本 skill 不用改包装，整目录拷进去即可。核对于 `0.2.2-rc.1`。

| rank | 来源 | 路径 |
|---|---|---|
| 100 | 项目 `.dsh` | `<项目根>/.dsh/skills` |
| 200 | 项目 `.agents` | `<项目根>/.agents/skills` |
| 300 | 自定义 | 插件配置里的 `customSkillDirs` |
| 400 | 用户 `dsh` | `$DSH_HOME/skills`（默认 `~/.dsh/skills`，跳过 `.system`） |
| 500 | 用户 `agents` | `$DSH_AGENTS_HOME/skills`（默认 `~/.agents/skills`，与 Codex 共用） |

三条与其它宿主不同的约束：

- **发现深度只有一层**：只认 `<根>/<名字>/SKILL.md` 和扁平的 `<根>/<名字>.md`，不递归。要保留 `references/`、`scripts/` 就必须用目录包，别压成单文件。
- **目录描述上限 500 字符**（`DEFAULT_CATALOG_DESCRIPTION_MAX_LENGTH`）：超出部分在渲染给模型时被截掉，写在 500 之后的触发词等于不存在。本 skill 的 `description` 因此压到 475 字符，中文触发词放最前面。
- **frontmatter 只读 `name`、`description`、`whenToUse`、`metadata`、`invocation`**：`license` 这类多余键会被解析后忽略，不会导致整份 skill 被丢弃；`name` 必须是 kebab-case，`modelInvocable` 默认开，用户也能用 `/<名字>` 手动点名。

`name` 或调用值写错、frontmatter 缺失的文件会被静默跳过（只在日志里留一条警告），模型侧看不出区别——装完不生效时先查这三项。



## B 类：规则文件对照

| 宿主 | 文件 | 备注 |
|---|---|---|
| Codex CLI、Cursor、Jules、Amp、OpenCode、Zed、Cline、Junie 等 | `AGENTS.md` | **跨工具事实标准**，优先用它；放一节「先复用，再新建」即可被多数编码代理读到 |
| Claude Code | `CLAUDE.md`（或 `.claude/skills/`） | 与 `AGENTS.md` 可同时存在，内容一致更稳 |
| GitHub Copilot（Chat / Coding Agent） | `.github/copilot-instructions.md` | 仓库级；组织级可在 Copilot 设置里配 |
| Cursor | `.cursor/rules/*.mdc` | MDC = Markdown + frontmatter，可用 `alwaysApply` / `globs` 控制生效范围 |
| Windsurf（Cascade） | `.windsurf/rules/*.md` | 工作区及其子目录、父目录都会被读取；旧版 `.windsurfrules` |
| Cline | `.clinerules/` 或 `.cline/rules/` | 也读全局 `AGENTS.md` |
| Roo Code | `.roorules` / `.roo/rules/`（`.roo/rules-code/` 等按模式分） | — |
| Kilo Code | `.kilocode/rules/`（新版走 `kilo.jsonc` 的 `instructions` 或 `.kilo/rules/`） | 与 Roo 是近亲，路径随版本变，装前看官方文档 |
| Continue | `.continue/rules/` 或 config 的 `rules` | 与 IDE 内联 |
| Aider | `CONVENTIONS.md`，用 `--read` 指定（或写进 `.aider.conf.yml`） | 默认不自动读，必须显式给 |
| Gemini CLI | `GEMINI.md` | 部分版本也读 `AGENTS.md` |
| Kiro | `.kiro/steering/*.md`（自定义 agent 需在 `resources` 里显式列 glob） | steering 文件默认自动加载 |
| Amazon Q Developer | `.amazonq/rules/` | — |
| JetBrains AI / Junie | `.junie/guidelines.md` | 也读 `AGENTS.md` |
| Trae / Qoder / 通义灵码 / CodeGeeX / 文心 Comate / 腾讯 CodeBuddy | 各自「项目规则」入口，多数兼容 `AGENTS.md` | 找不到入口就用 `AGENTS.md` + 一次对话内粘贴 |

> 这类路径随版本调整，落地前在宿主官方文档里确认一次；确认成本远低于装错位置后"看起来没生效"的排查成本。
>
> **本表最后核对日期：2026-11-09**（Kiro / Cline / Roo / Kilo / Aider / Windsurf 已按厂商文档逐条校对）。宿主改版后请先复核再照抄。

## C 类：聊天平台落位

| 平台 | 入口 | 注意 |
|---|---|---|
| ChatGPT | Settings → Personalization → Custom Instructions；或 GPTs 的 Instructions；Projects 的项目说明 | 单框字数有限，用精简版；完整版塞知识文件 |
| Claude.ai | Projects 的 Project knowledge / Custom style；组织级可配 | 支持上传多文件，可整份放进去 |
| Gemini | Gems（自定义 Gem 的指令） | 让它先做英文检索再回中文 |
| Grok | 自定义指令 / 会话首条系统提示 | 擅长 X 与实时网页，适合查"是否已有人提过需求" |
| Microsoft Copilot | Copilot Studio 的 agent 指令 | — |
| Perplexity | Space 的 custom instructions | 偏检索，适合做第一轮候选发现 |
| Mistral Le Chat | 自定义指令 / Agents | — |
| DeepSeek | 系统提示（App 内可设"自定义提示词"） | 中文需求容易只搜中文，务必要求翻英文再搜 |
| 豆包 | 自定义指令 / 智能体的设定 | — |
| Kimi / 通义千问 / 文心一言 / 腾讯元宝 / 智谱清言 / 讯飞星火 / MiniMax / 阶跃 | 各自"人设/提示词/智能体设定" | 有联网开关的先开联网 |

## 字数预算建议

| 预算 | 用哪份 |
|---|---|
| ≤ 500 字 | 三句话版：先搜同类仓库 → 给 3–6 个候选打分 → 优先提 PR 加入，新建须写理由 |
| 500–1500 字 | 「精简版（中文）」或「Condensed prompt (English)」 |
| 1500–4000 字 | 中文完整版 [zh-guide.md](zh-guide.md) |
| 无上限 / 可上传文件 | 完整 `SKILL.md` + `references/`，或把 `SKILL.md` 作为知识文件 |

## 精简版（中文，约 300 字，可直接粘贴）

```text
在写任何新程序、脚本、库、插件或服务之前，你必须先搜索是否已经存在同类型或同一想法的公开项目，尤其是 GitHub 上的开源仓库。

规则：
1. 先假设"已经有人做过"，直到检索证明没有。禁止先写雏形再找参考。
2. 检索至少三轮：① GitHub 仓库/代码搜索（带 stars、language、pushed 限定符，关键词要翻成英文并做同义扩展）；② 包管理器与框架插件市场（npm/PyPI/crates/官方插件目录/awesome-list）；③ 在候选仓库的 issue 里查是否已有人提过同样需求、维护者是否回过"PR welcome"。
3. 给出 3–6 个候选，并按"活跃度 / 维护者响应 / 许可证 / 贡献友好度 / 需求匹配度 / 工程质量"打分排序。
4. 默认结论是"加入这个仓库成为贡献者"，而不是新建仓库。按这个优先级选择：合并进上游（Join）→ 做官方插件/扩展（Extend）→ fork 改进并回提上游（Fork）→ 都不成立才新建（Build）。
5. 只有在用户要求私有闭源、许可证不允许、需求方向相反、维护者已弃坑等情况下才允许新建，且必须书面说明理由。
6. 贡献前先读 CONTRIBUTING、PR 模板与最近合并的 PR；功能改动先开 issue 问维护者，bug 可直接提 PR；PR 保持单一目的、带测试、带文档。
7. 不虚构仓库名、star 数、许可证；无法联网时输出可复制的检索清单并说明无法核实。不为刷贡献提交低价值 PR，不把私有代码贴到公开 issue。
8. 每次输出必须包含：找到了什么（表格）、为什么选它、打算改动什么、被拒时的备选方案。
```

## Condensed prompt (English, ~250 words)

```text
Before writing any new program, script, library, plugin or service, search first for existing projects with the same purpose or idea — especially public GitHub repositories — and prefer contributing to one of them over creating a new one.

Rules:
1. Assume someone already built it until searching proves otherwise. Never draft first and look for references later.
2. Run three rounds of search: (a) GitHub repo and code search with qualifiers (stars, language, pushed, topic), expanding keywords into English synonyms; (b) package registries and framework plugin markets (npm, PyPI, crates.io, official extension directories, awesome-lists); (c) the candidate repos' issues, to find whether the same request was already filed and whether maintainers replied "PR welcome".
3. Return 3–6 candidates ranked on activity, maintainer responsiveness, license compatibility, contribution friendliness, requirement fit, and code quality.
4. Default to joining an existing repo as a contributor, not creating a new one. Choose in this order: upstream it (Join) → ship it as an official plugin/extension (Extend) → fork and PR back (Fork) → build new only if none of these hold (Build).
5. Build new only when the user requires private/closed source, the license forbids the use, the repo's direction is explicitly opposite, or the project is abandoned. State the reason in writing.
6. Before contributing, read CONTRIBUTING, the PR template, and recently merged PRs. Open an issue first for features; small bug fixes may go straight to PR. Keep PRs single-purpose, tested and documented.
7. Never invent repo names, star counts or licenses. If you cannot browse, output a copy-paste search checklist and say you could not verify. Do not spam low-value PRs and never paste private user code into public issues.
8. Every answer must include: what you found (table), why you chose it, what you will change, and the fallback if it is rejected.
```

## 严格版补充（团队/自动化代理用）

需要硬性拦截时，把这条加到上面任一段的末尾：

```text
硬门禁：在给出候选清单与首个可执行的贡献动作（issue 或 PR 草稿）之前，禁止输出完整的新项目脚手架代码。若检索到同名/同类仓库但选择新建，必须在回复开头用一行标明"不可复用原因：<许可/方向/维护状态/需求差异>"，否则视为违规。
```

## 能力受限时

表里的降级做法属于"宿主真的没有这个能力"。如果只是格式不合，**先按 `SKILL.md` 的「Self-localization」改写包装**（字段、布局、语言、工具绑定都能换），五条规则与门禁不变。

| 缺失能力 | 降级做法 |
|---|---|
| 没有联网 | 输出检索串清单 + 领域关键词 + 竞品名，要求用户提供候选链接或开启联网；禁止凭记忆断言仓库事实 |
| 有网页但无 GitHub 登录 | 只读：仓库页、issue 页、license 文件都能看；不能提交 PR，改为产出可复制的 issue 正文与 patch |
| 有 `gh` 但未认证 | 先 `gh auth status`，未登录时让用户执行 `gh auth login`，不要尝试代登录或读凭据 |
| 只能读却不能写（沙箱/只读挂载） | 产出 patch 与 PR 描述文本，由用户粘贴到网页提交 |
| 无法访问 GitHub（网络受限） | 用镜像/代理不可行时，改在国内镜像站（Gitee、npm 国内镜像、清华 PyPI 镜像）搜同类项目，并在交付中注明来源平台与星数口径不同 |
| 目标仓库无法 clone | 用 GitHub 网页读关键文件（`raw.githubusercontent.com`）确认接口，再让用户提供本地 clone 路径继续 |

## 交给宿主的本地化指令（可直接粘贴）

把下面这段交给任何会读文件的 AI，让它自己把 skill 装进自己的格式；它跑完校验才算完成。

```text
你现在要为本宿主本地化 reuse-before-build。按顺序做，每步都要有可见输出：

1. 对照表：这个 skill 的元素 → 本宿主的形态（frontmatter / 布局 / 语言 / 工具），一行一个。
2. 搬运：<!-- RBB:INVARIANTS:BEGIN --> 与 <!-- RBB:INVARIANTS:END --> 之间逐字保留，不许改写、合并、精简或重新编号；只改标记外的包装。宿主要求换语言时，块照样保留，第一行加 <!-- RBB:INVARIANTS:LANG: <语言码> -->。
3. 降级：宿主没有的能力写进 unsupported：<能力、原因、替代做法>，不要假装支持。
4. 自检：运行 python scripts/verify_localized.py <你的副本>（不要加任何 flag），把退出码和缺失项贴出来；失败就回到第 2 步，直到 PASS。
5. 声明：文件最后一行写
   localized: host=<名称> | kept=<原样保留项> | changed=<改动项> | unsupported=<做不到的项，没有写 none>

完成定义（DoD）：verify_localized.py 退出码为 0，且声明行存在（末尾追加版权行不影响判定）。缺一条都不算完成。
```

注意三条边界：**哈希只对逐字搬运生效**；翻译版转成"规则条数 1..5 + 每条规则锚点 + 六项全局锚点"校验，删条、改号、丢门禁都能抓住；但**保留关键词却把语气写软**（"应当考虑"替掉"必须"）抓不住，只能靠人读 `kept / changed` 那行。`--canonical` 只对包内两份参考件有效，对本地化副本会直接拒绝，别拿它当免检通道。
