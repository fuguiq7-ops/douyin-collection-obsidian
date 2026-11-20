# 注册 / 移除「抖音收藏 → Obsidian」自动链路的 Windows 计划任务
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File register-scheduled-task.ps1 -Kind quicknote -Daily -At 05:00
#   powershell -NoProfile -ExecutionPolicy Bypass -File register-scheduled-task.ps1 -Kind feishu  -Daily -At 05:30
#   powershell -NoProfile -ExecutionPolicy Bypass -File register-scheduled-task.ps1 -Kind sync -Remove
param(
    [string]$ToolRoot = (Join-Path $PSScriptRoot '..\..\..'),
    [ValidateSet('sync', 'deepdive', 'quicknote', 'feishu')]
    [string]$Kind = 'sync',
    [switch]$Daily,
    [string[]]$Days = @('Wednesday', 'Sunday'),
    [string]$At = '19:30',
    [string]$TaskName = '',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$ToolRoot = [IO.Path]::GetFullPath($ToolRoot)

$map = @{
    sync      = @{ Task = 'DouyinToObsidian-Sync';      Wrapper = '定时同步.cmd';  Log = 'scheduled.log';  Desc = '同步抖音收藏 / 稍后再看到本地 Obsidian 库' }
    deepdive  = @{ Task = 'DouyinToObsidian-Deepdive';  Wrapper = '定时深挖.cmd';  Log = 'deepdive.log';  Desc = '深挖标星 / 已赞条目：下载原片并转写逐字稿' }
    quicknote = @{ Task = 'DouyinToObsidian-Quicknote'; Wrapper = '定时提炼.cmd';  Log = 'quicknote.log'; Desc = '把已转写条目提炼成速查稿与推送草稿' }
    feishu    = @{ Task = 'DouyinToObsidian-Feishu';    Wrapper = '定时推送.cmd';  Log = 'feishu.log';   Desc = '把速查草稿推送到飞书知识库' }
}
$cfg = $map[$Kind]
if (-not $TaskName) { $TaskName = $cfg.Task }

if ($Remove) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已移除计划任务: $TaskName"
    } else {
        Write-Host "计划任务不存在: $TaskName"
    }
    return
}

$wrapper = Join-Path $ToolRoot $cfg.Wrapper
if (-not (Test-Path -LiteralPath $wrapper)) {
    throw "找不到 $wrapper。请确认 -ToolRoot 指向工具根目录（含定时脚本的目录）。"
}
$venv = Join-Path $ToolRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venv)) {
    Write-Warning "未找到 .venv：请先在工具目录运行 准备.cmd，否则任务会失败。"
}

$action = New-ScheduledTaskAction -Execute $wrapper -WorkingDirectory $ToolRoot
if ($Daily) {
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
} else {
    $trigger = foreach ($day in $Days) { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $At }
}
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description ("{0}（{1}）" -f $cfg.Desc, $At) `
    -Force | Out-Null

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "已注册计划任务: $TaskName"
Write-Host ("  触发方式: " + $(if ($Daily) { '每日 ' + $At } else { ($Days -join '、') + ' ' + $At }))
Write-Host ("  下次运行: " + $info.NextRunTime)
Write-Host ("  运行入口: " + $wrapper)
Write-Host ("  日志文件: " + (Join-Path (Join-Path $ToolRoot 'data\logs') $cfg.Log))
