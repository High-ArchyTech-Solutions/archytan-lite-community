# Archytan Lite in about a minute, for Windows PowerShell 5.1 and PowerShell 7:
# start a gate, get a signed ALLOW and a BLOCK, spend a single-use
# capability twice, and verify the decision log.
#
#   powershell -ExecutionPolicy Bypass -File quickstart.ps1
#
# Needs Docker Desktop. The gate listens on 127.0.0.1 only, so the example
# token below never leaves this machine. Every answer is checked as it
# arrives, so the script stops at the first one that differs from what the
# comments say. Same steps as quickstart.sh.

$Image = if ($env:IMAGE) { $env:IMAGE } else { 'ghcr.io/high-archytech-solutions/archytan-lite:2.3.0' }
$Name = 'archytan-quickstart'
$Gate = 'http://127.0.0.1:8421'
$Headers = @{ Authorization = 'Bearer quickstart-token' }

function Invoke-Gate($Path, $Body) {
    # The gate answers a refusal with 403; show that answer instead of throwing.
    $json = $Body | ConvertTo-Json -Compress -Depth 5
    try { Invoke-RestMethod -Method Post -Uri "$Gate$Path" -Headers $Headers -ContentType 'application/json' -Body $json }
    catch { if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message | ConvertFrom-Json } else { throw } }
}
function Show($Answer) { $Answer | ConvertTo-Json -Compress -Depth 5 | Write-Host }
function Expect($Want, $Answer) {
    if ($Answer.decision -ne $Want) { throw "unexpected answer, wanted $Want`: $($Answer | ConvertTo-Json -Compress -Depth 5)" }
}

if (docker volume ls -q --filter "name=^$Name$") {
    throw "Remove the previous run first: docker rm -f $Name; docker volume rm $Name"
}

Write-Host '== 1. Create a volume for the signing key and the decision log'
docker volume create $Name | Out-Null

Write-Host "== 2. Generate the gate's signing key; its public half verifies every receipt"
$keygen = docker run --rm -v "${Name}:/data" --entrypoint keygen $Image -out /data/signing_key.pem
if ($LASTEXITCODE -ne 0) { throw 'keygen failed' }
$PubKey = ($keygen | Select-String '^public key.*: ([0-9a-f]+)$').Matches[0].Groups[1].Value
Write-Host "public key: $PubKey"

Write-Host '== 3. Start the gate with the example policy and single-use capabilities'
docker run -d --name $Name -p 127.0.0.1:8421:8421 -v "${Name}:/data" `
    -e ARCHYTAN_LITE_CALLER_TOKEN=quickstart-token `
    -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db `
    -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem `
    -e ARCHYTAN_LITE_POLICY_PATH=/usr/local/share/archytan-lite/examples/policy.json `
    -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:quickstart `
    -e ARCHYTAN_LITE_CAPABILITIES=on `
    $Image | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'the gate did not start' }
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "$Gate/v1/healthz" | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
}
Show (Invoke-RestMethod "$Gate/v1/healthz")

Write-Host '== 4. An admin asks to delete business biz_42: ALLOW, a signed receipt and a capability'
$allow = Invoke-Gate '/v1/authorize' @{
    action          = 'business.delete'
    actor           = @{ uid = 'u1'; role = 'admin' }
    resource        = @{ type = 'business'; id = 'biz_42' }
    idempotency_key = 'quickstart-1'
}
Show $allow; Expect 'ALLOW' $allow

Write-Host '== 5. An owner asks for the same thing: BLOCK, and the refusal is logged too'
$block = Invoke-Gate '/v1/authorize' @{
    action          = 'business.delete'
    actor           = @{ uid = 'u2'; role = 'owner' }
    resource        = @{ type = 'business'; id = 'biz_42' }
    idempotency_key = 'quickstart-2'
}
Show $block; Expect 'BLOCK' $block

Write-Host '== 6. Spend the capability right before acting: the first redeem works, a second is refused'
$redeem = @{
    token         = $allow.capability.token
    action        = 'business.delete'
    resource_type = 'business'
    resource_id   = 'biz_42'
}
$first = Invoke-Gate '/v1/redeem' $redeem
Show $first; Expect 'ALLOW' $first
$second = Invoke-Gate '/v1/redeem' $redeem
Show $second; Expect 'BLOCK' $second

Write-Host '== 7. Verify the log: every receipt signed, none edited, reordered or removed'
$verify = docker exec -e "ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX=$PubKey" $Name archytan-lite --verify-chain
Write-Host $verify
if ($verify -notmatch 'chain OK: 2 receipt\(s\)') { throw "unexpected answer, wanted chain OK: $verify" }

Write-Host ''
Write-Host "Done. Clean up with: docker rm -f $Name; docker volume rm $Name"
