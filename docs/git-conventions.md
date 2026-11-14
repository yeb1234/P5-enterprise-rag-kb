# Git 版本管理与提交规范

> **性质**：本文是**规范建议 + 仓库现状实查记录**，不是已强制执行的策略。
> 实查命令：`git branch -avv`、`git tag -l`、`git remote -v`、`git status --short`、`git log --all --oneline --decorate -40`、`git show -s --format=…`（在 `C:/Users/yeb12/WorkBuddy/P5企业级知识库项目` 执行）。
> **本轮未创建任何 Git hook、未配置远端、未打 tag、未改写历史、未执行 `git commit`**。文中规则除「实查结论」外均为**建议**，落地手段见 §6。

---

## 1. 仓库现状（实查，非推断）

### 1.1 分支：6 条，构成一条线性里程碑链

```
4992bed  baseline: 项目现状（评估工作台接入前）
   │
b66d116  [main]  evalbench: 评估工作台骨架（调研/样本/提示词/参数/指标/执行器/探测/展示）
   │
2361ece  [eval/v1-baseline]  eval/v1: 基线评估（3 模型 × 12 样本）+ 评分口径 1.0→1.2 定稿与前后对照
   │
3a7dc41  [eval/v2-prompts]   eval/v2: 提示词版本对比收敛到 v2_grounded（economy 0.25→1.00，延迟 -42%）+ 聚合延迟修复
   │
466a5cc  [eval/v3-params]    eval/v3: 参数组 A/B/C 对比收敛到 B（确定性贪心），保留 RGB 上下文设定
   │
5c0efc7  [eval/v4-models]    eval/v4: 最终配置三模型分别评估 + kimi 兼容性适配三步实证与选型结论
   │
a089a7f  demo/v1: 最终交付（评估流程 + 结果展示页 + 简要说明 + 项目记忆）
   │
60720aa  feat: 评估测试可观测子页 + 四维限流/成本预算/权限矩阵 + 前端 i18n/6主题色/明暗模式
   │
e18709b  docs: 补齐整体改进日志并统一前端静态资源版本
   │
3005197  chore: 记录收尾复核与全套回归结果
   │
1ae2efc  docs: 收口组织模型调整方案的事实偏差与离线复核结论
   │
e1dd3d4  [demo/v1, HEAD]  docs: 补记离线 33/33 端到端自测与隔离跑法
```

| 实查项 | 结果 |
|---|---|
| 分支 | `main`、`eval/v1-baseline`、`eval/v2-prompts`、`eval/v3-params`、`eval/v4-models`、`demo/v1`（当前 HEAD `e1dd3d4`） |
| 历史形态 | **线性链，无 merge commit**（`git log --all --oneline` 为单一路径）；每个里程碑分支 tip 就是「一个版本一次提交」 |
| tag | **空**（`git tag -l` 无输出） |
| remote | **空**（`git remote -v` 无输出）→ 无推送、无 PR、无服务端分支保护 |
| 工作区 | 干净（`git status --short` 无输出，本轮文档落盘前） |
| 现有提交前缀 | `baseline:` / `eval/v1:` … `eval/v4:` / `demo/v1:` / `evalbench:` / `feat:` / `docs:` / `chore:` —— **只有后 3 种符合 Conventional Commits 的 type 白名单** |

### 1.2 两个必须诚实指出的现状问题

1. **`main` 已落后**：`main` 停在 `b66d116`（评估工作台骨架），而 `demo/v1` 已推进到 `e1dd3d4`，中间 10 个提交**从未回灌 main**。历史分支名 `eval/*` / `demo/*` 实际上承担了版本线，main 反而不是最新稳定基线。
2. **无不可变版本锚点**：没有任何 tag，`demo/v1` 这类分支名会被继续提交移动。定位某个「开发版本」目前只能靠记住 hash（如 `RUN-095a2a4a40c6` 对应的物料在 `demo/selftest.py` 中固定，但代码版本靠 hash 记忆）。

> 这两点是 §2 分支策略与 tag 规则要解决的真实问题，不是理论设计。

---

## 2. 分支策略

### 2.1 三类分支的用途（结合本项目实际 6 条）

| 类别 | 本项目分支 | 用途 | 是否可继续提交 |
|---|---|---|---|
| `main` | `main` | **稳定基线 / 唯一发布起点**。应始终指向最近一个「已验收、可复现」的开发版本 | 仅接受里程碑回灌（合并或快进），**不做日常开发** |
| `eval/*` | `eval/v1-baseline` / `eval/v2-prompts` / `eval/v3-params` / `eval/v4-models` | **评估迭代里程碑**：每条 = 一次评估版本冻结（基线 / 提示词 / 参数 / 模型），对应一次口径定稿 | **定稿后冻结**，改为只读快照；新评估版本另开 `eval/v5-*` |
| `demo/*` | `demo/v1` | **可运行演示交付线**：演示切片、结果展示页、离线自测说明、改进日志与复核收口 | 演示版本内可继续提交；**交付定稿后打 tag 并冻结**，后续另开 `demo/v2` |
| 功能分支（建议新增） | `feat/<主题>` / `fix/<主题>` | 日常改动的工作区，从 `main` 切出，改完合回 | 是，合并后删除 |

> `eval/` 是**目录冻结区**，`eval/*` 是**分支名**，两者无关，不要混淆：分支名带 `eval/` 不表示可以改动 `eval/` 目录内文件（见 §4）。

### 2.2 保护规则（**建议**；当前无远端，只能本地落地）

服务端保护（GitHub/GitLab 的 protected branch、required review、status check）在本仓库**不适用**——实查无 remote。落地为三层：

| 层 | 规则 | 落地方式 |
|---|---|---|
| 约定层 | `main` / `eval/*` 定稿分支 / `demo/*` 定稿分支禁止 `git push --force`、禁止 `git reset --hard` 改写已发布历史；冻结分支不再直接提交 | 本文 + 团队纪律；已发布的历史 hash（含 §1.1 全部 12 个）**一律不回改** |
| 本地层 | 提交前强制跑 §5 检查清单，尤其冻结区与密钥扫描 | 可选 `pre-commit` hook（§6 给出片段，**本轮未创建**） |
| 远端层（未来） | 接入远端后对 `main` 启用保护分支、PR 合并、CI 状态检查；`eval/*` / `demo/*` 定稿后设只读 | **未做**，列为后续 |

> 注意：`docs/CHANGELOG.md` 的历史条目**不回改**（项目既有裁决），只新增修订记录——这条同样适用于 Git 历史：不要用 `rebase -i` / `filter-repo` 去「修正」§1.1 里那些不符合新规范的旧提交信息，它们作为反例留档即可。

### 2.3 合并方向（单向向前）

```
main ──► eval/v1 ──► eval/v2 ──► eval/v3 ──► eval/v4 ──► demo/v1 ──► demo/v2 …
  ▲                                                          │
  └────────────── 里程碑定稿后回灌（建议，当前未做）──────────┘
```

- **允许**：从 `main`（或前一里程碑 tip）切出新 `eval/vN`；从最新 `eval/*` 切出 `demo/vN`；里程碑定稿后把 tip **回灌 `main`**（`git merge --ff-only`，保持线性历史）。
- **禁止**：反向合并（`demo/v1` → `eval/v4` 覆盖已定稿评估版本）、跨里程碑直接跳合并、在已冻结的 `eval/*` 上追加提交。
- **建议补做**：把 `e1dd3d4` 这条已验收的演示交付线回灌 `main`（或至少打 tag），消除 §1.2 的「main 落后」；**本轮未执行**。
- 由于当前历史全是线性快进，优先用 `--ff-only`；确需并行开发时用 `--no-ff` 产生一个合并提交并在正文写明合并范围。

### 2.4 tag 规则（**建议**，当前仓库无 tag）

| 项 | 规则 |
|---|---|
| 类型 | **附注标签** `git tag -a`（带作者/日期/说明），不用轻量标签 |
| 命名 | `v<major>.<minor>.<patch>` 用于正式版本；评估里程碑用 `eval-v1.0` / `eval-v2.0` …；演示交付用 `demo-v1.0`；骨架阶段可用 `v0.1.0-eval-v4` 这类预发布后缀 |
| 打在何处 | 打在**里程碑分支 tip**（即「一个版本一次提交」的那个提交），不打在分支名上（分支会移动，tag 不会） |
| 不可变 | tag 一经发布**不得移动/删除重建**；写错则新增修正 tag 并在 CHANGELOG 注明 |
| 与物料对齐 | tag 说明里写明该版本的 `run_id` / `config_snapshot` / 回归计数（本项目 demo `run_id=RUN-095a2a4a40c6` 与 `config_snapshot` 是不可变冻结对象，见 §4） |
| 建议补做 | 给现有 5 个里程碑补打 tag（例如 `git tag -a eval-v1.0 2361ece -m "…"`），**本轮未执行** |

### 2.5 「每个开发版本一次提交」的落地办法（**建议 SOP**）

项目现状天然就是这个模式：`eval/v1`…`eval/v4` 每个分支 tip 恰好一次提交、串联成链。要把它变成可重复的纪律：

1. **切版本分支**：从 `main`（或上一里程碑 tip）切 `git switch -c eval/v5-<主题> main`。
2. **一个版本 = 一个逻辑变更集**：本次要做的改动集中完成，中途不提交；用 `git add -p` 精确暂存，避免把调试文件、`.env`、冻结区误入。
3. **提交前跑 §5 全量检查**（含冻结区零 diff、密钥扫描、带 `cr-at-eol` 的 `git diff --check`）。
4. **一次提交**：`git commit -m "type(scope): subject" -m "正文…"`，信息按 §3。
5. **打 tag**：`git tag -a eval-v5.0 -m "…"`（附注、不可变）。
6. **验收信号**：该版本的回归命令全绿后，才允许回灌 `main`（`git switch main && git merge --ff-only eval/v5-<主题>`）。
7. **若中途不得不多次提交**：在**未推送、无远端、未打 tag** 的前提下，用 `git rebase -i <base>` 压缩为一次（squash）再打 tag；已发布历史**禁止**压缩。
8. **「一次提交」≠「一个巨型提交」**：若一个版本里出现多个互不相关的关注点（例如同时改「提示词收敛」和「聚合延迟修复」），应当**拆成两个版本分支、两次提交**，而不是塞进一个 commit 用 `+` 罗列——这正是 §3.5 中 `3a7dc41`、`60720aa` 的反例。

---

## 3. 提交信息规范（Conventional Commits 收敛版，**建议**）

格式：

```
<type>(<scope>): <subject>
<空行>
<body>
<空行>
<footer>
```

### 3.1 type 白名单（**只允许以下 10 个**，小写）

| type | 含义 | 本项目适用场景 |
|---|---|---|
| `feat` | 新功能 | 新增 API、页面、可观测子页 |
| `fix` | 缺陷修复 | 口径偏差、断言失败、缓存串部门等 |
| `docs` | 仅文档 | 改进日志、方案、README、本文类规范 |
| `chore` | 杂务 / 记录 | 收尾复核、回归结果记录、基线快照 |
| `refactor` | 不改变外部行为的重构 | 抽取 `visible_department_ids()` 等 |
| `test` | 仅测试 | 越权用例集、对拍用例 |
| `perf` | 性能优化 | 索引、expr 分片 |
| `build` | 构建系统 / 依赖 | Dockerfile、compose、package.json |
| `ci` | 流水线 | 评测闸门、自动化脚本 |
| `revert` | 回滚某次提交 | 正文写 `This reverts commit <hash>.` |

> **不在白名单**：`baseline`、`eval/v1`…`eval/v4`、`evalbench`、`demo/v1`、`wip`、`update`、`misc`。这些应改为「合法 type + 对应 scope」，例如 `eval/v1:` → `docs(eval):` 或 `feat(eval):`。

### 3.2 scope（可选，小写，括号内）

建议白名单（取自项目实际目录 / 模块）：`backend`、`webapp`、`frontend`、`docs`、`compose`、`migrations`、`config`、`deps`、`evalbench`、`eval`、`demo`。

- 一次提交只写一个 scope；跨多个模块且无法归并时，说明「该提交应拆分」。
- **`eval` / `demo` / `evalbench` 作为 scope 只表示「主题与评估/演示相关」，绝不表示可以改动 `eval/`、`demo/`、`evalbench/` 目录**——这三个目录是冻结区，见 §4。
- 不写 scope 是允许的（如 `docs: …`），但同一仓库内尽量一致。

### 3.3 subject 写法

- 用**祈使句 / 动宾短语**，中文即可：`docs: 补齐整体改进日志并统一前端静态资源版本`。
- 首字小写（中文不受大小写影响，英文 type/scope 一律小写）；**句末不加句号**。
- **只描述一个关注点**；禁止用 `+`、`&`、`、` 把多个不相关改动串成清单。
- 长度：`type(scope): ` 前缀不计入时，**subject ≤ 50 字符**；含前缀整行 **≤ 72 字符**（硬上限）。中文按字符计（Git Bash 下可用 `awk` 粗核，多字节字符可能按字节计，见 §5）。
- 不在 subject 里塞数字结论 / 括号注释 / 版本号（放 body）：`feat(eval): 提示词对比收敛到 v2_grounded` 优于把 `economy 0.25→1.00，延迟 -42%` 写进 subject。

### 3.4 body 与 footer

- subject 与 body 之间**必须空一行**；body 每行 **≤ 72 字符**（中文建议 ≤ 36 汉字后折行）。
- body 写「**为什么**」与「影响范围」，不重复 subject；多条用 `- ` 列表。
- 需要记录的量化结论（如 `51/51`、`33/33`、`run_id`、延迟变化）放 body，不放 subject。
- footer 用 `key: value` 或 `key #value`：`Refs: #12`、`BREAKING CHANGE: …`、`Reviewed-by: …`、`Frozen-Areas: eval/ demo/ evalbench/ untouched`。
- **破坏性变更**必须在 footer 写 `BREAKING CHANGE:`，并在 body 说明迁移步骤。
- body / footer 均可省略（如纯 chore 记录），但涉及配置、迁移、冻结边界的提交**建议必写 body**。

### 3.5 长度限制汇总

| 部分 | 建议 | 硬上限 |
|---|---|---|
| 首行（含 `type(scope): `） | ≤ 50 字符 | 72 字符 |
| subject 本身 | ≤ 50 字符 | — |
| body 每行 | ≤ 72 字符（中文 ≤ 36 汉字） | 100 字符 |
| body 总行数 | ≤ 20 行 | — |
| footer 每行 | ≤ 72 字符 | — |

---

## 3.6 用本项目真实提交作正例与反例

> 所有 hash 均为实查结果；**历史提交不回改**，以下只作为「今后怎么写」的对照。

### 正例

| hash | 原文 | 好在哪 |
|---|---|---|
| `e18709b` | `docs: 补齐整体改进日志并统一前端静态资源版本`<br>body 三条：`- 新增 docs/CHANGELOG.md：…` / `- README.md 文档索引加入改进日志` / `- webapp/static/index.html 中 app.js?v=10 对齐 style.css?v=11…` | type 合规、subject 简洁单一、body 用 `- ` 列表说明「改了什么 / 为什么」，可直接复用为模板 |
| `3005197` | `chore: 记录收尾复核与全套回归结果` | type 合规、subject 短且单一；纯记录类省略 body 可接受 |
| `1ae2efc` | `docs: 收口组织模型调整方案的事实偏差与离线复核结论`<br>body 写明「实查 i18n.js 为手写字典…作废机械生成四步改手工同步四步…五套离线回归全绿，源码与冻结区零改动」 | type 合规、明确写出「**冻结区零改动**」这一关键事实，符合 §4 纪律；可改进点：body 是长单行，应按 72 字符折行 |
| `e1dd3d4` | `docs: 补记离线 33/33 端到端自测与隔离跑法`<br>body 写明隔离临时库 + 离线 provider 补跑、**原真实配置报告按字节还原**、临时服务与临时库已清理 | type 合规、body 交代了「不污染演示库」的处置，是可复现性记录的好范例；可改进点：量化结论 `33/33` 建议移到 body |

**建议的标准写法（以本轮三份交付为例，未执行提交）**：

```
build(compose): 补齐编排韧性并新增部署与 Git 规范

- docker-compose.yml 增加 restart: unless-stopped、日志轮转、
  资源上限与 healthcheck start_period，纯增量、不改服务名与端口
- 新增 docs/deployment-guide.md：编排复核 + 腾讯云上线建议
- 新增 docs/git-conventions.md：分支、提交、冻结区纪律
- 未执行 docker compose up、未改任何 Python/JS 源码

Frozen-Areas: eval/ demo/ evalbench/ untouched
```

### 反例（逐条给出改写）

| hash | 原文（节选） | 问题 | 建议改写 |
|---|---|---|---|
| `4992bed` | `baseline: 项目现状（评估工作台接入前）` | `baseline` **不在 type 白名单**；括号内塞说明；无 scope | `chore: 记录评估工作台接入前的项目基线快照` |
| `2361ece` | `eval/v1: 基线评估（3 模型 × 12 样本）+ 评分口径 1.0→1.2 定稿与前后对照` | 把分支名当 type；含 `/` 不是合法 type；**两个关注点**用 `+` 拼接；超长；量化细节挤在 subject | `feat(eval): 冻结 v1 基线评估与评分口径 1.2`（body 写 `3 模型 × 12 样本`、`economy` 定稿与前后对照） |
| `3a7dc41` | `eval/v2: 提示词版本对比收敛到 v2_grounded（economy 0.25→1.00，延迟 -42%）+ 聚合延迟修复` | type 非法；**「提示词收敛」与「聚合延迟修复」是两件事**，应拆两次提交；subject 远超 72 字符 | 拆为 `feat(eval): 提示词对比收敛到 v2_grounded` + `fix(eval): 修复聚合延迟统计`，量化结论进各自 body |
| `466a5cc` | `eval/v3: 参数组 A/B/C 对比收敛到 B（确定性贪心），保留 RGB 上下文设定` | type 非法；括号内塞两组信息 | `feat(eval): 参数组对比收敛到 B（确定性贪心）`，body 补「保留 RGB 上下文设定」 |
| `5c0efc7` | `eval/v4: 最终配置三模型分别评估 + kimi 兼容性适配三步实证与选型结论` | type 非法；`+` 拼接两个关注点 | 拆为 `feat(eval): 最终配置三模型分别评估` + `fix(eval): 适配 kimi 接口差异` |
| `a089a7f` | `demo/v1: 最终交付（评估流程 + 结果展示页 + 简要说明 + 项目记忆）` | type 非法；把 4 项交付物塞进括号；「最终交付」无信息量 | `feat(demo): 交付评估流程与结果展示页`，body 用 `- ` 列 4 项 |
| `b66d116` | `evalbench: 评估工作台骨架（调研/样本/提示词/参数/指标/执行器/探测/展示）` | 缺 type（`evalbench` 是模块名，应作 scope）；括号内 8 项清单 | `feat(evalbench): 搭建评估工作台骨架`，8 项清单移入 body |
| `60720aa` | `feat: 评估测试可观测子页 + 四维限流/成本预算/权限矩阵 + 前端 i18n/6主题色/明暗模式` | **type 合规但拆提交不合格**：三个不相关关注点（可观测子页 / 限流与预算 / i18n 与主题）用一个 `+` 长串；无 scope；无 body；远超长度上限 | 拆 3 次：`feat(webapp): 新增评估测试可观测子页`、`feat(webapp): 四维限流与成本预算`、`feat(webapp): 前端 i18n 六主题色与明暗模式` |

> 归纳：本仓库历史提交最常见的两类问题是 **① 把分支名/模块名当 type**（`eval/vN:`、`demo/v1:`、`evalbench:`、`baseline:`），**② 用一个 `+` 长串把多个关注点塞进一次提交**。新规范优先解决这两点。

---

## 4. 冻结区提交纪律（**硬性约束**）

### 4.1 绝对不得改动

| 对象 | 约束 |
|---|---|
| `eval/` | 目录内 14 个版本化对象**完全不动**；`__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径不动 |
| `demo/` | `run_id=RUN-095a2a4a40c6`、`config_snapshot` 与 16/16 自测**零字节改动**；历史物料不回溯重写 |
| `evalbench/` | 完全不动，无需适配层 |
| `docs/evaluation-plan.md` | 含 `__TBC__` 字面量的行（第 706 行「账号/租户/工作空间」、第 733 行 `EMBEDDING_CACHE__TBC`）**及周边文字一律不动** |
| `docs/CHANGELOG.md` | **历史条目不回改**，仅新增修订记录（本轮任务亦明确不改动此文件） |
| `.env` | 密钥只进 `.env`（`.gitignore:1` 已排除），**绝不入库**；`.env.example` 只放模板值、不放真实密钥 |

### 4.2 提交前必跑（团队既有裁决口径，**原样保留**）

```bash
git diff --exit-code -- eval/ demo/
```

返回 0 才允许提交。补充两点说明（不替换上述裁决命令）：

- `git diff` 默认只比较**工作区 vs 暂存区**，且**看不到未跟踪的新文件**。因此 §5 额外加上 `git status --porcelain -- eval/ demo/ evalbench/`（抓未跟踪文件）与 `git diff --cached --exit-code -- eval/ demo/`（抓已暂存改动）作为补强。
- `python demo/selftest.py`（16/16、7 字段与 `run_id` 一致）是冻结物料的运行时校验，产品/QA 侧已有命令；`config_snapshot_hash` 由 `eval/config/objects/*.json` 14 个对象规范化哈希产生，`eval/` 与 `demo/` 零 diff 即哈希不变，**不新增校验脚本**。

### 4.3 密钥扫描

```bash
git grep -nE 'sk-[A-Za-z0-9]{20,}' -- .          # 期望：零命中
! git ls-files --error-unmatch .env >/dev/null 2>&1 && echo "OK: .env 未被跟踪"
```

> `.env.example` 中的 `DEEPSEEK_API_KEY=`、`EMBEDDING_API_KEY=` 是空值模板，不是密钥；`JWT_SECRET_KEY=change-me-in-production-with-a-32-byte-random-string` 是占位提示串，均不视为泄漏。真实密钥一旦写入，应立即轮换并清理历史（若已进过版本库，仅删除文件不够）。

---

## 5. 提交前检查命令清单（**可直接复制，Git Bash 可用**）

```bash
#!/usr/bin/env bash
# 文件：无（本轮未创建脚本/hook，以下为可直接粘贴执行的命令块）
set -euo pipefail
cd "C:/Users/yeb12/WorkBuddy/P5企业级知识库项目"

# 1) 工作区总览：确认没有意外的未跟踪文件 / 误改文件
git status --short

# 2) 【硬性】冻结区零 diff（团队裁决口径，原样保留）
git diff --exit-code -- eval/ demo/

# 3) 补强：已暂存改动 + 未跟踪文件 + evalbench + evaluation-plan
git diff --cached --exit-code -- eval/ demo/
git status --porcelain -- eval/ demo/ evalbench/
git diff --exit-code -- evalbench/
git diff --exit-code -- docs/evaluation-plan.md

# 4) 密钥与配置：不入库、无真实密钥串
git grep -nE 'sk-[A-Za-z0-9]{20,}' -- . || true
! git ls-files --error-unmatch .env >/dev/null 2>&1 && echo "OK: .env 未被跟踪"

# 5) 格式：无空白错误、无冲突标记
# 本仓库 index.html / style.css 为 CRLF、app.js / i18n.js / theme.js 为 LF，
# 且 core.autocrlf=false。裸 `git diff --check` 会把 CRLF 的 \r 误判为
# trailing whitespace（2026-11-14 提交前实测复现），故必须带 cr-at-eol：
git -c core.whitespace=cr-at-eol diff --check
git -c core.whitespace=cr-at-eol diff --cached --check
! git grep -nE '^(<<<<<<<|=======|>>>>>>>)' -- . ':!docs/git-conventions.md' || true

# 6) 本次改动范围复核（人眼确认）
git diff --stat
git diff --cached --stat

# 7) 提交信息自检（提交后用；首行长度粗核，中文按字节计会偏大）
# git log -1 --format='%s' | awk '{print "首行字符数(字节):", length($0)}'

# 8) 一个版本一次提交
git add -p                                  # 精确暂存，避免误入冻结区/.env/调试文件
git commit -m "type(scope): subject" -m "- 为什么改 / 影响范围" -m "Frozen-Areas: eval/ demo/ evalbench/ untouched"
```

**上线 / 交付前追加（与部署流程对齐，见 `docs/deployment-guide.md`）**：

```bash
python demo/selftest.py                     # 16/16 且 run_id / config_snapshot 一致
git diff --exit-code -- eval/ demo/         # 再次确认
docker compose config -q                    # 编排静态校验（若本机有 docker compose）
```

**门禁判定**：第 2、3 步任一返回非 0，或第 4 步出现真实密钥串 → **立即停止提交**，先还原冻结区文件（`git restore --source=HEAD -- <path>`，仅在确认是自己误改时）并轮换密钥。

---

## 6. 落地建议与待确认

| 项 | 状态 | 建议 / 默认假设 |
|---|---|---|
| 本文规则强制力 | **未强制** | 当前靠纪律 + §5 手工清单；本轮**未创建** `.git/hooks/pre-commit`、未配置远端 |
| `pre-commit` hook（**未落地**） | 只设计 | 可把 §5 第 2–5 步写入 `.git/hooks/pre-commit` 并 `chmod +x`；注意 hook 不随版本库分发，需另存 `scripts/` 并文档化 |
| `main` 回灌 / 里程碑补 tag | **未执行** | 见 §1.2 / §2.3 / §2.4；建议在下个开发版本开工前完成，需主理人确认基线 |
| 远端与保护分支 | **未做** | 无 remote；接入后再启用 PR / status check / protected branch |
| 「每个开发版本一次提交」 | 现有历史天然符合 | 按 §2.5 SOP 固化；已发布历史不压缩、不改写 |
| 历史提交信息不合规 | 实查存在 | **不回改**，仅作反例留档；从下一次提交起执行 §3 |
| 提交粒度争议 | 【待确认】 | 「一个版本一次提交」与「多关注点应拆分」冲突时，默认**拆成多个版本分支**，而非合并成一个巨型提交 |
| 中文 subject 长度口径 | 【待确认】 | 默认按字符计 ≤ 50；Git Bash 下 `awk length` 按字节计，中文会偏大，仅作粗核不作硬门禁 |

---

> **相关文档**：`docs/deployment-guide.md`（编排复核与上线建议）、`docs/operations.md`（高可用与「已实现 / 只设计 / 未做」三态）、`docs/CHANGELOG.md`（版本坐标，**历史条目不回改**）。
