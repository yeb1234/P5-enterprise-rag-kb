"""多格式评估集 · 语料生成（确定性，可复跑）。

为什么不用真实业务文档：真实文档没有 ground truth，就没法算召回率。
这里每份文档埋**唯一事实**（型号 / 编号 / 端口 / 温度 / 尺寸），问题与答案都可机器判定。

格式覆盖与「能不能进检索链路」是两件事，本文件只负责**造字节**：

| 文件 | 格式 | 主链路可检索 | 说明 |
|---|---|---|---|
| 01-产品说明书.txt | txt | 是 | 纯文本基线 |
| 02-故障排查手册.md | md | 是 | 含错误码表格（Markdown 表格） |
| 03-设备台账.csv | csv | 是 | 表格：靠 `chunk_text` 按字符切，行结构会保留 |
| 04-接口配置.json | json | 是 | 配置型结构化文本 |
| 05-配网脚本.py | py | 是 | 代码 |
| 06-运行日志.log | log | 是 | 非结构化日志 |
| 07-现场部署.yaml | yaml | 是 | 工程配置文件 |
| 08-技术规范.pdf | pdf | **是（本轮新接）** | 手写合法 PDF，带文本层 |
| 09-铭牌照片.png | png | 否 | 无文本层图片，只能靠 OCR / 视觉 |
| 10-管线图.dxf | dxf | 否 | ASCII 工程图文件，但不在支持扩展名内 |

PDF 内容只用 ASCII：不嵌 CJK 字体就没法在 PDF 里放中文，这是格式约束不是偷懒。
中文 PDF 需要字体映射（ToUnicode CMap），不在本次评估范围。
"""
from __future__ import annotations

from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


# ---------------------------------------------------------------- 纯文本类

TXT_MANUAL = """\
KX-2400 智能网关产品说明书（V3.2）

一、产品型号与规格
本产品型号为 KX-2400，额定功率 850W，输入电压 220V，防护等级 IP44。
整机重量 3.6kg，支持壁挂与导轨两种安装方式。

二、开机与配网
长按电源键 3 秒开机。进入「设置 - 网络」，选择现场 Wi-Fi 并完成配网。
配网过程中指示灯为蓝色慢闪；配网成功后转为常亮。

三、账号绑定
扫码绑定企业账号后，可在知识库中上传产品文档与现场照片。

四、售后
整机保修 24 个月，保修期自出库日期起算。非人为损坏免费换新。
"""

MD_TROUBLESHOOT = """\
# 故障排查手册

## 错误码对照表

| 错误码 | 含义 | 处理动作 |
|---|---|---|
| E4711 | 网关证书过期 | 重签证书并下发后重启网关 |
| E4712 | 上行链路丢包率超阈值 | 检查光模块与尾纤 |
| E4713 | 时钟未同步 | 配置 NTP 服务器 |

## 常见现象

- 指示灯红色常亮：多为 E4711，先查证书有效期。
- 指示灯红蓝交替：固件升级中，不要断电。

## 升级注意

升级包必须与型号匹配。KX-2400 使用 kx24 系列固件，刷错型号会导致无法启动。
"""

CSV_LEDGER = """\
设备编号,型号,归属部门,入库日期,存放位置,负责人
SN-88301,KX-2400,仓储部,2026-03-14,A区-03货架,张伟
SN-88302,KX-2400,运维部,2026-03-14,A区-03货架,李娜
SN-88303,KX-2100,研发部,2026-02-28,B区-01货架,王强
SN-88310,KX-2400,仓储部,2026-04-02,C区-02货架,张伟
"""

JSON_CONFIG = """\
{
  "service": "kx-gateway-agent",
  "listen": {"host": "0.0.0.0", "port": 18443},
  "upstream": {"url": "https://kb.internal/api/v1", "timeout_seconds": 30},
  "limits": {"max_inflight": 64, "rate_per_minute": 600},
  "tls": {"enabled": true, "min_version": "TLSv1.2"},
  "note": "端口变更需同步防火墙策略"
}
"""

PY_SCRIPT = """\
# 现场配网脚本：把网关拉到工程网段
import subprocess

DEFAULT_GATEWAY = "10.66.8.1"
DNS_PRIMARY = "10.66.8.53"
INTERFACE = "eth0"


def configure(iface: str = INTERFACE) -> None:
    # 注意：网关地址写错会导致整机失联，改前先确认现场网段
    subprocess.run(["ip", "route", "add", "default", "via", DEFAULT_GATEWAY,
                    "dev", iface], check=True)


if __name__ == "__main__":
    configure()
"""

LOG_TEXT = """\
2026-11-05 02:11:03 INFO  agent start, version=3.2.1
2026-11-05 02:12:44 WARN  tls handshake slow, cost=2100ms
2026-11-05 02:14:07 ERROR code=E4711 gateway certificate expired
2026-11-05 02:14:19 ERROR code=E4711 gateway certificate expired
2026-11-05 02:14:31 ERROR code=E4711 gateway certificate expired
2026-11-05 02:15:02 INFO  retry scheduled in 30s
2026-11-05 02:16:10 INFO  agent recovered, uptime=310s
"""

YAML_DEPLOY = """\
# 现场部署配置（生产）
cluster: kb-prod-shanghai
namespace: kb
replicas: 3
resources:
  requests: {cpu: "500m", memory: "1Gi"}
  limits: {cpu: "2", memory: "4Gi"}
env:
  LOG_LEVEL: info
  MAX_INFLIGHT: "64"
ingress:
  host: kb.internal.example.com
  tls_secret: kb-tls
"""

# PDF 文本层（ASCII only）
PDF_LINES = [
    "KX-2400 Technical Specification (Rev.B)",
    "",
    "1. Environmental",
    "   Operating temperature: -20 to 55 C",
    "   Storage humidity: 5% to 95% non-condensing",
    "   Protection grade: IP54",
    "",
    "2. Mechanical",
    "   Dimensions: 320x210x95 mm",
    "   Net weight: 3.6 kg",
    "   Mounting: wall bracket / DIN rail",
    "",
    "3. Electrical",
    "   Rated power: 850 W",
    "   Input: 220 VAC 50/60 Hz",
    "",
    "4. Compliance",
    "   EMC: EN 55032 Class B",
    "   Safety: EN 62368-1",
]


# ---------------------------------------------------------------- 生成器

def _make_pdf(lines: list[str]) -> bytes:
    """手写最小合法 PDF（Helvetica + WinAnsi，纯 ASCII）。

    不引第三方库是因为 reportlab / fpdf 在项目里都没有，
    为了生成两份测试文件装一个重依赖不划算；这段 40 行的写入器足够产出
    pypdf 能正确抽取文本层的文件。若以后要放中文，必须嵌 CJK 字体 + ToUnicode，
    那时应该换 reportlab，而不是继续手写。
    """
    parts = ["BT", "/F1 11 Tf", "50 790 Td", "15 TL"]
    for line in lines:
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({esc}) Tj")
        parts.append("T*")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {size} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def _make_plate_image() -> bytes:
    """无文本层铭牌图：像素里写着 74119 与 SN-74119。

    「无字」指没有文本层，不是「没有信息」——这正是 OCR / 视觉要解决的场景。
    """
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    width, height = 760, 420
    img = Image.new("RGB", (width, height), (252, 252, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, 96], fill=(226, 236, 252))
    draw.rectangle([24, 132, width - 24, height - 24], outline=(30, 30, 30), width=4)

    font_big = font_small = None
    for candidate in (
        "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            font_big = ImageFont.truetype(candidate, 132)
            font_small = ImageFont.truetype(candidate, 44)
            break
        except Exception:
            continue
    if font_big is None:  # 退路：位图字体，够大也能读，只是不好看
        font_big = ImageFont.load_default()
        font_small = font_big

    draw.text((70, 168), "74119", fill=(18, 28, 78), font=font_big)
    draw.text((70, 320), "SN-74119", fill=(150, 30, 30), font=font_small)
    draw.text((430, 30), "NAME PLATE", fill=(30, 60, 140), font=font_small)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


DXF_TEXT = """\
0
SECTION
2
HEADER
9
$ACADVER
1
AC1015
0
ENDSEC
0
SECTION
2
ENTITIES
0
TEXT
8
LAYER-PIPE-3
1
DN150 main pipe, length 42.5 m
0
TEXT
8
LAYER-PIPE-3
1
slope 0.3 percent toward sump
0
ENDSEC
0
EOF
"""


FILES: dict[str, bytes] = {
    "01-产品说明书.txt": TXT_MANUAL.encode("utf-8"),
    "02-故障排查手册.md": MD_TROUBLESHOOT.encode("utf-8"),
    "03-设备台账.csv": CSV_LEDGER.encode("utf-8"),
    "04-接口配置.json": JSON_CONFIG.encode("utf-8"),
    "05-配网脚本.py": PY_SCRIPT.encode("utf-8"),
    "06-运行日志.log": LOG_TEXT.encode("utf-8"),
    "07-现场部署.yaml": YAML_DEPLOY.encode("utf-8"),
    "10-管线图.dxf": DXF_TEXT.encode("utf-8"),
}


def build(force: bool = False) -> Path:
    """把全部语料写到 `evalfmt/corpus/`，返回目录。可重复执行（内容确定）。"""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in FILES.items():
        target = CORPUS_DIR / name
        if force or not target.exists() or target.read_bytes() != payload:
            target.write_bytes(payload)

    pdf = CORPUS_DIR / "08-技术规范.pdf"
    payload = _make_pdf(PDF_LINES)
    if force or not pdf.exists() or pdf.read_bytes() != payload:
        pdf.write_bytes(payload)

    png = CORPUS_DIR / "09-铭牌照片.png"
    payload = _make_plate_image()
    if force or not png.exists() or png.read_bytes() != payload:
        png.write_bytes(payload)
    return CORPUS_DIR


if __name__ == "__main__":
    out = build(force=True)
    for p in sorted(out.iterdir()):
        print(f"{p.name:28s} {p.stat().st_size:>8d} B")
