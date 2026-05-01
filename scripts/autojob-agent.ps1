param(
    [string]$BaseUrl = "http://127.0.0.1:8080",
    [int]$IntervalSeconds = 90,
    [int]$MaxJobsPerRun = 0
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runtime = Join-Path $Root "runtime"
$LogDir = Join-Path $Runtime "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "autojobs.log"

$styles = @("cosmic","cyberpunk","volcanic","nebula","industrial","dark_cinematic","fantasy","dystopian","alien_planet")
$prompts = @(
    "KAMDRIDI audio reactive cinematic metal energy",
    "Echoes Unearthed underground vinyl dust and bronze light",
    "War Machines dark sci-fi battle pulse with red core",
    "Industrial sparks, smoke, rhythm, and ancient machinery",
    "Album visualizer with cinematic embers and deep shadows"
)

function Write-AgentLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date).ToString("s"), $Message
    Add-Content -LiteralPath $Log -Value $line
    Write-Host $line
}

$count = 0
Write-AgentLog "Echoes autojob agent started. BaseUrl=$BaseUrl Interval=$IntervalSeconds"
while ($true) {
    if ($MaxJobsPerRun -gt 0 -and $count -ge $MaxJobsPerRun) { break }
    try {
        $healthOk = $false
        try {
            Invoke-WebRequest -Uri $BaseUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
            $healthOk = $true
        } catch {
            Write-AgentLog "Server not reachable yet: $($_.Exception.Message)"
        }

        if ($healthOk) {
            $style = Get-Random -InputObject $styles
            $prompt = Get-Random -InputObject $prompts
            $body = @{ prompt = $prompt; style = $style; duration = 15; source = "autojob-agent" } | ConvertTo-Json -Compress
            try {
                $response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/generate" -ContentType "application/json" -Body $body -TimeoutSec 15
                Write-AgentLog "Submitted job style=$style prompt='$prompt' response=$($response | ConvertTo-Json -Compress)"
                $count++
            } catch {
                Write-AgentLog "Generate failed: $($_.Exception.Message)"
            }
        }
    } catch {
        Write-AgentLog "Agent loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
Write-AgentLog "Echoes autojob agent stopped after $count jobs."
