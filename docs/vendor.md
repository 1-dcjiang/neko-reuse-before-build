# 下游 vendor 接入（N.E.K.O 适配版）

本文只适用于 `1-dcjiang/neko-reuse-before-build`。通用技能仓
`1-dcjiang/reuse-before-build` 是另一份发布物，不要把两个版本混在同一插件目录。

## 选哪种发布物

| 目的 | 使用 | 说明 |
|---|---|---|
| 给 N.E.K.O 安装插件 | `reuse_before_build.neko-plugin` | 在 N.E.K.O 插件页安装本地插件包 |
| 把源码纳入自己的仓库 | `neko-reuse-before-build-v0.3.8-vendor.zip` | 固定 tag 的运行源码、规则、文档与校验工具 |
| 核对 Release 附件 | `SHA256SUMS.txt` | 覆盖插件包、vendor ZIP、校验工具及发布证明等附件 |
| 核对 vendor 后的文件 | ZIP 内的 `VENDOR-SHA256SUMS.txt` | 逐文件 SHA-256；复制到目标目录后仍能复检 |
| 核对官方插件包来源 | `market-evidence.json` | 记录源码提交与 `.neko-plugin` 哈希，不是 vendor ZIP 的哈希 |

vendor ZIP 不含 `.git`、GitHub Actions、测试、缓存或本机运行数据。开发测试留在源仓库；
下游只需运行文件即可。

## 1. 固定版本并下载

以下示例固定为 `v0.3.8`。升级时同时替换命令里的 tag 和文件名，不要用 `main` 作为可复现依赖。

```bash
mkdir rbb-vendor
cd rbb-vendor
gh release download v0.3.8 \
  --repo 1-dcjiang/neko-reuse-before-build \
  --pattern 'neko-reuse-before-build-v0.3.8-vendor.zip' \
  --pattern 'SHA256SUMS.txt' \
  --pattern 'vendor_release.py'
```

发布流程会核对 tag 与 `plugin.toml` 的 `0.3.8` 完全一致。`vendor_release.py` 是随 Release
提供的标准库校验工具，不需要安装第三方依赖。

## 2. 先校验，再解压

```bash
python vendor_release.py verify-archive \
  neko-reuse-before-build-v0.3.8-vendor.zip \
  --sums SHA256SUMS.txt
python -m zipfile -e neko-reuse-before-build-v0.3.8-vendor.zip extracted
```

`verify-archive` 同时检查：

1. ZIP 本身的 SHA-256 与 Release 的 `SHA256SUMS.txt` 一致；
2. ZIP 内文件集合与 `VENDOR-SHA256SUMS.txt` 一致，没有缺失或未登记文件；
3. 每个文件的 SHA-256、包内版本和根目录都正确；
4. 没有重复路径、路径穿越或符号链接。

已在 macOS / Linux 下载全部 Release 附件时，也可以执行 `sha256sum -c SHA256SUMS.txt`
做整批核对。SHA-256 用于完整性检查，不等于签名；不要把它表述成来源签名或安全审计。

## 3. 放进 N.E.K.O 插件目录

把 `extracted/neko-reuse-before-build-v0.3.8/` **里面的全部内容**复制到目标 N.E.K.O
仓库或安装目录：

```text
N.E.K.O/
└─ plugin/
   └─ plugins/
      └─ reuse_before_build/
         ├─ plugin.toml
         ├─ __init__.py
         ├─ rbb_core.py
         ├─ skills/
         └─ …
```

目标必须是 `plugin/plugins/reuse_before_build`，因为清单入口固定为
`plugin.plugins.reuse_before_build:ReuseBeforeBuildPlugin`。不要再套一层
`neko-reuse-before-build-v0.3.8`，也不要同时用 `.neko-plugin` 安装同一插件。

首次 vendor 前确认目标目录不存在；升级时先备份下游改动，再完整替换运行文件，避免新旧
脚本混用。保留下游已有的许可文件与本包 `LICENSE`，并继续履行各自的许可义务。

## 4. 在目标目录复检并启用

在 N.E.K.O 根目录运行：

```bash
python plugin/plugins/reuse_before_build/scripts/vendor_release.py verify-tree \
  plugin/plugins/reuse_before_build
```

看到 `PASS verify-tree` 后，再按宿主流程加载插件并点一次「自检规则完整性」。
`verify-tree` 校验清单内文件，不拒绝下游额外的配置或运行数据；规则正典仍需由插件自检，
两者不能互相替代。

## 更新与维护约定

- 每次升级固定一个新的 tag，重新核对 ZIP 与文件级清单，记录升级版本和哈希。
- 本地补丁与上游升级分开提交，便于下次比较；不要直接改 `VENDOR-SHA256SUMS.txt`
  来掩盖文件变化。确需修改时，应重新构建包并生成新清单。
- 本插件运行不依赖第三方 Python 包；GitHub 检索使用 PATH 中的 `gh`，没有 `gh`
  时规则、交付检查和本地化校验仍可使用。
- 通用技能仓与 N.E.K.O 适配版的修复要分别验证。本文中的 vendor 附件只由本仓库的
  tag Release 生成。

## 发布链路

推送 `v0.3.8` 后，官方 N.E.K.O Release 工作流先完成构建、测试、Release 与证明；
随后独立 `Vendor Source Release` 工作流通过 `workflow_run` 确认其完整成功，再检出同一发布提交，
构建 ZIP、校验包内清单、汇总 `SHA256SUMS.txt` 并上传。官方 managed `release.yml` 不作修改，
因此 vendor 附件不会与官方 Release 并发写入，也不会影响市场发布门禁。
