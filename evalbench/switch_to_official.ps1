# 切到 DeepSeek 官方 API 跑 SQuAD 全量基线
# 6 点后拿到官方 key，按下面两步执行：
#   $env:DEEPSEEK_API_KEY = "sk-你的key"
#   .\evalbench\switch_to_official.ps1 -PassId p1
#
# 事实：
# - 官方 model id = deepseek-flash（不是网关的 deepseek-v4.1-flash-1017），见 .env.example:49
# - 设 DEEPSEEK_BASE_URL 后缓存 key 自动隔离，不复用网关旧结果
# - 不设本脚本时仍走网关，历史 2000+ 缓存不失效

param(
    [ValidateSet("p1", "p2")][string]$PassId = "p1",
    [string]$Params = "A",
    [string]$Prompts = "v4_en_grounded",
    [int]$MaxBatches = 2,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $env:DEEPSEEK_API_KEY) {
    Write-Error "请先设置 DEEPSEEK_API_KEY 环境变量"
    exit 2
}
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"

Write-Output "=== 切官方 DeepSeek ==="
Write-Output ("端点: " + $env:DEEPSEEK_BASE_URL)
Write-Output ("模型: deepseek-flash | pass: " + $PassId + " | 参数: " + $Params + " | 提示词: " + $Prompts)

if (-not $DryRun) {
    Write-Output "[探活] 单条请求验证 key/模型名..."
    python evalbench/probe_official.py
    if ($LASTEXITCODE -ne 0) {
        Write-Error "探活失败，停止跑批（检查 key / 端点 / 模型名）"
        exit 1
    }
    Write-Output "[探活] OK"

    python evalbench/run_full_baseline.py `
        --label "squad-$PassId-official" `
        --pass-id $PassId `
        --dataset public/squad_v2_full.jsonl `
        --batch 400 `
        --max-batches $MaxBatches `
        --models deepseek-flash `
        --prompts $Prompts `
        --params $Params `
        --wait 20 --factor 1.5 --cap 180 --max-retries 10
    if ($LASTEXITCODE -ne 0) {
        Write-Error ("跑批返回 " + $LASTEXITCODE)
    }
} else {
    Write-Output "[DryRun] 未实际调用 API"
}
