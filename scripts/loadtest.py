#!/usr/bin/env python3
"""极简压测器：纯标准库（threading + http.client），不依赖 locust / k6。

用途：给 `webapp/` 演示切片做「能扛多少 / 瓶颈在哪 / 阈值该定多少」的初筛。
它只测**应用层网关**（限流 / 并发闸 / 请求体上限）与目标端点自身的耗时，
不测数据库性能、不测真实模型链路、不代表生产容量。

⚠️ 关键前提（务必读）：
`webapp/main.py::gateway_guard` 的限流在**鉴权之前**执行，所以未登录访问
`/api/docs` 得到的 401 仍然**走完了限流与并发闸**。因此本脚本把
`401 / 403` 计入「成功响应」（链路正常、只是没登录），压测依然有效。
只有 `429`（限流命中）、`503`（并发闸打满）、连接错误才是真正需要关注的信号。

用法：
    python scripts/loadtest.py                                  # 默认 GET /api/docs，10 并发 10 秒
    python scripts/loadtest.py --concurrency 4 --duration 3     # 小并发基线
    python scripts/loadtest.py --qps 5 --duration 10            # 限速 5 QPS，避免触发 429
    python scripts/loadtest.py --token "$TOKEN"                 # 带 Bearer
    python scripts/loadtest.py --json results/lt1.json          # 结构化输出，便于前后对比
    python scripts/loadtest.py --method POST --path /api/chat/stream \
        --body '{"question":"配网步骤"}' --sse --token "$TOKEN"   # SSE：TTFT 与总时长分开记

三态：**已实现**（本脚本）。真实容量结论 **未做**——本机没有压测环境、
没有真实模型链路、没有生产级硬件；脚本给出的数字只能在你自己机器上横向对比。
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import sys
import threading
import time
from collections import Counter
from urllib.parse import urlsplit

# 这些状态码说明「链路正常、应用层有应答」，计入成功（限流在鉴权前，401 也压到了网关）
OK_STATUSES = frozenset({200, 201, 202, 204, 301, 302, 304, 400, 401, 403, 404, 405, 409, 422})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="极简 HTTP 压测器（标准库实现）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--url", default="http://127.0.0.1:8765", help="目标服务根地址")
    p.add_argument("--path", default="/api/docs", help="压测路径（含 query 亦可）")
    p.add_argument("--method", default="GET", help="HTTP 方法")
    p.add_argument("--body", default=None, help="请求体字符串（POST 用，可 JSON）")
    p.add_argument("--token", default=None, help="Bearer token（不带则匿名，401 仍算成功）")
    p.add_argument("--concurrency", type=int, default=10, help="并发线程数")
    p.add_argument("--duration", type=float, default=10.0, help="持续秒数")
    p.add_argument("--qps", type=float, default=0.0, help="全局限速（0=不限速）")
    p.add_argument("--timeout", type=float, default=10.0, help="单请求超时秒数")
    p.add_argument("--sse", action="store_true", help="按 SSE 读流：另记首 token 时间（TTFT）")
    p.add_argument("--json", dest="json_out", default=None, help="把结果写入 JSON 文件")
    p.add_argument("--label", default="", help="本次压测标签，便于对比")
    return p.parse_args(argv)


def percentile(values: list[float], p: float) -> float | None:
    """nearest-rank 百分位；空集合返回 None（不编造 0）。"""
    if not values:
        return None
    ordered = sorted(values)
    k = int(math.ceil(p / 100.0 * len(ordered))) - 1
    return ordered[max(0, min(k, len(ordered) - 1))]


def make_slot_limiter(qps: float):
    """返回 acquire()：按全局 QPS 上限分配发送时刻（不忙等）。"""
    if qps <= 0:
        return lambda: None
    interval = 1.0 / qps
    lock = threading.Lock()
    next_slot = [time.perf_counter()]

    def acquire() -> None:
        with lock:
            now = time.perf_counter()
            if next_slot[0] < now:
                next_slot[0] = now
            wait = next_slot[0] - now
            next_slot[0] += interval
        if wait > 0:
            time.sleep(wait)

    return acquire


def worker(index: int, args: argparse.Namespace, stop: threading.Event,
           acquire_slot, shared: dict, schemeless_path: str,
           host: str, port: int, use_tls: bool) -> None:
    """一个压测线程：自己维护一条 keep-alive 连接，本地统计后合并。"""
    conn_cls = http.client.HTTPSConnection if use_tls else http.client.HTTPConnection
    headers = {"User-Agent": "kb-loadtest/1.0", "Accept": "*/*",
               "Connection": "keep-alive"}
    if args.token:
        headers["Authorization"] = "Bearer " + args.token
    if args.body is not None:
        headers["Content-Type"] = "application/json"
    body = args.body.encode("utf-8") if args.body is not None else None

    local = {"total": 0, "ok": 0, "c429": 0, "c503": 0, "c4xx": 0, "c5xx": 0,
             "errors": 0, "lat": [], "ttft": [], "r429": Counter(), "b503": Counter()}
    conn = None
    try:
        while not stop.is_set():
            acquire_slot()
            if stop.is_set():
                break
            if conn is None:
                conn = conn_cls(host, port, timeout=args.timeout)
            started = time.perf_counter()
            status = 0
            ttft = None
            try:
                conn.request(args.method, schemeless_path, body=body, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                if args.sse:
                    # 逐块读，记录首字节时间与首个 data: 行时间；读完整个流才算一次完成
                    first_byte = None
                    saw_data = None
                    while True:
                        chunk = resp.read(2048)
                        if not chunk:
                            break
                        if first_byte is None:
                            first_byte = time.perf_counter()
                        if saw_data is None and b"data:" in chunk:
                            saw_data = time.perf_counter()
                    ttft = (saw_data or first_byte or time.perf_counter()) - started
                else:
                    resp.read()
                elapsed = time.perf_counter() - started
                local["total"] += 1
                local["lat"].append(elapsed)
                if ttft is not None:
                    local["ttft"].append(ttft)
                if status == 429:
                    local["c429"] += 1
                    dim = resp.getheader("X-RateLimit-Dimension") or "unknown"
                    local["r429"][f"{dim}|{resp.getheader('X-RateLimit-Limit')}"
                                   f"|{resp.getheader('X-RateLimit-Window')}"] += 1
                elif status == 503:
                    local["c503"] += 1
                    try:
                        kind = json.loads(resp.read() or b"{}").get("kind", "unknown")
                    except (ValueError, TypeError):
                        kind = "unknown"
                    local["b503"][str(kind)] += 1
                elif status in OK_STATUSES:
                    local["ok"] += 1
                elif 400 <= status < 500:
                    local["c4xx"] += 1
                else:
                    local["c5xx"] += 1
            except (http.client.HTTPException, OSError):
                local["errors"] += 1
                try:
                    if conn is not None:
                        conn.close()
                finally:
                    conn = None
    finally:
        if conn is not None:
            conn.close()
        with shared["lock"]:
            for key in ("total", "ok", "c429", "c503", "c4xx", "c5xx", "errors"):
                shared[key] += local[key]
            shared["lat"].extend(local["lat"])
            shared["ttft"].extend(local["ttft"])
            shared["r429"].update(local["r429"])
            shared["b503"].update(local["b503"])
            shared["done_threads"] += 1


def verdict_of(shared: dict, elapsed: float) -> tuple[str, str]:
    """返回 (结论短标签, 一句话解释)。只用实测值，不预设数字。"""
    total = max(1, shared["total"] + shared["errors"])
    rate_429 = shared["c429"] / total
    rate_503 = shared["c503"] / total
    ok_qps = shared["ok"] / elapsed if elapsed > 0 else 0.0

    if shared["errors"] == total and total > 1:
        return "CONNECTION_FAILED", "全部请求连接失败：目标服务未启动或地址/端口不对，本次数据无效"

    if shared["c503"] > 0 and rate_503 >= 0.05:
        kinds = "、".join(f"{k}×{v}" for k, v in shared["b503"].most_common(3)) or "unknown"
        return ("SERVER_BUSY",
                f"触发 503（{kinds}）：并发闸是当前瓶颈。请降低并发或提高 WEBAPP_MAX_INFLIGHT，"
                f"并观察 P99 是否随并发线性恶化；成功 QPS 约 {ok_qps:.1f}")

    if shared["c429"] > 0:
        top = shared["r429"].most_common(1)[0]
        dim, limit, window = (top[0].split("|") + ["?", "?"])[:3]
        try:
            cap = float(limit) / float(window) if window not in ("?", "", None) else None
        except (TypeError, ValueError):
            cap = None
        cap_txt = f"，该维度单 key 上限约 {cap:.1f} QPS" if cap else ""
        return ("RATE_LIMITED",
                f"在 {shared['concurrency']} 并发触发 429（{dim} 维度 {limit} 次 / {window}s{cap_txt}）；"
                f"成功 QPS 约 {ok_qps:.1f}，429 占比 {rate_429:.1%}")

    return ("NO_LIMIT_HIT",
            f"未触发限流，可继续加压（成功 QPS 约 {ok_qps:.1f}，"
            f"P99 {fmt_ms(percentile(shared['lat'], 99))}）")


def fmt_ms(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds * 1000:.1f} ms"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.concurrency < 1:
        print("--concurrency 必须 >= 1", file=sys.stderr)
        return 2
    if args.duration <= 0:
        print("--duration 必须 > 0", file=sys.stderr)
        return 2

    parts = urlsplit(args.url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        print(f"--url 非法：{args.url}（示例 http://127.0.0.1:8765）", file=sys.stderr)
        return 2
    use_tls = parts.scheme == "https"
    host = parts.hostname
    port = parts.port or (443 if use_tls else 80)
    base_path = parts.path.rstrip("/")
    schemeless = base_path + args.path

    shared = {"total": 0, "ok": 0, "c429": 0, "c503": 0, "c4xx": 0, "c5xx": 0,
              "errors": 0, "lat": [], "ttft": [], "r429": Counter(), "b503": Counter(),
              "lock": threading.Lock(), "done_threads": 0, "concurrency": args.concurrency}
    stop = threading.Event()
    acquire_slot = make_slot_limiter(args.qps)

    print(f"[loadtest] {args.method} {args.url.rstrip('/')}{args.path}"
          f"  并发={args.concurrency} 时长={args.duration}s"
          f"{f' 限速={args.qps:g} QPS' if args.qps > 0 else ' 限速=off'}"
          f"{'  Bearer=on' if args.token else '  匿名（401 仍计入成功）'}"
          f"{'  SSE 读流' if args.sse else ''}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True, name=f"lt-{i}",
                                args=(i, args, stop, acquire_slot, shared, schemeless,
                                      host, port, use_tls))
               for i in range(args.concurrency)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(args.duration)
    stop.set()
    for t in threads:
        t.join(timeout=max(1.0, args.timeout + 1.0))
    elapsed = max(1e-6, time.perf_counter() - wall_start)

    total = shared["total"] + shared["errors"]
    ok_qps = shared["ok"] / elapsed
    total_qps = total / elapsed
    p50, p95, p99 = (percentile(shared["lat"], p) for p in (50, 95, 99))
    verdict, reason = verdict_of(shared, elapsed)

    r429_detail = [{"dimension": k.split("|")[0], "limit": k.split("|")[1],
                    "window_s": k.split("|")[2], "count": v}
                   for k, v in shared["r429"].most_common()]
    b503_detail = [{"kind": k, "count": v} for k, v in shared["b503"].most_common()]

    print("")
    print(f"{'指标':<22}{'值'}")
    print("-" * 58)
    print(f"{'总请求':<22}{total}")
    print(f"{'成功(含 401/403)':<20}{shared['ok']}")
    print(f"{'429 限流':<20}{shared['c429']}")
    print(f"{'503 并发闸':<19}{shared['c503']}")
    print(f"{'4xx(其他)':<21}{shared['c4xx']}")
    print(f"{'5xx':<22}{shared['c5xx']}")
    print(f"{'连接错误':<21}{shared['errors']}")
    print("-" * 58)
    print(f"{'实际总 QPS':<20}{total_qps:.1f}")
    print(f"{'成功 QPS':<21}{ok_qps:.1f}")
    print(f"{'P50 / P95 / P99':<19}{fmt_ms(p50)} / {fmt_ms(p95)} / {fmt_ms(p99)}")
    if args.sse:
        print(f"{'TTFT P50 / P95':<18}{fmt_ms(percentile(shared['ttft'], 50))} / "
              f"{fmt_ms(percentile(shared['ttft'], 95))}")
    if r429_detail:
        print(f"{'429 命中维度':<19}{r429_detail}")
    if b503_detail:
        print(f"{'503 类型':<21}{b503_detail}")
    print("-" * 58)
    print(f"结论 [{verdict}]: {reason}")
    if args.duration < 60 and shared["c429"] > 0:
        print("[提示] 本次时长 < 限流窗口（默认 60s）：窗口允许一次性打完限额，"
              "短时爆发会高估持续容量；测稳态请用 --duration 70 复测。")

    if args.json_out:
        payload = {
            "label": args.label,
            "config": {"url": args.url, "path": args.path, "method": args.method,
                       "concurrency": args.concurrency, "duration_s": args.duration,
                       "qps_limit": args.qps, "auth": bool(args.token), "sse": args.sse},
            "elapsed_s": round(elapsed, 3),
            "counts": {k: shared[k] for k in
                       ("total", "ok", "c429", "c503", "c4xx", "c5xx", "errors")},
            "qps": {"total": round(total_qps, 2), "ok": round(ok_qps, 2)},
            "latency_ms": {"p50": None if p50 is None else round(p50 * 1000, 2),
                           "p95": None if p95 is None else round(p95 * 1000, 2),
                           "p99": None if p99 is None else round(p99 * 1000, 2)},
            "ttft_ms": None if not args.sse else {
                "p50": round((percentile(shared["ttft"], 50) or 0) * 1000, 2),
                "p95": round((percentile(shared["ttft"], 95) or 0) * 1000, 2)},
            "rate_429": r429_detail,
            "busy_503": b503_detail,
            "verdict": verdict,
            "verdict_reason": reason,
            "note": "演示机实测值，仅供横向对比；不是容量结论，不是生产基线",
        }
        try:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            print(f"[loadtest] 已写入 {args.json_out}")
        except OSError as exc:
            print(f"[loadtest] 写 JSON 失败：{exc}", file=sys.stderr)
            return 1

    if verdict == "CONNECTION_FAILED":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
