# 切 DeepSeek 官方 API 操作说明（6 点后用）

## 为什么换

当前 Intern-AI 网关 TPM 限流严重（一批 400 条卡 45 分钟），进度 800/11873 停滞。
换官方 DeepSeek API 的**真实收益是速度与稳定性**：无网关限流、并发上限 2500、价格透明。
缓存省钱是次要的——本评估每条 context 不同，prefix cache 只有系统提示词那 537 字符稳定命中。

## 成本（官方 rate card，2026-09 核对）

模型 V4.1-Flash（官方 id `deepseek-flash`），空闲时段（北京 12-14 点、18 点后等）：
input $0.15/百万 token（cache hit $0.003）、output $0.6/百万 token；高峰翻倍。

| 项 | 估算 |
|---|---|
| 单 pass 全量 11873 条 | **约 $0.74（≈¥5.3）** |
| 双 pass（参数不同，全量重算） | 约 $1.48（≈¥10.7） |
| 保守（completion 翻到 200 token） | 单 pass 约 $1.95 |

**结论：成本可忽略，换 API 是买速度不是省钱。**

> 注意：网关 `/models` 的 `cost_usd` 字段是内部记账单位（比官方美元高约 6000 倍），
> **不要**用它算真实成本。本表用官方 rate card。

## 执行步骤（6 点后）

1. 确认手上有官方 key（`.env` 里 `DEEPSEEK_API_KEY` 或临时设环境变量）
2. PowerShell 里执行：
   ```powershell
   $env:DEEPSEEK_API_KEY = "sk-你的key"
   .\evalbench\switch_to_official.ps1 -PassId p1
   ```
3. 脚本会先**探活**（一条极简请求），确认 key/端点/模型名都对了才开跑——
   不会跑到一半才发现鉴权失败
4. 默认每次跑 2 批就退出（每次约几分钟，官方不限流的话），可反复执行直到跑完；
   跑完 30 批后仍需按项目约定 commit + push（脚本不自动提交，避免误提交讲稿噪音）

## 两种模式（重要）

| 模式 | 操作 | 后果 |
|---|---|---|
| **续跑**（默认） | 直接跑 | 保留网关已跑的 2 批（offset 0、400），用官方跑剩余 28 批。省时，但**两批来自不同后端** |
| **重跑**（同源可比） | 先删 `out/full_baseline_progress_p1.json` 再跑 | 官方跑全部 30 批。结果同源、可比性强；多花约 ¥3.5 |

**推荐重跑模式**：全量基线是要写进报告的，混用两个后端会在方法论上留瑕疵；
¥3.5 买"数据同源"很值。但如果你赶时间，续跑也能用，报告里注明"前 800 条来自网关"即可。

## 技术细节（已实现，无需你管）

- `runner.active_endpoint()`：优先级 `DEEPSEEK_*`（官方）> `EVAL_*` > `DISCOVERY_*`（网关默认）
- **缓存 key 隔离**：设了 `DEEPSEEK_BASE_URL` 后 key 变化，不复用网关旧答案；默认仍保持原 key，历史 2000+ 缓存不失效
- `models.json` 新增 `official_deepseek` 段：`deepseek-flash`（快速）/ `deepseek-v4-pro`（强推理）
- 官方模型名与网关不同：`deepseek-flash` ≠ `deepseek-v4.1-flash-1017`，勿混用
- `probe_official.py` 探活；`switch_to_official.ps1` 一键切换（DryRun 可只验配置不调 API）

## pass2 提醒

pass2（Step3 再收敛后）必须用**不同参数或提示词**——否则缓存 key 相同，
pass2 会 100% 命中 pass1 缓存、零新调用，等于假跑。Step3 我会主动确保参数真的变了。
