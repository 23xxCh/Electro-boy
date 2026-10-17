param(
    [switch]$DryRun,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
$containerName = 'anker-ha-lovelace-preview'
$image = 'ghcr.io/home-assistant/home-assistant:stable'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configDir = Join-Path $projectRoot 'ha_lovelace_preview'
$serviceDir = Join-Path $PSScriptRoot 'local_ha_service'
$simSrc = Join-Path $configDir 'sim_src'

$required = @(
    (Join-Path $configDir 'configuration.yaml'),
    (Join-Path $configDir 'lovelace-live.yaml'),
    (Join-Path $configDir 'rest-live.yaml'),
    (Join-Path $configDir 'lp_ha_inputs.yaml'),
    (Join-Path $configDir 'sim_src\VE\run.py'),
    (Join-Path $configDir 'custom_components\anker_solix_official\manifest.json'),
    (Join-Path $configDir 'custom_components\anker_solix_official\config\6ff7f6dd1fada1bebc0d7e18c02c01ee69ce977f91a2ab29d171b251a4e0c445.yaml'),
    (Join-Path $serviceDir 'run'),
    (Join-Path $PSScriptRoot 'modbus_502_proxy.py'),
    (Join-Path $PSScriptRoot 've_bridge.py'),
    (Join-Path $PSScriptRoot 'export_lp_ha_snapshot.py')
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing local HA file: $path"
    }
}

$existing = @(docker ps -a --filter "name=^/$containerName`$" --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
$exists = $existing -contains $containerName
$supervised = $false
if ($exists) {
    $image = docker inspect $containerName --format '{{.Image}}'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the current HA image.' }
    $mounts = docker inspect $containerName --format '{{json .Mounts}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the existing HA container.' }
    $supervised = @($mounts | Where-Object { $_.Destination -eq '/etc/services.d/anker-stack' }).Count -eq 1
}
docker image inspect $image --format '{{.Id}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The local HA image is unavailable: $image" }

if ($DryRun) {
    Write-Host "HA config: $configDir"
    Write-Host "Supervised VE/proxy/bridge service: $serviceDir"
    Write-Host "HA image: $image"
    Write-Host "HA browser: http://127.0.0.1:8123/anker-live/overview"
    Write-Host "Read-only VE browser: http://127.0.0.1:45678/"
    Write-Host "Existing container: $exists; supervised: $supervised; recreate requested: $Recreate"
    return
}

# The running HA container sees this bind-mounted file immediately; no restart.
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'export_lp_ha_snapshot.py') -Destination $simSrc -Force

if ($exists -and -not $Recreate) {
    if (-not $supervised) {
        throw 'The existing container has no supervised VE mount. Rerun with -Recreate when ready to replace this local preview container.'
    }
    if ((docker inspect $containerName --format '{{.State.Running}}') -ne 'true') {
        docker start $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the HA container.' }
    }
    return
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'modbus_502_proxy.py') -Destination $simSrc -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 've_bridge.py') -Destination $simSrc -Force

if ($exists) {
    if ((docker inspect $containerName --format '{{.State.Running}}') -eq 'true') {
        docker stop --time 15 $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the HA container.' }
    }
    docker rm $containerName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not remove the stopped HA container.' }
}

docker run -d --name $containerName --restart unless-stopped --pull never `
    --mount "type=bind,source=$configDir,target=/config" `
    --mount "type=bind,source=$serviceDir,target=/etc/services.d/anker-stack,readonly" `
    -p '127.0.0.1:8123:8123' -p '127.0.0.1:45678:45680' $image | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not create the supervised HA container.' }
Write-Host 'HA and the supervised VE/proxy/bridge service are starting.'
