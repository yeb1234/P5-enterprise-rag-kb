"""生成链路评估语料与问答集，并校验标注一致性。

产物（同目录）：
  corpus_v1.json     5 份合成说明书（每份整篇作为一个 chunk 入库）
  chain_qa_v1.jsonl  60 条问答：30 可答 / 15 不可答 / 15 干扰

为什么要校验：key_facts 必须逐字出现在 gold_doc 原文里，否则
answer_accuracy 会因为「标注压根不在语料中」而恒为 0——这种假失败
比模型答错更难排查。归一化口径直接复用 evalbench，避免两套规则漂移。

运行：python webapp/eval_datasets/build_chain_qa.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evalbench.core.scoring import normalize  # noqa: E402

CORPUS = [
    {
        "name": "wifi-setup.txt",
        "title": "智能网关配网说明",
        "text": (
            "设备配网步骤：长按机身复位键 3 秒，指示灯快闪后松开；"
            "在 App 中选择 2.4GHz Wi-Fi 并输入密码，听到连接成功提示音即完成配网。"
            "若配网失败，请确认路由器未开启 5GHz 独占模式。"
            "配网成功后设备会自动同步时间，时区默认为东八区。"
            "单台网关最多可连接 128 个子设备。"
        ),
    },
    {
        "name": "billing.txt",
        "title": "费用与账单说明",
        "text": (
            "费用说明：本月账单包含一次性配网服务费 20 元，请在每月 25 日前完成缴费，"
            "逾期将按日加收千分之五滞纳金。发票可在个人中心自助开具，"
            "电子发票在 3 个工作日内发送至预留邮箱。企业客户可申请月结，账期为 30 天。"
        ),
    },
    {
        "name": "warranty.txt",
        "title": "保修政策",
        "text": (
            "保修政策：整机保修 12 个月，电池保修 6 个月，保修期自签收之日起计算。"
            "人为损坏、私自拆机、进水不在保修范围内。"
            "申请保修需提供订单号与设备序列号，寄修往返运费由用户承担。"
            "保修期内更换的配件享受剩余保修期。"
        ),
    },
    {
        "name": "firmware.txt",
        "title": "固件升级指引",
        "text": (
            "固件升级：设备每两周检查一次更新，也可在 App 的「设备信息」页手动触发。"
            "升级过程约需 5 分钟，期间请保持通电，切勿断电。"
            "升级失败会自动回滚到上一版本。"
            "跨大版本升级（如 2.x 升 3.x）需要先备份配置。"
        ),
    },
    {
        "name": "asset-scrap.txt",
        "title": "设备报废流程",
        "text": (
            "设备报废流程：填写资产报废单，经部门主管与财务审批后，交由行政统一回收处理，"
            "并在资产台账中登记注销。报废设备中的存储介质必须先行消磁或物理销毁。"
            "单价超过 5000 元的设备报废需分管副总审批。"
        ),
    },
]

# (id, type, question, gold_doc, key_facts, note)
CASES = [
    # ---- 可答 30 条 ----
    ("Q001", "answerable", "长按复位键多久可以进入配网？", "wifi-setup.txt", ["3 秒"], ""),
    ("Q002", "answerable", "配网时应该选择哪个频段的 Wi-Fi？", "wifi-setup.txt", ["2.4GHz"], ""),
    ("Q003", "answerable", "配网成功的提示是什么？", "wifi-setup.txt", ["提示音"], ""),
    ("Q004", "answerable", "配网失败要先检查路由器的什么设置？", "wifi-setup.txt", ["5GHz 独占模式"], ""),
    ("Q005", "answerable", "一台网关最多可以连接多少个子设备？", "wifi-setup.txt", ["128 个"], ""),
    ("Q006", "answerable", "配网成功后设备时区默认是什么？", "wifi-setup.txt", ["东八区"], ""),
    ("Q007", "answerable", "配网完成后设备会自动同步什么？", "wifi-setup.txt", ["时间"], ""),
    ("Q008", "answerable", "一次性配网服务费是多少钱？", "billing.txt", ["20 元"], ""),
    ("Q009", "answerable", "账单每月几号前需要完成缴费？", "billing.txt", ["25 日"], ""),
    ("Q010", "answerable", "逾期缴费的滞纳金怎么计算？", "billing.txt", ["千分之五"], ""),
    ("Q011", "answerable", "电子发票多久会发送到邮箱？", "billing.txt", ["3 个工作日"], ""),
    ("Q012", "answerable", "企业客户的账期是多少天？", "billing.txt", ["30 天"], ""),
    ("Q013", "answerable", "发票可以在哪里自助开具？", "billing.txt", ["个人中心"], ""),
    ("Q014", "answerable", "整机保修多长时间？", "warranty.txt", ["12 个月"], ""),
    ("Q015", "answerable", "电池保修多长时间？", "warranty.txt", ["6 个月"], ""),
    ("Q016", "answerable", "保修期从什么时候开始计算？", "warranty.txt", ["签收之日"], ""),
    ("Q017", "answerable", "哪些情况不在保修范围内？", "warranty.txt", ["人为损坏", "私自拆机", "进水"], ""),
    ("Q018", "answerable", "申请保修需要提供哪些信息？", "warranty.txt", ["订单号", "设备序列号"], ""),
    ("Q019", "answerable", "寄修的往返运费由谁承担？", "warranty.txt", ["用户承担"], ""),
    ("Q020", "answerable", "设备多久检查一次固件更新？", "firmware.txt", ["两周"], ""),
    ("Q021", "answerable", "在哪里可以手动触发固件升级？", "firmware.txt", ["设备信息"], ""),
    ("Q022", "answerable", "固件升级大约需要多长时间？", "firmware.txt", ["5 分钟"], ""),
    # key_fact 取「通电」而非「保持通电」：模型实际输出「保持设备通电」，
    # 中间插了「设备」二字，长子串会假阴性。短实体对语序变化更鲁棒。
    ("Q023", "answerable", "固件升级期间对供电有什么要求？", "firmware.txt", ["通电"], ""),
    ("Q024", "answerable", "固件升级失败会怎样处理？", "firmware.txt", ["自动回滚"], ""),
    ("Q025", "answerable", "跨大版本升级前需要做什么准备？", "firmware.txt", ["备份配置"], ""),
    ("Q026", "answerable", "报废单需要经过哪些人审批？", "asset-scrap.txt", ["部门主管", "财务"], ""),
    ("Q027", "answerable", "报废设备由哪个部门统一回收？", "asset-scrap.txt", ["行政"], ""),
    ("Q028", "answerable", "报废设备的存储介质必须怎么处理？", "asset-scrap.txt", ["消磁", "物理销毁"], ""),
    ("Q029", "answerable", "单价超过多少元的设备报废需要副总审批？", "asset-scrap.txt", ["5000 元"], ""),
    ("Q030", "answerable", "报废后要在哪里登记注销？", "asset-scrap.txt", ["资产台账"], ""),

    # ---- 不可答 15 条（语料中不存在，期望拒答）----
    ("Q031", "unanswerable", "设备支持哪些语音助手？", None, [], "语料无语音助手信息"),
    ("Q032", "unanswerable", "设备的防水等级是多少？", None, [], "语料无防水等级"),
    ("Q033", "unanswerable", "可以同时连接几个蓝牙耳机？", None, [], "语料无蓝牙耳机"),
    ("Q034", "unanswerable", "设备的重量是多少克？", None, [], "语料无重量"),
    ("Q035", "unanswerable", "支持 5G 蜂窝网络吗？", None, [], "语料无蜂窝网络"),
    ("Q036", "unanswerable", "包装清单里包含哪些配件？", None, [], "语料无包装清单"),
    ("Q037", "unanswerable", "设备使用的处理器型号是什么？", None, [], "语料无处理器"),
    ("Q038", "unanswerable", "是否支持太阳能充电？", None, [], "语料无太阳能"),
    ("Q039", "unanswerable", "说明书有几种语言版本？", None, [], "语料无语言版本"),
    ("Q040", "unanswerable", "设备的工作温度范围是多少？", None, [], "语料无工作温度"),
    ("Q041", "unanswerable", "支持 Matter 协议吗？", None, [], "语料无 Matter"),
    ("Q042", "unanswerable", "设备可以挂墙安装吗？", None, [], "语料无安装方式"),
    ("Q043", "unanswerable", "设备有几种颜色可选？", None, [], "语料无颜色"),
    ("Q044", "unanswerable", "语音唤醒词可以自定义吗？", None, [], "语料无唤醒词"),
    ("Q045", "unanswerable", "出厂默认管理员密码是什么？", None, [], "语料无默认密码"),

    # ---- 干扰 15 条（关键词跨文档，gold 只有一个）----
    ("Q046", "distractor", "配网服务费需要多少钱？", "billing.txt", ["20 元"], "配网一词在 wifi 文档，价格在 billing"),
    ("Q047", "distractor", "设备电池的保修期是多久？", "warranty.txt", ["6 个月"], "设备一词在报废文档"),
    ("Q048", "distractor", "设备升级过程中可以断电吗？", "firmware.txt", ["保持通电"], "设备一词在多份文档"),
    ("Q049", "distractor", "报废设备的回收由哪个部门负责？", "asset-scrap.txt", ["行政"], "设备一词在配网/保修文档"),
    ("Q050", "distractor", "配网时路由器不能开启什么模式？", "wifi-setup.txt", ["5GHz 独占模式"], "配网在账单文档也出现"),
    ("Q051", "distractor", "账单逾期会加收什么费用？", "billing.txt", ["滞纳金"], "费用在配网文档也出现"),
    ("Q052", "distractor", "设备进水还能享受保修吗？", "warranty.txt", ["进水"], "设备一词在多份文档"),
    ("Q053", "distractor", "固件升级失败后设备会怎样？", "firmware.txt", ["自动回滚"], "设备一词在多份文档"),
    ("Q054", "distractor", "配网成功后还需要手动设置时间吗？", "wifi-setup.txt", ["自动同步"], "配网在账单文档也出现"),
    ("Q055", "distractor", "报废金额较大的设备需要谁审批？", "asset-scrap.txt", ["分管副总"], "设备一词在多份文档"),
    ("Q056", "distractor", "电子发票什么时候能收到？", "billing.txt", ["3 个工作日"], "发票仅账单文档"),
    ("Q057", "distractor", "私自拆机会影响保修吗？", "warranty.txt", ["私自拆机"], "设备一词在多份文档"),
    ("Q058", "distractor", "跨版本固件升级前要做什么准备？", "firmware.txt", ["备份配置"], "升级在配网文档也出现"),
    ("Q059", "distractor", "网关最多能带多少个子设备？", "wifi-setup.txt", ["128 个"], "设备一词在多份文档"),
    ("Q060", "distractor", "报废设备后要在台账里做什么？", "asset-scrap.txt", ["登记注销"], "设备一词在多份文档"),
]


def main() -> int:
    here = Path(__file__).resolve().parent
    doc_by_name = {d["name"]: d for d in CORPUS}

    bad: list[tuple[str, str, str]] = []
    rows: list[dict] = []
    for cid, ctype, question, gold, facts, note in CASES:
        if gold:
            doc = doc_by_name.get(gold)
            if doc is None:
                bad.append((cid, gold, "gold_doc 不存在"))
                continue
            for kf in facts:
                if normalize(kf) not in normalize(doc["text"]):
                    bad.append((cid, kf, f"不在 {gold} 原文中"))
        rows.append({
            "id": cid, "type": ctype, "question": question,
            "gold_doc": gold, "key_facts": facts, "note": note,
        })

    if bad:
        print("[FAIL] 标注校验未通过：", file=sys.stderr)
        for b in bad:
            print(f"  {b[0]}  key_fact={b[1]!r}  {b[2]}", file=sys.stderr)
        return 1

    corpus_path = here / "corpus_v1.json"
    corpus_path.write_text(
        json.dumps({"version": "v1", "documents": CORPUS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    qa_path = here / "chain_qa_v1.jsonl"
    with qa_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    print(f"[OK] {corpus_path.name}: {len(CORPUS)} 份文档")
    print(f"[OK] {qa_path.name}: {len(rows)} 条, 分布={counts}")
    print(f"[OK] 标注校验通过：{sum(len(r['key_facts']) for r in rows)} 个 key_fact 全部命中原文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
