# The Archytan Lite MCP challenge, for Windows PowerShell 5.1 and PowerShell 7:
# an agent behind the MCP gate, then the attacks, with nothing installed but
# Docker Desktop.
#
#   powershell -ExecutionPolicy Bypass -File try-mcp.ps1
#
# Same steps as try-mcp.sh. Every answer is checked as it arrives, and the
# script stops at the first one that differs from what it expects. The gate
# keeps running afterwards, on 127.0.0.1 only, for your own attempts.

# Each docker call is checked through its exit code, as in quickstart.ps1.
# Text piped into docker goes out as UTF-8.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$Image = if ($env:IMAGE) { $env:IMAGE } else { 'ghcr.io/high-archytech-solutions/archytan-lite:2.6.1' }
$NodeImage = if ($env:NODE_IMAGE) { $env:NODE_IMAGE } else { 'node:22-slim' }
$Name = 'archytan-mcp-challenge'
$Work = "$Name-work"
$Port = if ($env:PORT) { $env:PORT } else { '8431' }
$Source = 'https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/challenge/challenge.mjs'

function Check($What) { if ($LASTEXITCODE -ne 0) { throw "$What failed" } }

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker isn't running. Start Docker Desktop and run this again." }
if ((docker ps -aq --filter "name=^$Name$") -or (docker volume ls -q --filter "name=^$Name") ) {
    throw "Remove the previous run first: docker rm -f $Name; docker volume rm $Name $Work"
}

Write-Host "== 1. Volumes for the gate's data and the challenge's files, and the gate's signing key"
docker volume create $Name | Out-Null; Check 'docker volume create'
docker volume create $Work | Out-Null; Check 'docker volume create'
$keygen = docker run --rm -v "${Name}:/data" --entrypoint keygen $Image -out /data/signing_key.pem
Check 'keygen'
$PubKey = ($keygen | Select-String '^public key.*: ([0-9a-f]+)$').Matches[0].Groups[1].Value
Write-Host "  public key: $PubKey"

Write-Host "== 2. A credential for the agent, bound to the role files_agent, and the config"
$local = Join-Path $PSScriptRoot 'challenge.mjs'
$script = if ($PSScriptRoot -and (Test-Path $local)) { Get-Content -Raw $local } else { (Invoke-WebRequest $Source -UseBasicParsing).Content }
$script | docker run --rm -i -v "${Work}:/challenge" $NodeImage sh -c 'cat > /challenge/challenge.mjs'
Check 'copying challenge.mjs'
$callergen = docker run --rm --entrypoint callergen $Image -caller-id challenge-agent -role files_agent
Check 'callergen'
$callergen | docker run --rm -i -v "${Work}:/challenge" $NodeImage node /challenge/challenge.mjs setup | ForEach-Object { "  $_" }
Check 'setup'

Write-Host "== 3. Start the gate: credential-bound roles, single-use capabilities, 127.0.0.1 only"
docker run -d --name $Name -p "127.0.0.1:${Port}:8421" -v "${Name}:/data" -v "${Work}:/challenge:ro" `
    -e ARCHYTAN_LITE_CALLERS_PATH=/challenge/config/callers.json `
    -e ARCHYTAN_LITE_POLICY_PATH=/challenge/config/policy.json `
    -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db `
    -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem `
    -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:mcp-challenge `
    -e ARCHYTAN_LITE_CAPABILITIES=on `
    $Image | Out-Null
Check 'starting the gate'
for ($i = 0; $i -lt 60; $i++) {
    docker exec $Name archytan-lite -healthcheck *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Milliseconds 500
}
docker exec $Name archytan-lite -healthcheck *> $null
if ($LASTEXITCODE -ne 0) { docker logs $Name; throw 'the gate did not become healthy' }
Write-Host "  listening on 127.0.0.1:$Port"

# 4 to 6 run in a node container that shares the gate's network, so the MCP
# gate reaches it on loopback, and its process namespace, so the policy
# reload can signal it.
docker run --rm --network "container:$Name" --pid "container:$Name" -v "${Work}:/challenge" `
    -e "PUBKEY=$PubKey" -e npm_config_cache=/challenge/.npm $NodeImage node /challenge/challenge.mjs run
Check 'the challenge run'

Write-Host ''
Write-Host "== 7. The gate's own log says why each attack was refused, and the decision log verifies"
$logs = docker logs $Name 2>&1 | ForEach-Object { "$_" }
$reasons = $logs | Select-String 'escalation attempt refused|does not authorize|already spent|would not be made now' |
    ForEach-Object { ($_.Line -replace '^[0-9/]+ [0-9:]+ (INFO|WARN) ', '' -replace ' trace_id=\S+', '' -replace ' cap_id=\S+', '') }
$reasons | ForEach-Object { Write-Host "  $_" }
foreach ($want in 'escalation attempt refused', 'does not authorize this action', 'already spent', 'would not be made now') {
    if (-not ($reasons -match [regex]::Escape($want))) { throw "unexpected: the gate's log has no `"$want`"" }
}
$verify = docker exec -e "ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX=$PubKey" $Name archytan-lite --verify-chain
Write-Host "  $verify"
if ($verify -notmatch '^chain OK') { throw "unexpected answer, wanted chain OK: $verify" }

$token = (docker run --rm -v "${Work}:/challenge" $NodeImage cat /challenge/config/agent.token).Trim()
Write-Host @"

== Your turn

The gate is still running on 127.0.0.1:$Port with the same policy. The agent's
credential holds the role files_agent:

  $token

Try to get past it: claim another role, spend a capability on something it
wasn't issued for, spend one twice or after the policy changed, or build an
MCP message the MCP gate and the server read differently. For example:

  try { Invoke-RestMethod -Method Post http://127.0.0.1:$Port/v1/authorize ``
    -Headers @{ Authorization = 'Bearer $token' } -ContentType 'application/json' ``
    -Body '{"action":"file.write","actor":{"uid":"you","role":"admin"},"resource":{"type":"file","id":"/challenge/files/readme.txt"},"idempotency_key":"try-1"}'
  } catch { `$_.ErrorDetails.Message }

More attacks to try, and how to put your own MCP host in front of it:
  https://github.com/High-ArchyTech-Solutions/archytan-lite-community/tree/main/challenge

If something gets through, report it privately, never in a public issue:
  the MCP gate:  https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate/security/advisories/new
  the gate:      https://github.com/High-ArchyTech-Solutions/archytan-lite-community/security/advisories/new
Tell us how it went, whatever happened:
  https://github.com/High-ArchyTech-Solutions/archytan-lite-community/issues/new?template=challenge-report.yml

Clean up with: docker rm -f $Name; docker volume rm $Name $Work
"@
