"""测试隔离配置：

- 独立临时数据库（WEBAPP_DB_PATH），避免踩演示库；
- 强制离线提供者（WEBAPP_*_PROVIDER），避免命中真实 API / 触发限流；
- 真实端点相关字段仍是 __TBC__，真实调用由单独的「真实集成冒烟」脚本负责（容忍 429）。
"""
import os
import tempfile
import atexit

_test_db = tempfile.mktemp(suffix=".db", prefix="webapp_test_")
os.environ["WEBAPP_DB_PATH"] = _test_db
os.environ["WEBAPP_LLM_PROVIDER"] = "offline"
os.environ["WEBAPP_EMBEDDING_PROVIDER"] = "offline"
os.environ["WEBAPP_RERANK_PROVIDER"] = "off"


@atexit.register
def _cleanup():
    try:
        os.remove(_test_db)
    except OSError:
        pass
