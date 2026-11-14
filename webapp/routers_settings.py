"""模型设置路由（admin）：LLM / Embedding / Rerank 提供者切换与连接测试。

设计要点：
- 配置落 SQLite（app_settings），切换即时生效、无需重启；
- 仅白名单键与枚举值；未知键 400；
- 连接测试返回 {ok, message, detail}，失败也返回 200，便于前端展示原因而不报错。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from webapp import db, providers, settings
from webapp.deps import require_admin

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    patch: dict


class ProviderTest(BaseModel):
    target: str  # llm | embedding | rerank


def _effective(cfg: dict) -> dict:
    return {
        "llm_provider": cfg["llm_provider"],
        "llm": providers.get_model_label(cfg),
        "thinking": providers.get_thinking_mode(cfg),
        "embedding": settings.effective_embedding(cfg)["name"],
        "rerank": ("off" if cfg["rerank_provider"] == "off"
                   else "LLM-as-reranker" if cfg["rerank_provider"] == "llm"
                   else cfg["external_rerank_model"]),
    }


@router.get("/settings")
def get_settings(_: dict = Depends(require_admin)):
    cfg = settings.get_all()
    local_models: list[str] = []
    local_ok = False
    try:
        local_models = providers.probe_models(cfg["local_llm_base_url"])
        local_ok = True
    except Exception:
        pass
    # Discovery 网关的模型清单：一次拉取给前端下拉用（失败则空，不影响页面）
    discovery_models: list[str] = []
    discovery_ok = bool(os.environ.get("DISCOVERY_API_KEY"))
    if discovery_ok:
        try:
            discovery_models = providers.probe_models(
                os.environ.get("DISCOVERY_BASE_URL", ""),
                os.environ.get("DISCOVERY_API_KEY", ""),
            )
        except Exception:
            discovery_ok = False
    return {
        "config": cfg,
        "effective": _effective(cfg),
        "local": {"reachable": local_ok, "models": local_models},
        "external": {
            "model": cfg["sensenova_model"],
            "configured": bool(os.environ.get("SENSENOVA_API_KEY")),
        },
        "discovery": {"configured": discovery_ok, "models": discovery_models},
        # 外部嵌入与重排共用硅基流动同一把 key；这里只回「是否已配置」，不下发密钥。
        "external_embedding": {
            "configured": bool(settings.effective_embedding(cfg)["api_key"]),
            "base_url": cfg["external_embedding_base_url"],
            "model": cfg["external_embedding_model"],
        },
    }


@router.put("/settings")
def update_settings(body: SettingsUpdate, user: dict = Depends(require_admin)):
    try:
        cfg = settings.set_many(body.patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.log_action(user["username"], user["role"], "settings", "update",
                  ", ".join(f"{k}={v}" for k, v in body.patch.items()))
    return {"config": cfg, "effective": _effective(cfg)}


@router.post("/settings/test")
def test_provider(body: ProviderTest, _: dict = Depends(require_admin)):
    cfg = settings.get_all()
    t = body.target
    low = settings.thinking_disabled(cfg)
    try:
        if t == "llm":
            if cfg["llm_provider"] == "local":
                r = providers.test_llm(cfg["local_llm_base_url"], cfg["local_llm_model"],
                                       thinking_disabled=low)
                return {"ok": True, "message": f"本地 LLM 可用（{r['latency_ms']} ms）", "detail": r}
            if cfg["llm_provider"] == "sensenova":
                r = providers.test_llm(
                    os.environ.get("SENSENOVA_BASE_URL", ""),
                    cfg["sensenova_model"],
                    os.environ.get("SENSENOVA_API_KEY", ""),
                    thinking_disabled=low,
                )
                return {"ok": True, "message": f"外部 DeepSeek 可用（{r['latency_ms']} ms）", "detail": r}
            if cfg["llm_provider"] == "discovery":
                r = providers.test_llm(
                    os.environ.get("DISCOVERY_BASE_URL", ""),
                    cfg["discovery_model"],
                    os.environ.get("DISCOVERY_API_KEY", ""),
                    thinking_disabled=low,
                )
                note = "（注意：该模型只返回推理过程，正文为空）" if r.get("reasoning_only") else ""
                return {"ok": True,
                        "message": (f"Discovery 网关可用（{r['latency_ms']} ms，模型 {cfg['discovery_model']}，"
                                    f"{r.get('thinking_mode', '')}，{r.get('low_thinking_evidence', '')}）{note}"),
                        "detail": r}
            return {"ok": True, "message": "当前为离线占位，无需连接"}
        if t == "embedding":
            eff = settings.effective_embedding(cfg)
            if eff["kind"] == "offline":
                return {"ok": True, "message": "当前为离线词法检索，无需连接"}
            r = providers.test_embedding(eff["base_url"], eff["model"], eff["api_key"])
            label = "硅基流动 BAAI/bge-m3 嵌入" if eff["kind"] == "external" else "本地嵌入"
            return {"ok": True,
                    "message": f"{label}可用，维度 {r['dim']}，{r['latency_ms']} ms",
                    "detail": r}
        if t == "rerank":
            rp = cfg["rerank_provider"]
            docs = ["长按复位键进入配网模式", "账号绑定流程"]
            if rp == "llm":
                scores = providers.rerank_with_llm("配网", docs, providers.get_llm(cfg))
                if scores is None:
                    return {"ok": False,
                            "message": "LLM 重排不可用（当前 LLM 为离线占位，或输出无法解析）"}
                return {"ok": True, "message": "LLM-as-reranker 可用", "detail": {"scores": scores}}
            if rp == "external":
                scores = providers.rerank(
                    cfg["external_rerank_base_url"], cfg["external_rerank_model"],
                    "配网", docs, os.environ.get("RERANK_API_KEY", ""),
                )
                if scores is None:
                    return {"ok": False,
                            "message": "外部 rerank 不可用：检查 Base URL / 模型名 / RERANK_API_KEY"}
                return {"ok": True, "message": "外部 rerank 可用", "detail": {"scores": scores}}
            return {"ok": True, "message": "rerank 已关闭"}
        raise HTTPException(status_code=400, detail=f"未知测试目标: {t}")
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "message": f"连接失败：{type(e).__name__}: {str(e)[:160]}"}
