# Hypothesis 批注接入

笔记检索 Demo 第一阶段。使用 Python 3.10+ 标准库。

实验可继续使用固定快照。日常查询可读取自动同步更新的镜像。

## 自动同步与查询

在 Linux 运行环境配置 `HYPOTHESIS_API_TOKEN`，把镜像文件放到同步进程与仓库程序都能读取的持久目录。在该环境的调度器里定期运行同一条命令，每次复用同一路径。

```sh
python3 hypothesis_sync.py sync --store /path/to/persistent/notes.json
```

同一镜像绑定认证账户及筛选范围。可加 `--group GROUP_ID` 或 `--uri URL`，以后每次同步保持相同参数。每轮按 ID 完成分页，按稳定 ID 比较新增、修改和移出范围的批注。镜像的 `synced_at` 表示最后成功同步的 UTC 时间。API 故障、分页总数不一致或账户范围改变会使该轮失败，之前的镜像仍可用。

仓库程序可以直接读镜像中的 `notes`，也可以调用下列只读命令。它按 Hypothesis 的修改时间返回，支持 `--id ANNOTATION_ID`。镜像包含划线原文、评论原文和来源。可在查询前立即运行一次 `sync`，再读本轮结果。

```sh
python3 hypothesis_sync.py latest --store /path/to/persistent/notes.json --limit 20
```

同步与查询使用 hypothesis_snapshot.py 和 hypothesis_sync.py。凭据与个人镜像留在运行环境，代码仓库可只保存脚本。对比实验可以从某次同步固定一份输入。

## 第二阶段 · 模型标记

模型标记读取同步镜像或固定的 `notes.json`，生成独立的 `tags.json`。先在云端运行环境配置三个值：

- `NOTE_TAG_API_URL`：所选服务完整的 Chat Completions 地址。
- `NOTE_TAG_MODEL`：实际验证的模型名。
- `NOTE_TAG_API_KEY`：通过运行环境的 secrets 注入。

```sh
python3 hypothesis_tags.py build --notes notes.json --index tags.json --max-calls 5
python3 hypothesis_tags.py vocab --notes notes.json --index tags.json
python3 hypothesis_tags.py read-note ANNOTATION_ID --notes notes.json --index tags.json
python3 hypothesis_tags.py read-tag TAG_ID --notes notes.json --index tags.json --limit 5
```

同一索引续跑只处理新增、文本有变化或标记失败的笔记。建库先小批调用，默认每次最多 20 次，每次请求默认最多 1000 个输出 token。若服务要求 `max_completion_tokens`，可加 `--output-token-field max_completion_tokens`。单条笔记的输入默认上限是 8000 字符，超长笔记在覆盖率中单列，仍可读取完整原文。返回包括处理数、失败状态及 API 提供的 token 数。

模型只收到划线、评论及少量候选标签。它可以复用标签或提出新标签。标签必须附可在指定字段逐字核对的依据，完成状态的依据只能来自评论。脚本校验文字和来源，标签含义及完成状态判断仍须用真实模型结果抽查。首版规范化同类同名标签，近义标签的合并留待样本评估。

更换 API、模型、prompt 或影响结果的预算参数时，用新索引路径保存对照。查询时同时提供当前笔记和标签文件，已删除和已改写的旧标签会被排除。未经模型处理的笔记计为待处理，已经处理但无可用标签的笔记可以返回空标签列表。

处理个人批注时，可在独立私有仓库的 GitHub Actions 中将它接在 `sync` 之后。每轮先从私有存储取上一版索引用于续跑，然后发布新版笔记和该轮索引。在查询端拿到这两个文件即可查看词表和原文，无需模型 API key。排程应串行运行同一索引的写入，配置首次执行前先用公开样本检查这两个文件能否从目标 Work 环境读回。

**当前验证** 端到端通过本地 Chat Completions 模拟服务，覆盖调用预算、可追溯依据、标签复用、失败续跑、笔记改写和移除，以及按标签回读原文。个人笔记与真实模型的标签质量、实际 token 用量、云端定时发布与 Work 读取待验。

## 用真实账号端到端测试

公开 fork 使用公开样本。个人批注实测放在私有运行环境，例如独立私有仓库的 GitHub Actions。把 `HYPOTHESIS_API_TOKEN` 与 `NOTE_TAG_API_KEY` 通过该运行环境的 secrets 注入，并配置 `NOTE_TAG_API_URL` 和 `NOTE_TAG_MODEL`。

```sh
python3 note-retrieval/hypothesis_e2e.py
```

脚本验证真实同步、打标、按标签回读和重复运行时的缓存命中。可通过 `HYPOTHESIS_E2E_URI` 限定到指定文档。脚本从最近十条批注中选出最多三条，将合计不超过 2000 字符的划线和评论发给所配置的模型服务。服务要求 `max_completion_tokens` 时，可传 `--output-token-field max_completion_tokens`。脚本在临时目录中处理批注，输出计数、模型报告的 token 用量和必要的错误状态。

## 拉取自己的批注

在 [Hypothesis 开发者页面](https://hypothes.is/account/developer) 取得 API token。运行下列命令后，在终端提示处输入 token。也可在运行环境配置 `HYPOTHESIS_API_TOKEN`，并省略 `--prompt-token`。

```sh
python3 hypothesis_snapshot.py fetch --prompt-token --limit 30 --output snapshots/mine
```

脚本先读取认证账户，再按这个账户拉取批注。可用 `--group GROUP_ID` 缩小到一个组，或用 `--uri URL` 缩小到一篇网页或 PDF。默认读取账户可见的自身批注，使用批注 ID 分页，数量上限用于获取可重复检查的小批样本。ID 排序体现稳定分页次序，与新旧顺序无关。

也可以在 Hypothesis 侧栏通过「分享 → 导出 → JSON」导出选定页面的自己的批注，再走同一套字段提取。

```sh
python3 hypothesis_snapshot.py import-json annotations.json --output snapshots/exported
```

换一个 `--output` 路径即可保留多次拉取。输出目录已经存在时，脚本会报错。

## 快照内容

- `raw.json` 保存各页 API 响应及其查询参数，或原样保留导出 JSON 的数据结构。
- `notes.json` 保存方便后续读取的笔记数组及采集时间。通过 `id` 关联同一条批注。

| 字段 | 含义 |
| --- | --- |
| `summary` | Hypothesis 保存的划线原文，取自 `TextQuoteSelector.exact`。无划线的页面笔记或回复为 `null` |
| `quotes` | 各段划线原文。单段直接作为摘要，多段用两个换行连接，原段仍可单独读取 |
| `comment` | 批注 `text` 原文，包括 Markdown、空白和换行。纯划线的评论为空字符串 |
| `source` | 页面或 PDF 地址、文档标题、可用时的批注与原位链接、目标 URI 和选择器类型 |
| `created`、`updated` | 服务器记录的创建和修改时间 |
| `group`、`references`、`hypothesis_tags` | 原有分组、回复链和 Hypothesis 标签 |

完整定位选择器（包括有值时的 PDF 页片段、文本位置及前后文）保留在 `raw.json`。同一 ID 在导出中出现多次时按修改时间保留一份。

## 已核对的 API 行为

- 从 `GET /api/search` 读取批注，采用 `sort=id&order=asc`，下一页用前页最后一条的 ID 作为 `search_after`。同一笔记的创建、修改和身份可通过 `created`、`updated` 和 `id` 跟踪。
- Hypothesis 的删除接口返回被删除 ID 及 `deleted: true`。搜索会排除已删除的批注。同步脚本每轮核对当前可见集合，在全量校验通过后移出缺失的 ID。两次轮询之间发生的中间版本可能不会出现在镜像里。
- 可用 `GET /api/annotations/{id}` 核对单条数据。公开与私有批注的读取受 Hypothesis 当前账户权限约束。凭据仅用于向 Hypothesis 发起请求。

参考：[Hypothesis API v1](https://h.readthedocs.io/en/latest/api-reference/v1/) · [导出说明](https://web.hypothes.is/help/exporting-and-importing-annotations/)

## 首次验证

`public_sample/` 包含通过真实 API 拉取的 15 条公开批注，每页 5 条。样本覆盖网页、PDF、纯划线、有评论的划线及无划线的笔记或回复。逐条核对了划线、评论、来源和时间。换用每页 3 条重拉前 8 条，ID 和内容一致。还用单条读取接口核对了网页和 PDF 批注。JSON 导入用本地样本检验了多段划线、空字段、原有空白和按 ID 去重。

个人账户的 API 拉取与界面原位定位待用自己的批注验证。有一页的侧栏 JSON 就可以先验证个人数据的字段映射。

