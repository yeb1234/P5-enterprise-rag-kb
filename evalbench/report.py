#!/usr/bin/env python3
"""汇总所有 run / 对照 / 模型探测，生成可静态打开的 Demo 展示页。"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1f2430;--muted:#6b7280;--line:#e5e7eb;
--accent:#2563eb;--good:#15803d;--bad:#b91c1c;--warn:#b45309;--chip:#eef2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:18px;margin:32px 0 12px;padding-left:10px;
border-left:4px solid var(--accent)}h3{font-size:15px;margin:18px 0 8px}
.sub{color:var(--muted);margin:0 0 18px}
.badges{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 20px}
.badge{background:var(--chip);color:#3730a3;border:1px solid #c7d2fe;border-radius:999px;
padding:3px 11px;font-size:12px}
.badge.warn{background:#fffbeb;color:var(--warn);border-color:#fde68a}
.badge.ok{background:#f0fdf4;color:var(--good);border-color:#bbf7d0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi b{display:block;font-size:24px;line-height:1.2}.kpi span{color:var(--muted);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:8px;overflow:hidden;font-size:13px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#f3f4f6;font-weight:600;color:#374151}tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:#fafafa}
.num{font-variant-numeric:tabular-nums}.up{color:var(--good);font-weight:600}
.down{color:var(--bad);font-weight:600}.flat{color:var(--muted)}
.bar{height:8px;background:#e5e7eb;border-radius:99px;overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;background:var(--accent)}
code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}
.note{color:var(--muted);font-size:12px;margin:8px 0 0}
ul{margin:6px 0 0 18px;padding:0}li{margin:4px 0}
.footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else "—"))


def load_runs() -> list[dict]:
    out = []
    for d in sorted((ROOT / "runs").glob("RUN-*")):
        s_path, c_path = d / "summary.json", d / "config_snapshot.json"
        if not (s_path.exists() and c_path.exists()):
            continue
        s = json.loads(s_path.read_text(encoding="utf-8"))
        c = json.loads(c_path.read_text(encoding="utf-8"))
        out.append({"summary": s, "config": c, "dir": d.name})
    out.sort(key=lambda x: x["config"].get("created_at", ""))
    return out


def load_comparisons() -> list[dict]:
    out = []
    for p in sorted((ROOT / "iterations" / "comparisons").glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        c.setdefault("name", p.stem)
        out.append(c)
    return out


def metric_table(runs: list[dict], final_run: dict | None) -> str:
    if not final_run:
        return "<div class='card'>尚无 live 评估结果。</div>"
    groups = final_run["summary"]["groups"]
    models = sorted({g["model"] for g in groups})
    cats = sorted({g["task_category"] for g in groups})
    dims = ("answer_accuracy", "rejection_correctness", "faithfulness",
            "format_compliance", "answer_economy")
    idx = {(g["model"], g["task_category"], g["dimension"]): g["mean"] for g in groups}
    rows = []
    for cat in cats:
        for m in models:
            cells = []
            for d in dims:
                v = idx.get((m, cat, d))
                if v is None:
                    cells.append("<td class='flat'>—</td>")
                else:
                    cells.append(
                        f"<td class='num'><div class='bar'><i style='width:{v*100:.0f}%'></i></div>{v:.3f}</td>")
            rows.append(f"<tr><td>{esc(cat)}</td><td>{esc(m)}</td>{''.join(cells)}</tr>")
    head = "".join(f"<th>{esc(d)}</th>" for d in dims)
    return (f"<table><thead><tr><th>任务类别</th><th>模型</th>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def main() -> int:
    runs = load_runs()
    comparisons = load_comparisons()
    probe_path = ROOT / "out" / "models_probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    live_runs = [r for r in runs if r["summary"].get("comparable")]
    argv = sys.argv[1:]
    final_arg = ""
    for i, a in enumerate(argv):
        if a.startswith("--final-run="):
            final_arg = a.split("=", 1)[1]
        elif a == "--final-run" and i + 1 < len(argv):
            final_arg = argv[i + 1]
    final = (next((r for r in live_runs if r["dir"] == final_arg), None)
             if final_arg else (live_runs[-1] if live_runs else None))

    total_records = sum(r["summary"].get("n_records", 0) for r in runs)
    ok_records = sum(r["summary"].get("status_counts", {}).get("ok", 0) for r in live_runs)
    models = sorted({m for r in live_runs for m in r["summary"].get("by_model", {})})
    branches = sorted({r["summary"].get("git", {}).get("branch", "?") for r in live_runs})

    # 分支 ↔ 版本 ↔ 结果 对应表
    trace_rows = []
    for r in runs:
        c, s = r["config"], r["summary"]
        g = s.get("git", {})
        pv = ",".join(c.get("prompts", {}).keys())
        pp = ",".join(c.get("params", {}).keys())
        mm = ",".join(c.get("models", {}).keys())
        trace_rows.append(
            f"<tr><td><code>{esc(r['dir'])}</code></td><td>{esc(s.get('label'))}</td>"
            f"<td><code>{esc(g.get('branch'))}</code> / <code>{esc(g.get('commit'))}</code></td>"
            f"<td>{esc(pv)}</td><td>{esc(pp)}</td><td>{esc(mm)}</td>"
            f"<td>{esc(s.get('mode'))} / {'可比' if s.get('comparable') else '占位'}</td>"
            f"<td class='num'>{esc(s.get('n_records'))}</td></tr>")
    trace = ("<table><thead><tr><th>run_id</th><th>轮次</th><th>分支 / commit</th><th>提示词版本</th>"
             "<th>参数组</th><th>模型</th><th>模式</th><th>记录数</th></tr></thead>"
             f"<tbody>{''.join(trace_rows) or '<tr><td colspan=8>暂无</td></tr>'}</tbody></table>")

    # 前后对照
    cmp_rows = []
    for c in comparisons:
        name = esc(c.get("name"))
        if c.get("kind") == "across_runs":
            d = c.get("mean_primary_delta")
            cls = "up" if (d or 0) > 0 else ("down" if (d or 0) < 0 else "flat")
            cmp_rows.append(
                f"<tr><td><code>{name}</code></td>"
                f"<td><code>{esc(c.get('baseline_run'))}</code><br><span class='note'>{esc(c.get('baseline_label'))}</span></td>"
                f"<td><code>{esc(c.get('candidate_run'))}</code><br><span class='note'>{esc(c.get('candidate_label'))}</span></td>"
                f"<td class='num {cls}'>{'+' if (d or 0)>0 else ''}{esc(d)}</td>"
                f"<td>{esc(c.get('candidate_git', {}).get('branch'))}</td></tr>")
        else:
            cmp_rows.append(
                f"<tr><td><code>{name}</code></td><td><code>{esc(c.get('run'))}</code></td>"
                f"<td>{esc(c.get('compared_by'))}: {esc(', '.join(c.get('variants', [])))}</td>"
                f"<td class='flat'>—</td><td>{esc(c.get('git', {}).get('branch'))}</td></tr>")
    cmp_table = ("<table><thead><tr><th>对照名</th><th>基线 run / 轮次</th><th>候选 run / 轮次</th>"
                 "<th>主指标均值变化</th><th>分支</th></tr></thead>"
                 f"<tbody>{''.join(cmp_rows) or '<tr><td colspan=5>暂无对照</td></tr>'}</tbody></table>")

    # 模型差异
    probe_rows = []
    for r in probe.get("results", []):
        t = r.get("test_llm") or {}
        sb = r.get("small_budget") or {}
        tp = r.get("temperature_param") or {}
        probe_rows.append(
            f"<tr><td>{esc(r.get('model'))}</td><td>{esc(r.get('role'))}</td>"
            f"<td class='num'>{esc(t.get('latency_ms'))}</td><td class='num'>{esc(t.get('content_chars'))}</td>"
            f"<td>{esc(sb.get('empty_content'))}</td><td>{esc(tp.get('supported'))}</td>"
            f"<td>{esc(r.get('conclusion'))}</td></tr>")
    probe_table = ("<table><thead><tr><th>模型</th><th>角色</th><th>探活延迟(ms)</th><th>正文字数</th>"
                   "<th>24token 空正文</th><th>接受 temp=0</th><th>处理结论</th></tr></thead>"
                   f"<tbody>{''.join(probe_rows) or '<tr><td colspan=7>暂无</td></tr>'}</tbody></table>")

    # 降级状态
    status_rows = []
    for r in live_runs:
        sc = r["summary"].get("status_counts", {})
        status_rows.append(f"<tr><td><code>{esc(r['dir'])}</code></td><td>{esc(r['summary'].get('label'))}</td>"
                           f"<td>{esc(json.dumps(sc, ensure_ascii=False))}</td></tr>")
    status_table = ("<table><thead><tr><th>run</th><th>轮次</th><th>状态分布</th></tr></thead>"
                    f"<tbody>{''.join(status_rows) or '<tr><td colspan=3>暂无</td></tr>'}</tbody></table>")

    iteration_log = ROOT / "iterations" / "iteration_log.md"
    log_html = ""
    if iteration_log.exists():
        log_html = ("<div class='card'><h3>逐轮收敛结论</h3><pre style='white-space:pre-wrap;"
                    "font:12px/1.7 inherit;margin:0'>" + esc(iteration_log.read_text(encoding="utf-8")[:6000])
                    + "</pre></div>")

    parts = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>evalbench · 评估结果与演示</title><style>", CSS, "</style></head><body><div class='wrap'>",
        "<h1>evalbench · 真实 API 评估工作台</h1>",
        "<p class='sub'>多提示词 / 多参数 / 多模型对比评估 · 结果按 run 落盘 · 每次迭代对应独立 Git 分支</p>",
        "<div class='badges'><span class='badge'>数据来源：自建样本 self_built_v1</span>",
        "<span class='badge'>框架依据：RGB / SQuAD 2.0 公开基准口径</span>",
        "<span class='badge ok'>live 结果 comparable=true</span>",
        "<span class='badge warn'>非质量基线 · 不构成准确率承诺</span></div>",
        "<div class='grid'>",
        f"<div class='kpi'><b>{len(runs)}</b><span>评估 run（含离线）</span></div>",
        f"<div class='kpi'><b>{ok_records}/{total_records}</b><span>live 有效记录 / 全部 run 记录</span></div>",
        f"<div class='kpi'><b>{len(models)}</b><span>参与评估的模型</span></div>",
        f"<div class='kpi'><b>{len(branches)}</b><span>迭代分支</span></div>",
        f"<div class='kpi'><b>{len(comparisons)}</b><span>前后对照</span></div>",
        "</div>",
        "<div class='card'><h3>收敛后的最终配置（Demo 默认）</h3><ul>"
        "<li>提示词：<code>v2_grounded</code>（grounding + 显式拒答出口 + 简短结论 + 引用编号）</li>"
        "<li>参数：<code>B</code>（<code>temperature=0</code>、<code>passage_num=5</code>、<code>noise_rate=0.6</code>、<code>max_tokens=512</code>）</li>"
        "<li>默认模型：<code>minimax-m3</code>（全维度 1.000 / 平均 1.16 s）；"
        "<code>deepseek-v4.1-flash-1017</code> 质量相同但延迟高 8.4 倍；"
        "<code>kimi-k2.6</code> 因复述 prompt 不推荐用于该配置</li>"
        "<li>最终 run：<code>" + esc(final["dir"] if final else "—") + "</code></li>"
        "</ul><p class='note'>收敛路径：v1 基线 → 提示词 v2（economy 0.25→1.00）→ 参数 B（可复现性）"
        " → 按模型适配与选型。每步的前后对照与结论见第 3 节。</p></div>",
        "<h2>1. 分支 ↔ 版本 ↔ 评估结果对应</h2>", trace,
        "<p class='note'>每个 run 的 <code>config_snapshot.json</code> 记录分支、commit、提示词/参数哈希与样本哈希，可精确追溯。</p>",
        "<h2>2. 按任务类别与评估维度分别给结论</h2>",
        f"<p class='note'>下表取最终轮 <code>{esc(final['dir'] if final else '—')}</code>；"
        "各类别主指标：事实抽取/噪声鲁棒/信息整合 → answer_accuracy，负面拒绝 → rejection_correctness。</p>",
        metric_table(runs, final),
        "<h2>3. 迭代收敛（前后对照）</h2>", cmp_table,
        "<p class='note'>采纳口径：负面拒绝的 rejection_correctness 不下降，且 answer_accuracy / faithfulness "
        "均不下降、至少一项提升。</p>", log_html,
        "<h2>4. 模型差异与兼容性适配结论</h2>", probe_table,
        "<div class='card'><h3>共性结论</h3><ul>",
        "".join(f"<li>{esc(x)}</li>" for x in probe.get("differences", [])),
        "</ul></div>",
        "<h2>5. 降级与限流留痕</h2>", status_table,
        "<p class='note'>429 分两类：rate_limit_exceeded 退避重试；quota_exceeded 不重试。"
        "失败组合标状态且不参与评分，绝不编造结果。</p>",
        "<h2>6. 边界与已知限制</h2>",
        "<div class='card'><ul>",
        "<li>样本为 12 条自建中文数据，依据公开基准任务定义构造，<b>不是</b> RGB / SQuAD 官方数据抽样，不得称「在官方基准上的分数」。</li>",
        "<li>本工作台是平行于 <code>eval/</code> 冻结占位体系的新体系；<code>eval/</code> 的 <code>__TBC__</code> / "
        "<code>NOT_COMPARABLE</code> / <code>real_api</code> 阻断口径未改动。</li>",
        "<li>字符串匹配评分对同义改写不鲁棒；未使用 LLM-as-judge，避免裁判漂移。</li>",
        "<li>离线模式 <code>comparable=false</code>，仅用于验证链路可跑，其分数不进入任何真实结论。</li>",
        "<li>未做全局并发闸/令牌桶、压测、质量基线冻结；RGB 许可为 CC BY-NC-SA 4.0，商业用途需另行授权。</li>",
        "</ul></div>",
        "<h2>7. 复跑</h2><div class='card'><pre style='white-space:pre-wrap;font:12px/1.7 inherit;margin:0'>",
        esc("python evalbench/run_eval.py --offline --label smoke --models minimax-m3 --prompts v1_baseline --params A\n"
            "python evalbench/probe_models.py            # 多模型兼容性探测\n"
            "python evalbench/run_eval.py --label <轮次> --models <模型> --prompts <版本> --params <组>\n"
            "python evalbench/compare.py --a <基线run> --b <候选run> --label <对照名>\n"
            "python evalbench/report.py --final-run <最终run>   # 重新生成展示页\n"
            "python -m unittest discover -s evalbench/tests -t ."),
        "</pre></div>",
        "<div class='footer'>evalbench · 生成于静态汇总，无外部依赖 · 事实源：evalbench/runs/、"
        "evalbench/iterations/comparisons/、evalbench/out/models_probe.json</div>",
        "</div></body></html>",
    ]
    html_text = "".join(parts)

    dashboard = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "final_run": final["dir"] if final else None,
        "n_runs": len(runs), "live_runs": [r["dir"] for r in live_runs],
        "models": models, "branches": branches,
        "comparisons": [{"name": c.get("name"), "kind": c.get("kind"),
                         "mean_primary_delta": c.get("mean_primary_delta")} for c in comparisons],
        "by_model_final": final["summary"].get("by_model") if final else None,
    }
    (ROOT / "out" / "dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "out" / "index.html").write_text(html_text, encoding="utf-8")
    print(json.dumps({
        "html": "evalbench/out/index.html",
        "dashboard": "evalbench/out/dashboard.json",
        "runs": len(runs), "live_runs": len(live_runs), "final_run": dashboard["final_run"],
        "comparisons": len(comparisons),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
