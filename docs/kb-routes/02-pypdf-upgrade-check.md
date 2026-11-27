# pypdf 5.9 → 6.21 升级可行性验证（2026-11-27）

**结论：好升。可以升，且已经把依赖声明改好、在实验分支 `experiment/pypdf6-neo4j-graphrag` 上跑通。**
这次只验证升级可行性，**没有合并回 `feature/graph-rag-wiki`**——要不要合，由主理人定。

## 为什么要升

`neo4j-graphrag` 1.24.0 的核心依赖里写死了 `pypdf>=6.19`，
本机和原 Dockerfile 里是 pypdf 5.9.0（backend 还钉了 `<6.0`），两套依赖打架。
这是上一轮没有安装 `neo4j-graphrag` 包的唯一原因。

## 升了什么

| 位置 | 原值 | 新值 |
|---|---|---|
| `backend/requirements.txt` | `pypdf>=5.0,<6.0` | `pypdf>=6.19,<7.0` |
| `webapp/Dockerfile` | `pypdf>=4.0` | `pypdf>=6.19,<7.0` |
| `showcase/Dockerfile` | `pypdf>=4.0` | 未动（站点不依赖 neo4j-graphrag，保持宽松） |

## 兼容性面（为什么风险小）

项目里 pypdf 的使用只有 **`PdfReader` + `reader.pages[i].extract_text()`** 这一个组合，
三处：`parsing/router.py:142`、`webapp/rag.py:64`、`backend/app/services/parser.py:67/81`。
**没有用 `PdfWriter`、没有用被 6.x 改动/弃用的 API**（`add_js`、`remove_images` 布尔参数、
`parse_filename_page_ranges`、`PageRange` 零步长之类一个都没碰）。

pypdf 6.x / 7.x 的变更集中在安全限制、图片/字体/加密、Writer API，
和不带参数的 `extract_text()` 基本无关。

## 实测证据（不是推断）

1. **隔离环境**：`python -m venv --system-site-packages` + 装 `pypdf==6.21.4`、
   `neo4j-graphrag==1.24.0`；主环境保持 5.9.0 不受影响。
2. **真实 PDF**：拿库里现成的 `08-技术规范.pdf` 与 `simple.pdf` 逐页 `extract_text()`，
   两页都抽出非空文本，无异常。
3. **API 导入**：`webapp.rag._pdf_text`、`parsing.ParseRouter` 在 pypdf 6 下导入正常。
4. **全量单测**：`405 passed`（含解析、webapp、三路取证）；`backend` `26 passed`。
5. **neo4j-graphrag 包本身**：`Text2CypherRetriever`、`VectorRetriever`、`HybridRetriever`、
   `SimpleKGPipeline`、`LLMInterface` 全部 import 成功——包是真能用，不是只装上了。
6. **Docker 镜像**：用改后的 Dockerfile 实建 `kb-webapp:pypdf6-test`，
   镜像内 `pypdf 6.21.4 / neo4j 6.4.1`；替换运行中的容器后：
   e2e **33/33 `DEMO_READY`**、`verify_infra` **`INFRA_READY`**、`8765 -> 200`。

## 没做的事 / 边界

- **没有把 `neo4j-graphrag` 接进 `kgraph/` 代码**。这次只验证「升级可行 + 包可用」；
  真正接入会把现在的自写 Text2Cypher 换成它的 `Text2CypherRetriever`，是另一笔改动。
- **没有升 `showcase/Dockerfile`**：它只是演示站点，用不到图谱依赖，保持 `pypdf>=4.0`。
- 7.x 没试：7.0 有 `Cleanup deprecations for the next major release`，
  没必要为了一个包去追大版本，`<7.0` 是刻意的上界。

## 回到那个选择

现在两条路都通了：

- **A（当前主线）**：官方 neo4j 驱动 + 自写 Text2Cypher，pypdf 保持 5.9.0；
- **B（本实验分支）**：升级 pypdf 6.19+，具备安装 `neo4j-graphrag` 的条件。

差别不在"能不能",在于要不要引入第三方 retriever 抽象——A 可控、代码自己写得清；
B 少写一些代码、但多一层框架依赖和 schema 约束。你定。
