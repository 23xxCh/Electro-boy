param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Stop')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$ha = 'anker-ha-lovelace-preview'
$lp = 'anker-lp-controller'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$haInputs = Join-Path $root 'ha_lovelace_preview\sim_src\lp_ha_snapshot.json'
$metaUrl = 'http://127.0.0.1:45678/api/meta'

function Get-ContainerState([string]$name) {
    $running = & docker inspect $name --format '{{.State.Running}}' 2>$null
    if ($LASTEXITCODE -ne 0) { return 'missing' }
    if ([string]$running -eq 'true') { return 'running' }
    return 'stopped'
}

function Get-VeMeta {
    Invoke-RestMethod -Uri $metaUrl -TimeoutSec 3
}

function Set-VeClock([string]$clockAction) {
    if ($clockAction -notin @('pause', 'resume')) { throw 'Invalid VE clock action.' }
    $code = @"
import json, urllib.request
request = urllib.request.Request('http://127.0.0.1:45678/api/sim', data=json.dumps({'action': '$clockAction'}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5))['meta']))
"@
    $result = $code | docker exec -i $ha python3 -
    if ($LASTEXITCODE -ne 0) { throw "VE $clockAction failed." }
    return ($result | ConvertFrom-Json)
}

function Wait-VeMeta([int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        try { return Get-VeMeta } catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $deadline)
    throw "VE did not become ready within $seconds seconds."
}

function Wait-HaInputs([int]$slot, [double]$notBefore) {
    $deadline = (Get-Date).AddSeconds(90)
    do {
        try {
            $data = Get-Content -LiteralPath $haInputs -Raw | ConvertFrom-Json
            $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 - [double]$data.captured_at
            if ($data.sim_slot -eq $slot -and [double]$data.captured_at -ge $notBefore -and
                $age -ge -1 -and $age -le 4 -and
                $null -ne $data.site_1 -and $null -ne $data.site_2 -and $null -ne $data.site_3) {
                return
            }
        } catch { }
        if ((Get-ContainerState $ha) -ne 'running') { throw 'HA container stopped during startup.' }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw 'HA did not export a fresh three-site snapshot for the paused VE slot.'
}

function Read-NewLpLogs([string]$since) {
    return ((& docker logs --since $since $lp 2>&1) -join "`n")
}

function Wait-LpTargets([int]$slot, [string]$since) {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        if ((Get-ContainerState $lp) -ne 'running') { throw 'Controller_LP exited before dispatching three sites.' }
        $logs = Read-NewLpLogs $since
        if ($logs.Contains("slot=$slot site_1 target=") -and
            $logs.Contains("slot=$slot site_2 target=") -and
            $logs.Contains("slot=$slot site_3 target=")) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Controller_LP did not produce three targets for slot $slot."
}

function Wait-LpReadback([int]$slot, [string]$since) {
    $deadline = (Get-Date).AddSeconds(20)
    do {
        if ((Get-ContainerState $lp) -ne 'running') { throw 'Controller_LP exited before VE readback.' }
        if ((Read-NewLpLogs $since).Contains("slot ${slot}: three HA commands observed at VE")) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "VE did not confirm three HA commands for slot $slot."
}

try {
    & docker info --format '{{.ServerVersion}}' 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running.' }

    if ($Action -eq 'Stop') {
        if ((Get-ContainerState $lp) -eq 'running') {
            & docker stop --timeout 45 $lp | Out-Null
            if ($LASTEXITCODE -ne 0 -or (Get-ContainerState $lp) -eq 'running') {
                throw 'Could not stop Controller_LP.'
            }
        }
        if ((Get-ContainerState $ha) -eq 'running') {
            try {
                $meta = Set-VeClock 'pause'
                if (-not $meta.paused) { throw 'VE did not pause.' }
                $score = Invoke-RestMethod -Uri 'http://127.0.0.1:45678/api/score' -TimeoutSec 5
                $snapshotDir = Join-Path $root 'ha_lovelace_preview\run_snapshots'
                New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
                $snapshot = Join-Path $snapshotDir ("before_stop_{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
                $snapshotJson = @{ captured_at = (Get-Date).ToUniversalTime().ToString('o'); meta = $meta; score = $score } |
                    ConvertTo-Json -Depth 20
                [System.IO.File]::WriteAllText($snapshot, $snapshotJson, (New-Object System.Text.UTF8Encoding($false)))
                Write-Host "Saved local score snapshot: $snapshot"
            } catch {
                Write-Warning "VE pause or score snapshot failed: $($_.Exception.Message). HA will still be stopped."
            }
            # LP's zero-power HA webhook is asynchronous; let HA process it before shutdown.
            Start-Sleep -Seconds 2
            & docker stop --timeout 30 $ha | Out-Null
            if ($LASTEXITCODE -ne 0 -or (Get-ContainerState $ha) -eq 'running') {
                throw 'Could not stop HA/VE.'
            }
        }
        Write-Host 'Controller_LP, VE and HA are stopped. The next VE start begins a new simulation.'
        Write-Host 'Zero-power execution was not independently verified through HA entities.'
        exit 0
    }

    $haState = Get-ContainerState $ha
    $lpState = Get-ContainerState $lp
    if ($lpState -eq 'missing') { throw 'Controller_LP container is missing; first create it using ha_host/README.md.' }
    if ($lpState -eq 'running') {
        if ($haState -ne 'running') { throw 'Controller_LP is running without HA; stop the stack first.' }
        $meta = Wait-VeMeta 5
        if ($meta.speed -eq 180 -and -not $meta.paused -and
            $meta.scheduler -eq 'Controller_LP_via_HA') {
            $recent = Read-NewLpLogs ((Get-Date).ToUniversalTime().AddSeconds(-15).ToString('yyyy-MM-ddTHH:mm:ss.fffZ'))
            if ($recent -notmatch 'slot \d+: three HA commands observed at VE') {
                throw 'Controller_LP is running but has no recent three-site VE readback. Run Stop, then Start.'
            }
            Write-Host "Already running: VE 180x, slot $($meta.slot), three-site Controller_LP."
            exit 0
        }
        throw 'Both containers run, but VE/LP state is inconsistent. Run the Stop shortcut, then Start.'
    }

    $lpStartedHere = $false
    try {
        & (Join-Path $PSScriptRoot 'start_local_ha.ps1')
        $meta = Wait-VeMeta 60
        $meta = Set-VeClock 'pause'
        if (-not $meta.paused -or $meta.speed -ne 180 -or $meta.slot_real_seconds -ne 5 -or
            $meta.n_sites -ne 3 -or $meta.finished) {
            throw 'VE must be paused at 180x with three sites and 5 seconds per slot.'
        }
        Write-Host "VE paused at slot $($meta.slot); waiting for three HA devices..."
        $pausedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        Wait-HaInputs ([int]$meta.slot) $pausedAt

        $haId = (& docker inspect $ha --format '{{.Id}}').Trim()
        $network = (& docker inspect $lp --format '{{.HostConfig.NetworkMode}}').Trim()
        if ($network -ne "container:$haId") {
            throw 'Controller_LP is bound to another HA container ID; see ha_host/README.md before recreating it.'
        }

        $since = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        & docker start $lp | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not start Controller_LP.' }
        $lpStartedHere = $true
        Wait-LpTargets ([int]$meta.slot) $since
        # HA webhooks return before the official integration finishes its asynchronous service call.
        Start-Sleep -Seconds 6
        $meta = Set-VeClock 'resume'
        if ($meta.paused) { throw 'VE did not resume.' }
        Wait-LpReadback ([int]$meta.slot) $since
        $meta = Get-VeMeta
        Write-Host "Running: VE $($meta.speed)x, slot $($meta.slot), three HA commands confirmed."
        Write-Host 'HA: http://127.0.0.1:8123/anker-live/overview'
        Write-Host 'VE: http://127.0.0.1:45678/'
        exit 0
    } catch {
        if ($lpStartedHere -and (Get-ContainerState $lp) -eq 'running') {
            & docker stop --timeout 45 $lp | Out-Null
        }
        if ((Get-ContainerState $ha) -eq 'running') {
            try { $null = Set-VeClock 'pause' }
            catch { & docker stop --timeout 30 $ha | Out-Null }
        }
        throw
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
