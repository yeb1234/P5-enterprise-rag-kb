"""接入契约门禁：真实 API 档位下，任何 blocking 字段仍为 __TBC__ 就快速失败。"""
from __future__ import annotations

from typing import Iterable


class ContractNotReady(RuntimeError):
    def __init__(self, pending: list[dict]):
        self.pending = pending
        brief = ", ".join(f"{p['component']}.{p['field_key']}" for p in pending[:8])
        more = "" if len(pending) <= 8 else f" ... 共 {len(pending)} 项"
        super().__init__(f"接入契约未就绪，仍为 __TBC__ 的 blocking 字段: {brief}{more}")


def pending_fields(registry, components: Iterable[str] | None = None) -> list[dict]:
    content = registry.content("OBJ-ADAPTER-CONTRACTS")
    token = content["placeholder_token"]
    out = []
    for comp_name, comp in content["components"].items():
        if components and comp_name not in components:
            continue
        for field in comp["fields"]:
            unresolved = field["value"] == token
            if isinstance(field["value"], dict):
                unresolved = all(v == token for v in field["value"].values())
            if unresolved:
                out.append({
                    "component": comp_name,
                    "provider_label": comp["provider_label"],
                    "field_key": field["field_key"],
                    "blocking": bool(field["blocking"]),
                    "needed_for": field["needed_for"],
                    "confirmation_source": field["confirmation_source"],
                    "value": field["value"],
                })
    return out


def contract_gate(registry, profile: str) -> dict:
    pending = pending_fields(registry)
    blocking = [p for p in pending if p["blocking"]]
    return {
        "profile": profile,
        "ready_for_real_api": len(blocking) == 0,
        "total_fields": sum(len(c["fields"]) for c in registry.content("OBJ-ADAPTER-CONTRACTS")["components"].values()),
        "pending_count": len(pending),
        "blocking_pending_count": len(blocking),
        "optional_pending_count": len(pending) - len(blocking),
        "pending": pending,
        "note": "offline_placeholder 不依赖任何真实契约；real_api 必须先消解所有 blocking 项",
    }


def assert_real_ready(registry, profile: str) -> None:
    if profile != "real_api":
        return
    gate = contract_gate(registry, profile)
    if not gate["ready_for_real_api"]:
        raise ContractNotReady([p for p in gate["pending"] if p["blocking"]])
    run_profile = registry.content("OBJ-RUN-PROFILE")["profiles"]["real_api"]
    if not run_profile["enabled"]:
        raise ContractNotReady([{
            "component": "run_profile", "field_key": "real_api.enabled",
            "blocking": True, "needed_for": ["*"], "confirmation_source": "变更审批",
            "value": False,
        }])
