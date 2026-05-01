param(
    [string]$TaskName = "EchoesEngineDaemon"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Daemon = Join-Path $Root "scripts\daemon.bat"
if (!(Test-Path -LiteralPath $Daemon)) {
    throw "Missing daemon script: $Daemon"
}

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Daemon`""
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Daemon script: $Daemon"
