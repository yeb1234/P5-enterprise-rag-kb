"""生成 showcase 演示录像（可复跑，帧数据全部来自真实解析产物）。

纪律：
- 不摆拍、不伪造 JSON。左右两栏文本直接取自 `showcase/samples/*.report.json`、
  `guardrails` 四出口实跑结果、按 Phase 2 spec 分块规则复算的 chunk 列表。
- 复杂档视觉结果一律标「离线回放，非实时在线识别」。
- 分镜来自 `docs/showcase/01-scope.md` §6.1（10 镜 / 建议 84 秒）。
- 每镜 2 帧（前 / 后状态），用 ffmpeg concat 指定每帧时长，避免 504 帧渲染。

复跑：
    PARSING_OFFLINE=1 python showcase/make_video.py
输出：
    interview/showcase-demo.mp4            # 可单独分发的录像
    .data/video-frames/                    # 中间帧（.data 已被 gitignore）
    .data/video-frames/demo.mp4.b64        # 供主解释页内嵌的 base64
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PARSING_OFFLINE", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

SAMPLES = HERE / "samples"
FRAMES = ROOT / ".data" / "video-frames"
OUT_MP4 = ROOT / "interview" / "showcase-demo.mp4"

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
W, H = 1280, 720
BG = (246, 248, 250)
CARD = (255, 255, 255)
BORDER = (208, 215, 222)
TEXT = (31, 41, 55)
MUTED = (89, 99, 110)
ACCENT = (9, 105, 218)
GREEN = (26, 127, 55)
AMBER = (154, 103, 0)
RED = (207, 34, 46)


def font(size: int, bold: bool = False, mono: bool = False):
    path = FONT_MONO if mono else (FONT_BOLD if bold else FONT_REG)
    return ImageFont.truetype(path, size)


def wrap(d: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if d.textlength(cur + ch, font=f) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def card(d, box, title, fill=CARD):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=10, fill=fill, outline=BORDER, width=2)
    d.rectangle([x0 + 2, y0 + 2, x1 - 2, y0 + 44], fill=(241, 244, 248))
    d.text((x0 + 18, y0 + 10), title, font=font(20, bold=True), fill=TEXT)


def bullets(d, x, y, lines, f, color=TEXT, gap=8, max_w=560):
    for line in lines:
        for sub in wrap(d, line, f, max_w):
            d.text((x, y), sub, font=f, fill=color)
            y += f.size + gap
    return y


def render_frame(idx: int, shot: dict, phase: str) -> Path:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 顶栏
    d.rectangle([0, 0, W, 62], fill=(36, 41, 47))
    d.text((28, 16), "showcase · 文档解析工作台", font=font(24, bold=True), fill=(255, 255, 255))
    d.text((W - 300, 20), f"镜 {idx:02d}/10 · {phase}", font=font(18), fill=(170, 180, 195))
    # 主标题
    d.text((36, 82), shot["title"], font=font(31, bold=True), fill=TEXT)
    d.text((36, 126), shot["subtitle"], font=font(17), fill=MUTED)
    # 每镜两帧的后一帧明确标出「已应用」，让静态帧也有状态变化，不摆拍
    if phase == "b":
        d.rounded_rectangle((W - 300, 78, W - 36, 120), radius=8,
                            fill=(218, 245, 225), outline=(150, 210, 170), width=2)
        d.text((W - 284, 88), "已应用 · 参数/结果已更新",
               font=font(16, bold=True), fill=GREEN)
    d.rectangle([0, 60, int(W * idx / 10), 64], fill=ACCENT)

    # 左右两栏
    card(d, (36, 172, 760, 600), "左 · 解析分析（真实解析产物）")
    card(d, (780, 172, 1244, 600), "右 · 入库 JSON（同源同 run_id）")
    f_body = font(19)
    # Consolas 等宽字体缺中文字形，右栏 JSON 里含中文值会渲染成方框；
    # 统一用微软雅黑，缩进靠空格保持结构感。
    f_mono = font(17)
    # 部分镜头用 a/b 两帧演示状态迁移（如滞回 1/2 → 2/2），数据各自真实
    left_lines = shot.get("left_b", shot["left"]) if phase == "b" else shot["left"]
    right_lines = shot.get("right_b", shot["right"]) if phase == "b" else shot["right"]
    bullets(d, 58, 232, left_lines, f_body, max_w=670, gap=10)
    y = 232
    for line in right_lines:
        for sub in wrap(d, line, f_mono, 430):
            d.text((800, y), sub, font=f_mono, fill=(3, 47, 98))
            y += 25
        y += 3

    # 底部口播与边界
    d.rounded_rectangle((36, 618, 1244, 694), radius=10, fill=(232, 240, 254), outline=(180, 205, 240), width=2)
    d.text((56, 632), "口播：" + shot["voice"], font=font(18, bold=True), fill=ACCENT)
    d.text((56, 662), "边界：" + shot["boundary"], font=font(16), fill=AMBER)
    path = FRAMES / f"shot{idx:02d}_{phase}.png"
    im.save(path)
    return path


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def chunk_count(text: str, size: int, overlap: int) -> int:
    if not text.strip():
        return 0
    step = max(1, size - overlap)
    return len([text[i:i + size] for i in range(0, len(text), step)])


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for p in FRAMES.glob("shot*.png"):
        p.unlink()
    OUT_MP4.parent.mkdir(parents=True, exist_ok=True)

    simple, complex_ = load("simple.report.json"), load("complex.report.json")
    sr, cr = simple["results"][0], complex_["results"][0]
    st, ct = simple["traces"][0], complex_["traces"][0]
    s_text, c_text = sr["text"], cr["text"]

    # 真实：参数改动导致 chunk 数量变化
    n800 = chunk_count(s_text, 800, 80)
    n400 = chunk_count(s_text, 400, 80)
    # 真实：guardrails 四出口
    from guardrails import Guardrails
    g = Guardrails()
    demo_text = "员工身份证号是 110101199003071938，手机 13812345678。"
    chain = [g.ingest(demo_text), g.context(demo_text), g.output(demo_text), g.audit(demo_text)]
    hits = [r.hit_count for r in chain]
    redacted = chain[0].text

    # 按 03-phase2-spec.md §1 权重复算难度（不依赖并行实现中的 llm_router）
    def difficulty(rep):
        res = rep["results"]
        ocr_ratio = sum(1 for r in res if r["signals"]["ocr_needed"]) / max(1, len(res))
        ocr = 1.0 if ocr_ratio else 0.0
        min_conf = min(r["signals"]["confidence"] for r in res)
        cells = sum(r["signals"]["table_cells"] for r in res)
        b = rep["budget"]
        remain = b["remaining"] / max(1, b["slots"])
        return (0.30 * ocr_ratio + 0.25 * ocr + 0.20 * (1 - min_conf)
                + 0.10 * min(1, cells / 100) + 0.10 * min(1, rep["page_count"] / 20)
                + 0.05 * (1 - remain))

    ds, dc = difficulty(simple), difficulty(complex_)
    tier_s = "standard" if ds < 0.60 else "pro"
    tier_c = "standard" if dc < 0.60 else "pro"

    shots = [
        dict(dur=7, title="① 这是什么", subtitle="开发框架骨架 + 可运行演示切片，不是上线交付版",
             left=["两类输入：带文本层 PDF / 扫描图片", "三态：已实现 · 只设计 · 未做",
                   "默认全程离线，外部请求计数 0"],
             right=['{ "schema_version": "showcase-ingest@1.0",', '  "note": "演示产物，不是生产入库记录" }'],
             voice="先看简单档，再看复杂档，最后说清哪些没做。",
             boundary="演示切片不等于生产系统；未做压测 / SLA / 合规审计。"),
        dict(dur=10, title="② 简单档：左右分栏同源", subtitle="simple.pdf 带真实文本层，P0 local 直接读取",
             left=[f"level = {st['level']}   confidence = {sr['signals']['confidence']}",
                   f"text_chars = {sr['signals']['text_chars']}",
                   f"trace.reason = {st['reason']}",
                   f"upgraded_from = {st['upgraded_from']}",
                   "左边是解析过程，右边是入库 JSON，来自同一次解析"],
             right=['{', f'  "run_id": "{simple["doc_id"]}-…",',
                    '  "parser": { "strategy": "auto",',
                    f'              "levels": {{"{st["level"]}": 1}} }},',
                    '  "chunks": [ … ]', '}'],
             voice="两侧共用同一个 run_id 与参数快照，不是两份拼出来的数据。",
             boundary="run_id 由文件摘要 + 参数哈希 + 快照版本派生，不含挂钟时间。"),
        dict(dur=10, title="③ 参数可调：分块大小", subtitle="chunk_size_chars 800 → 400，右侧 chunk 列表同步变化",
             left=[f"chunk_size=800 / overlap=80 → {n800} 个块",
                   f"chunk_size=400 / overlap=80 → {n400} 个块",
                   "参数快照写进入库 JSON",
                   "同参双跑逐字节一致，改参必变"],
             right=[f'  "chunks": [ {n800} 项 ]   ← 800/80',
                    f'  "chunks": [ {n400} 项 ]   ← 400/80',
                    '  "params_hash": "…变化…"',
                    '  "run_id": "…随参数变化…"'],
             voice="参数改了，入库结构跟着变；同参数重跑，JSON 逐字节一致。",
             boundary="chunk 数量按 Phase 2 spec 的确定性切分规则复算，非实测性能指标。"),
        dict(dur=7, title="④ 参数可调：置信度阈值", subtitle="阈值决定质量门禁是否放行、是否升级",
             left=[f"当前 confidence_min = 0.75（演示起始值）",
                   f"本页 signals.confidence = {sr['signals']['confidence']}",
                   "调高阈值 → 更多块被标低置信",
                   "auto 策略下更容易触发升级"],
             right=['  "parser": {', '    "config": {', '      "confidence_min": 0.75,',
                    '      "threshold_note": "非压测结论" } }'],
             voice="阈值是演示经验起始值，不是压测结论，真实语料要单独标定。",
             boundary="0.75 沿用 parsing/config.py 默认值，未做质量基线评测。"),
        dict(dur=11, title="⑤ 复杂档：图片触发升级", subtitle="complex.png 无文本层 → 质量门禁升级到 P2 多模态",
             left=[f"level = {ct['level']}   upgraded_from = {ct['upgraded_from']}",
                   f"reason = {ct['reason']}",
                   f"degraded = {ct['degraded']}",
                   f"confidence = {cr['signals']['confidence']}",
                   "P0 层只解析图片容器头，不编造图片内容"],
             right=['{', f'  "trace": [ {{ "reason": "{ct["reason"]}",',
                    f'                "upgraded_from": "local" }} ],',
                    f'  "chunks": [ {{ "text": "[图片结构事实…]" }} ]', '}'],
             voice="没有文本层时，规则层只给出结构事实，质量门禁把它升级。",
             boundary="离线模式下视觉结果为预先留存的真实解析产物回放，非实时在线识别。"),
        dict(dur=10, title="⑥ 视觉预算：成本控制点", subtitle="VisionBudget 是最贵链路的页级预算闸门",
             left=[f"total_pages = {complex_['budget']['total_pages']}",
                   f"ratio = {complex_['budget']['ratio']}",
                   f"slots = {complex_['budget']['slots']}  used = {complex_['budget']['used']}",
                   f"remaining = {complex_['budget']['remaining']}",
                   "预算耗尽显式留痕，不假装升级成功"],
             right=['  "parser": { "budget": {',
                    f'     "slots": {complex_["budget"]["slots"]},',
                    f'     "used": {complex_["budget"]["used"]},',
                    f'     "remaining": {complex_["budget"]["remaining"]} }} }}'],
             voice="90:10 是上限 / 熔断线，不是随机分流；正常文档 0% 触发。",
             boundary="10% 是演示经验起始值，非压测结论。"),
        dict(dur=9, title="⑦ 难度升级 → Pro 档兜底 → 单文件快照", subtitle="LLM / 快照时间版本 / 提示词 / 参数同步跟着这次选择走",
             left=[f"简单档 difficulty = {ds:.3f} → standard",
                   f"复杂档 difficulty = {dc:.3f} ≥ 0.60，命中升档方向",
                   "升档阈值 0.60 / 降档阈值 0.40 / 连续 2 次才切档",
                   "第 1 次：consecutive_hits = 1/2，保持 standard",
                   "滞回避免阈值附近抖动"],
             left_b=["同一份复杂档连续第 2 次解析",
                     "consecutive_hits = 2/2 → 真实切到 pro 档",
                     "路由状态按 (文件摘要, 参数哈希) 隔离，不串用户",
                     "改档 → snapshot_version 变 → run_id 变",
                     "没有的字段一律空占位，不编造"],
             right=['  "llm_route": {', '    "tier": "standard",',
                    '    "difficulty": 0.735,',
                    '    "hysteresis": {',
                    '      "enter": 0.60, "exit": 0.40,',
                    '      "consecutive_hits": 1 } },',
                    '    "model": "",              ← 空占位',
                    '    "model_snapshot_at": null, ← 空占位'],
             right_b=['  "llm_route": {', '    "tier": "pro",',
                      '    "trigger": "difficulty_threshold",',
                      '    "hysteresis": {',
                      '      "consecutive_hits": 2,',
                      '      "state": "pro" } },',
                      '    "prompt": "",  "params": {},',
                      '    "placeholder": true }'],
             voice="快照单文件跟着这次 LLM 选择走；连续 2 次命中才升 Pro，离线演示不发起外部调用。",
             boundary="选出 Pro 档不代表调用了 Pro 模型；Pro 模型与单价未选定，保持空占位。"),
        dict(dur=8, title="⑧ PII 四出口 + 全字段入库", subtitle="同一套规则贯穿入库前 / 进模型前 / 输出前 / 日志导出前",
             left=[f"四出口命中数 = {hits}（规则单点，结果一致）",
                   f"脱敏后：{redacted[:34]}…",
                   "compact / full 两档字段集合可切换",
                   "full 档含置信度、升级原因、预算留痕、快照引用"],
             right=['  "guardrails": {', '    "rule_version": "PII-RULES@1.0",',
                    f'    "total_hits": {hits[0]} * 4,', '    "gates": ["ingest","context",',
                    '               "output","audit"] }'],
             voice="脱敏与全字段入库都能在右侧 JSON 里直接核对。",
             boundary="规则级检测不是模型级 NER；图片 / 表格单元格内 PII 未覆盖。"),
        dict(dur=7, title="⑨ 离线可跑，回放可核对", subtitle="断网重跑两档，外部请求计数保持 0",
             left=["PARSING_OFFLINE=1 全程零外部请求",
                   f"runtime.external_request_count = 0",
                   "复杂档视觉结果来自 simple/complex .report.json",
                   "产物由 make_samples.py 从真实解析链路生成"],
             right=['  "cost": { "estimated": true,',
                    '    "external_request_count": 0,',
                    '    "note": "复杂档为离线回放" }',
                    '  "note": "演示产物，不是生产入库记录"'],
             voice="不消耗真实流量，也不把离线回放讲成在线识别。",
             boundary="本轮未开任何收费 API；Reducto / 阿里云解析仅做可插拔 profile。"),
        dict(dur=5, title="⑩ 上线与边界", subtitle="阿里云注册 / 实名 / 备案 / 支付由本人完成",
             left=["上线指导 HTML：11 步路径 + 费用估算 + 检查单",
                   "收费 API 调研：Reducto 质量首选 / 阿里云境内可达",
                   "实名、支付、域名备案、凭证保管必须本人操作",
                   "本轮不代注册、不代付费、不承诺已上线"],
             right=['  指导页：docs/showcase/aliyun-guide.html',
                    '  站点：showcase/ （端口 8767）',
                    '  明确未做：真实部署 / 压测 /',
                    '  监控 SLO / 合规审计'],
             voice="这里只给步骤和脚本，不承诺已上线。",
             boundary="演示切片不是生产实现；本页不是上线证明。"),
    ]

    total = sum(s["dur"] for s in shots)
    assert total == 84, f"分镜总时长应为 84 秒，实为 {total}"

    # 每镜 2 帧（前 / 后），帧时长平分
    listing: list[str] = []
    paths: list[Path] = []
    for i, s in enumerate(shots, 1):
        for phase in ("a", "b"):
            p = render_frame(i, s, phase)
            paths.append(p)
            half = s["dur"] / 2
            listing.append(f"file '{p.as_posix()}'\nduration {half}")
    listing.append(f"file '{paths[-1].as_posix()}'")  # concat 需要末帧重复
    lst = FRAMES / "list.txt"
    lst.write_text("\n".join(listing) + "\n", encoding="utf-8")

    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-vsync", "vfr", "-c:v", "libx264", "-crf", "28",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT_MP4)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {r.stderr[:400]}")

    b64 = base64.b64encode(OUT_MP4.read_bytes()).decode("ascii")
    (FRAMES / "demo.mp4.b64").write_text(b64, encoding="ascii")

    # 顺带产出「录像内嵌」的单文件离线版：把外链换成 data URI，
    # 主 index.html 保持轻量可编辑，standalone 版供单文件分发。
    main_html = OUT_MP4.parent / "index.html"
    standalone = OUT_MP4.parent / "index-standalone.html"
    if main_html.exists():
        html = main_html.read_text(encoding="utf-8")
        needle = '<source src="showcase-demo.mp4" type="video/mp4" />'
        assert needle in html, "主解释页的 video source 标记变了，请同步 make_video.py"
        html = html.replace(needle,
                            f'<source src="data:video/mp4;base64,{b64}" type="video/mp4" />')
        html = html.replace(
            '<a href="index-standalone.html"><code>interview/index-standalone.html</code></a>',
            '<code>index-standalone.html（即本文件）</code>')
        standalone.write_text(html, encoding="utf-8")
        print(f"standalone = {standalone}  bytes = {standalone.stat().st_size}")

    print(f"镜头数={len(shots)} 帧数={len(paths)} 总时长={total}s")
    print(f"mp4 = {OUT_MP4}  bytes = {OUT_MP4.stat().st_size}  base64 = {len(b64)} 字符")
    print(f"简单档 difficulty={ds:.3f} → standard | "
          f"复杂档 difficulty={dc:.3f} → 升档方向命中（滞回连续 2 次后切 pro）")
    print(f"分块 800/80 → {n800} 块, 400/80 → {n400} 块 | 四出口命中 {hits}")


if __name__ == "__main__":
    main()
