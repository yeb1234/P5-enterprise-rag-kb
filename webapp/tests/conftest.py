"""测试隔离配置：

- 独立临时数据库（WEBAPP_DB_PATH），避免踩演示库；
- 强制离线提供者（WEBAPP_*_PROVIDER），避免命中真实 API / 触发限流；
- 真实端点相关字段仍是 __TBC__，真实调用由单独的「真实集成冒烟」脚本负责（容忍 429）。

注意 `_test_db` 是**会话级共享**的（不是每个用例一份），所以任何用例改了
`rate_rules` / `budgets` 这类全局配置都会污染后续用例。见下方 `_isolate_rate_state`。
"""
import atexit
import os
import sqlite3
import tempfile

import pytest

_test_db = tempfile.mktemp(suffix=".db", prefix="webapp_test_")
os.environ["WEBAPP_DB_PATH"] = _test_db
os.environ["WEBAPP_LLM_PROVIDER"] = "offline"
os.environ["WEBAPP_EMBEDDING_PROVIDER"] = "offline"
os.environ["WEBAPP_RERANK_PROVIDER"] = "off"
# P2 多模态已接通真实网关，单测必须显式关掉，避免图片用例消耗真实流量
os.environ["PARSING_OFFLINE"] = "1"


@atexit.register
def _cleanup():
    try:
        os.remove(_test_db)
    except OSError:
        pass


def _reset_rate_state() -> None:
    """把限流规则与滑动窗口复位成默认值。

    限流状态有两份，都要清：
    1. **持久层**：SQLite 的 `rate_rules` 表（会话级共享库，会跨用例留存）；
    2. **进程层**：`observability._WINDOWS`（`MemoryLimiter` 刻意复用它，所以清一份等于清两份）。

    `db.RATE_RULE_DEFAULTS` 是默认值的唯一事实源，`_seed()` 与这里共用，
    避免「测试里写死 1200、种子改成 1200、某处还是 120」这类三方漂移。
    """
    from webapp import db, observability as obs, ratelimit_dist

    try:
        conn = db.get_conn()
        try:
            for dim, (lim, win) in db.RATE_RULE_DEFAULTS.items():
                conn.execute(
                    "UPDATE rate_rules SET limit_count=?, window_s=?, enabled=1"
                    " WHERE dimension=?", (lim, win, dim),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        # 首次用例在 init_db() 之前跑，表还不存在 —— 此时没有需要复位的东西
        pass

    with obs._rate_lock:
        obs._WINDOWS.clear()
    # 丢掉后端实例：万一某个用例改过 WEBAPP_REDIS_URL，下一个用例要重新决策后端
    ratelimit_dist._LIMITER = None
    ratelimit_dist._LIMITER_KEY = None


@pytest.fixture(autouse=True)
def _isolate_rate_state():
    """每个用例**开始前**复位限流状态。

    真实故障（已在全量跑里复现）：`test_http_path_does_not_share_model_bucket`
    把 `model` 维度配额改成 1 用来验证 429，改完没还原；由于测试库是会话级共享的，
    后续 `test_safety_api.py` 的所有 chat 请求全被 429 打死，报「2 failed」。
    业务代码没错，是测试之间没有隔离。

    选「开始前」而不是「结束后」：上一个用例崩在中间没走到收尾，下一个用例仍是干净起点。
    """
    _reset_rate_state()
    yield
