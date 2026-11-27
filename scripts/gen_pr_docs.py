"""从真实 git 历史生成 PR 描述（可复跑）。

为什么生成而不是手写：diffstat、文件清单、提交摘要都来自 `git diff`，
手写容易和实际不一致。PR 的「验证结论」「风险」是人工判断，写在 PRS 表里。

复跑：
    python scripts/gen_pr_docs.py
输出：
    docs/pr/PR-1.md … PR-6.md
    docs/pr/README.md（索引）
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "pr"

# (序号, 分支, base, head, 标题, 验证结论, 已知风险/边界)
PRS = (
    (1, "feature/p0-security-fix", "74fc9c4", "38cde1e",
     "修 P0 密钥漏检与 QA 5 条缺陷，补项目演进页与容器化",
     "33/33 e2e `DEMO_READY`（报告按字节还原）；31/31 集成；guardrails 33 项（新增 6 项密钥形态）；"
     "5 种真实密钥形态实测全拦、零回显",
     "A5 落盘前兜底扫描会把文档里自带的 `sk-…` 示例串也阻断，属「宁可阻断不可漏检」的有意取舍"),
    (2, "feature/nav-and-containers", "38cde1e", "3a783e8",
     "完整功能首页与解析工作台双向互链，补 webapp 镜像与一键启动",
     "两个容器 `healthy`、8765/8767 均 200；33/33 e2e；双向链接用 curl 抓真实 HTML 与 A1 JSON 核对",
     "Docker 29 + Compose v5 的 bake 在中文路径下报 gRPC 头编码错误，故用 `docker build` + `docker run` 绕开"),
    (3, "feature/queue-observability", "3a783e8", "402bade",
     "全局队列可观测性与权限边界；修 system/busy 无鉴权",
     "8 项队列权限测试；109 单测；33/33 e2e；真实 100 条积压下普通用户 `details=0`、响应不含文件名与邮箱",
     "真值取 `documents.status`，不是内存队列；`department` 只是展示标签，不参与权限判定"),
    (4, "feature/queue-ui", "402bade", "2206a3b",
     "任务中心改为按用户/按部门积压聚合，短码可区分",
     "109 单测；33/33 e2e；容器内双账号实测（研发部 100 / 交付部 30，短码 `qu***_a` / `qu***_b`）",
     "演示种子会被启动自检逐条消费，需演示前重新 `--seed`"),
    (5, "feature/gateway-hardening", "2206a3b", "66d5c24",
     "多用户网关加固五项 + 腾讯云指导页",
     "127 单测（含 18 项加固）；33/33 e2e；容器内实测 413 `PAYLOAD_TOO_LARGE`、"
     "429 `RATE_LIMIT_IP_EXCEEDED`、并发闸 503 `SERVER_BUSY`、伪造 XFF 不换限流桶、A 超支不影响 B",
     "限流是单机内存窗口，多副本需换 Redis 或云网关；`backend/` 骨架仍只有预留，未接并发闸"),
    (6, "feature/case-study", "66d5c24", "8ac4224",
     "14 个真实缺陷案例集 + 图文教学页 + PR 记录",
     "`cases.html`（个人材料，已移出仓库） HTMLParser 配对无异常、零外部资源、5 锚点、14 张案例卡；"
     "MD 与页面内容一致；走 `--no-ff` 合并生成真实 merge commit",
     "案例均为规则级/演示级修复，不含真实压测与 WAF 规则标定"),
)

CHECKLIST = """## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」
"""


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for no, branch, base, head, title, verify, risk in PRS:
        stat = run("git", "diff", "--stat", f"{base}..{head}")
        files = run("git", "diff", "--name-status", f"{base}..{head}")
        body = run("git", "show", "-s", "--format=%B", head)
        short = run("git", "rev-parse", "--short", head)

        lines = body.splitlines()
        subject = lines[0] if lines else title
        bullets = [x for x in lines[1:] if x.strip().startswith("-")]

        doc = [
            f"# PR #{no} · {title}",
            "",
            f"| 项 | 值 |",
            f"|---|---|",
            f"| 分支 | `{branch}` → `demo/v1` |",
            f"| 提交 | `{short}` |",
            f"| 基线 | `{base}` |",
            f"| 合并方式 | `--no-ff`（保留合并提交） |",
            "",
            "## 背景",
            "",
            subject,
            "",
        ]
        if bullets:
            doc += ["## 改动", ""] + [b.replace("- ", "- ", 1) for b in bullets] + [""]
        doc += ["## 变更规模", "", "```", stat, "```", "", "<details><summary>文件清单</summary>", "", "```",
                files, "```", "", "</details>", ""]
        doc += ["## 验证", "", verify, "", "## 风险与边界", "", risk, "", CHECKLIST]
        (OUT / f"PR-{no}.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
        rows.append((no, branch, short, title, len(files.splitlines())))

    index = ["# PR 索引（本地 origin：`.data/remote.git`）", "",
             "> 这些描述由 `scripts/gen_pr_docs.py` 从真实 git 历史生成，diffstat 与文件清单不是手写。",
             "> 推送与建 PR 的远端操作用 `scripts/push-to-github.sh`（需要你的仓库与凭证）。", "",
             "| PR | 分支 | 提交 | 标题 | 文件数 |", "|---|---|---|---|---|"]
    for no, branch, short, title, n in rows:
        index.append(f"| [#{no}](PR-{no}.md) | `{branch}` | `{short}` | {title} | {n} |")
    index += ["", "## 状态", "",
              "全部 6 个 PR 已在本地 `origin` 推送（`git branch -r` 可见），"
              "其中 #6 已 `--no-ff` 合并进 `demo/v1`；#1–#5 为交付点快照分支。", ""]
    (OUT / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"已生成 {len(rows)} 份 PR 描述 + 索引：{OUT}")


if __name__ == "__main__":
    main()
