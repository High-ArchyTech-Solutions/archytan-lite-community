# Archytan Lite

[![Quickstart](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/actions/workflows/quickstart.yml/badge.svg)](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/actions/workflows/quickstart.yml)

A fail-closed authorization gate for AI agents and the backends they act
through. Your code asks the gate before a sensitive action: deleting a
tenant, refunding a customer, unlocking a device. The gate answers with an
Ed25519-signed, hash-chained receipt, and an ALLOW carries a single-use
capability the actuator has to redeem before it acts. Redeeming re-checks
the grant against the policy in force, so revoking access also cuts off
capabilities already issued. One binary and one SQLite file, running on
your own infrastructure, with no telemetry.

This repository is Lite's public home: the quickstart, examples, JSON
Schemas, release notes and issue tracker. The gate is free to run under
the Community license, and the
[Node](https://www.npmjs.com/package/@high-archytech-solutions/archytan-lite)
and [Python](https://pypi.org/project/archytan-lite/) clients are MIT.
Product overview: [high-archy.tech/lite](https://high-archy.tech/lite).

## Quickstart

Needs Docker and curl. It takes a few seconds once the image is
downloaded; the first run adds the image pull:

```sh
curl -fsSLO https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/quickstart/quickstart.sh
bash quickstart.sh
```

In Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/quickstart/quickstart.ps1 -OutFile quickstart.ps1 -UseBasicParsing
powershell -ExecutionPolicy Bypass -File quickstart.ps1
```

The script:

1. creates a Docker volume for the signing key and the decision log;
2. generates the gate's signing key, whose public half verifies every receipt;
3. starts the gate on 127.0.0.1:8421 with the
   [example policy](examples/policy.json) and single-use capabilities on;
4. asks, as an `admin`, to delete business `biz_42`: **ALLOW**, with a
   signed receipt and a capability;
5. asks for the same thing as an `owner`: **BLOCK**, since the policy allows
   only admins, and the refusal is logged too;
6. redeems the capability twice: the first redeem succeeds and the second
   is refused;
7. verifies the decision log with `--verify-chain`: every receipt signed,
   none edited, reordered or removed.

It checks each answer as it arrives and stops at the first one that
differs. This repository runs both scripts against the published image on
every change and once a week; the badge above shows the latest run.

The core of it, if you'd rather type the commands yourself:

```sh
docker volume create archytan-quickstart
docker run --rm -v archytan-quickstart:/data --entrypoint keygen \
  ghcr.io/high-archytech-solutions/archytan-lite:2.3.1 -out /data/signing_key.pem
docker run -d --name archytan-quickstart -p 127.0.0.1:8421:8421 -v archytan-quickstart:/data \
  -e ARCHYTAN_LITE_CALLER_TOKEN=quickstart-token \
  -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db \
  -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem \
  -e ARCHYTAN_LITE_POLICY_PATH=/usr/local/share/archytan-lite/examples/policy.json \
  -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:quickstart \
  -e ARCHYTAN_LITE_CAPABILITIES=on \
  ghcr.io/high-archytech-solutions/archytan-lite:2.3.1

curl -s http://127.0.0.1:8421/v1/authorize \
  -H "Authorization: Bearer quickstart-token" -H "Content-Type: application/json" \
  -d '{"action":"business.delete","actor":{"uid":"u1","role":"admin"},"resource":{"type":"business","id":"biz_42"},"idempotency_key":"try-1"}'
```

Clean up with `docker rm -f archytan-quickstart && docker volume rm archytan-quickstart`.

## Make it yours

### Your own policy

`policy.json` names the actions you gate and the roles allowed to perform
each one. Start from [examples/policy.json](examples/policy.json); editors
that read `$schema` autocomplete and validate it from
[schemas/](schemas). Keep it in a folder and mount the folder read-only, so
an editor that saves by replacing the file is still seen by the gate:

```sh
  -v "$PWD/config:/etc/archytan-lite:ro" \
  -e ARCHYTAN_LITE_POLICY_PATH=/etc/archytan-lite/policy.json \
```

Policy changes apply live. Edit the file and send the gate a SIGHUP:

```sh
docker kill -s HUP archytan-quickstart
```

The gate validates the new file before swapping it in. A file that fails
validation is refused and logged, and the previous policy keeps serving.

### Bind roles to credentials before an agent calls the gate

The quickstart uses one shared token, so the gate takes the role from the
request body. That suits trusted backend code, which derives the role from
a session it already authenticated. It is unsafe for an AI agent, which
could simply claim `admin`. Give each caller its own credential instead:

```sh
docker run --rm --entrypoint callergen ghcr.io/high-archytech-solutions/archytan-lite:2.3.1 \
  -caller-id agent-invoices -role support_agent
```

It prints a token, shown once, for that caller alone, and a record for the
`callers` array of a callers file ([template](examples/callers.json)). Put
the file next to your policy and use `ARCHYTAN_LITE_CALLERS_PATH` in place
of `ARCHYTAN_LITE_CALLER_TOKEN`:

```sh
  -e ARCHYTAN_LITE_CALLERS_PATH=/etc/archytan-lite/callers.json \
```

Now each caller's role comes from its credential. An agent holding the
`support_agent` token that claims `admin` is refused, and the gate logs it:

```
WARN authorize: role escalation attempt refused trace_id=trc_… caller_id=agent-invoices credential_role=support_agent claimed_role=admin action=business.delete
```

Two tools in the image check a policy and callers file before you roll
them out: `policylint` finds what neither file shows on its own (a role
nobody holds, a caller whose role permits nothing), and `policytest` runs
ALLOW and BLOCK scenarios you write against the gate's own decision logic.
Both exit nonzero on a finding, so either can gate a CI pipeline:

```sh
docker run --rm -v "$PWD/config:/etc/archytan-lite:ro" --entrypoint policylint \
  ghcr.io/high-archytech-solutions/archytan-lite:2.3.1 \
  -policy /etc/archytan-lite/policy.json -callers /etc/archytan-lite/callers.json
```

### Call it from your code

```sh
npm install @high-archytech-solutions/archytan-lite
pip install archytan-lite
```

The clients treat anything other than a verified ALLOW that matches the
request as a denial: a timeout, a refused connection, a bad signature, a
receipt for some other action. Their READMEs on
[npm](https://www.npmjs.com/package/@high-archytech-solutions/archytan-lite)
and [PyPI](https://pypi.org/project/archytan-lite/) show the calls.

### Keep the log honest

`--verify-chain` checks every receipt's signature and the hash chain
linking it to the one before, and names the exact receipt where anything
stops matching. The chain proves nothing was altered among the receipts
present; it cannot show receipts deleted from the end. Record the latest
`chain_hash` (it is in every receipt) somewhere off the gate's disk now and
then, so a truncated tail shows up as a gap.

To hand the log to an auditor, `--export-chain <dir>` writes it into a new
or empty folder: `receipts.json` with every receipt exactly as stored, a
manifest signed with the gate's key, and the source of a standalone
verifier, so the auditor can check the log without this binary and without
access to the gate. It reads `ARCHYTAN_LITE_DB_PATH` and
`ARCHYTAN_LITE_SIGNING_KEY_PATH`. Resource ids, actor ids, roles and
idempotency keys are exported as recorded, because the signatures cover
them, so treat an export as personal data.

Both receipt formats have a JSON Schema:
[schemas/receipt.v1.json](schemas/receipt.v1.json) for the receipt an ALLOW
returns, and [schemas/receipts-export.v1.json](schemas/receipts-export.v1.json)
for `receipts.json`. They differ on purpose: an exported receipt carries the
idempotency key that recomputing its `intent_hash` needs, and its
`created_at` text as stored, while the returned one carries `timestamp`
instead. A schema checks shape only; a receipt is genuine when its
signature and its chain verify.

## Gate an agent's MCP tools

`archytan-mcp-gate` sits between an agent host (Claude Desktop, Claude
Code, an IDE agent) and an MCP tool server. The host launches the MCP gate
in place of the server, and the MCP gate launches the server itself, so it
becomes the only path to the tools. For every tool call it asks the gate,
verifies the signed answer, spends the single-use capability, and only then
forwards the call. Tools you have not mapped are hidden from the agent and
refused.

### 1. Download it

Pick your platform: `darwin-arm64`, `darwin-amd64`, `linux-amd64`,
`linux-arm64`, `windows-amd64.exe` or `windows-arm64.exe`.

```sh
base=https://github.com/High-ArchyTech-Solutions/archytan-lite-community/releases/latest/download
curl -fsSLO "$base/archytan-mcp-gate-darwin-arm64"
curl -fsSLO "$base/SHA256SUMS"
grep darwin-arm64 SHA256SUMS | shasum -a 256 -c   # on Linux: sha256sum -c
mv archytan-mcp-gate-darwin-arm64 archytan-mcp-gate && chmod +x archytan-mcp-gate
```

In Windows PowerShell:

```powershell
$base = 'https://github.com/High-ArchyTech-Solutions/archytan-lite-community/releases/latest/download'
Invoke-WebRequest "$base/archytan-mcp-gate-windows-amd64.exe" -OutFile archytan-mcp-gate.exe -UseBasicParsing
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS -UseBasicParsing
$expected = ((Select-String windows-amd64 SHA256SUMS).Line -split '\s+')[0]
if ((Get-FileHash archytan-mcp-gate.exe).Hash -eq $expected) { 'OK' } else { 'MISMATCH: do not run it' }
```

The checksum file is signed by the release workflow, so you can also check
that the binaries came from it:

```sh
curl -fsSLO "$base/SHA256SUMS.cosign.bundle"
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.cosign.bundle \
  --certificate-identity-regexp '^https://github.com/High-ArchyTech-Solutions/archytan-lite/\.github/workflows/mcp-gate-binaries\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A macOS binary downloaded through a browser, rather than curl, is
quarantined until you run `xattr -d com.apple.quarantine archytan-mcp-gate`.

### 2. Run a gate the agent cannot talk its way past

The MCP gate needs single-use capabilities on and a credential for the
agent, so the agent's role comes from its credential. This policy lets an
agent read and list files and keeps writing for an `editor`:

```json
{
  "version": 1,
  "actions": [
    { "action": "file.read",  "allowed_roles": ["files_agent", "editor"] },
    { "action": "file.list",  "allowed_roles": ["files_agent", "editor"] },
    { "action": "file.write", "allowed_roles": ["editor"] }
  ]
}
```

Save it as `config/policy.json`, create the agent's credential with
`callergen -caller-id claude-files -role files_agent` (see
[Bind roles to credentials](#bind-roles-to-credentials-before-an-agent-calls-the-gate)),
put its record in `config/callers.json`, and save the token alone in a
file only you can read, such as `~/.archytan/claude-files.token`. Then
generate the gate's signing key, keeping the public key it prints for step
4, and start the gate:

```sh
docker volume create archytan-lite
docker run --rm -v archytan-lite:/data --entrypoint keygen \
  ghcr.io/high-archytech-solutions/archytan-lite:2.3.1 -out /data/signing_key.pem
docker run -d --name archytan-lite -p 127.0.0.1:8421:8421 \
  -v archytan-lite:/data -v "$PWD/config:/etc/archytan-lite:ro" \
  -e ARCHYTAN_LITE_CALLERS_PATH=/etc/archytan-lite/callers.json \
  -e ARCHYTAN_LITE_POLICY_PATH=/etc/archytan-lite/policy.json \
  -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db \
  -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem \
  -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:agents \
  -e ARCHYTAN_LITE_CAPABILITIES=on \
  ghcr.io/high-archytech-solutions/archytan-lite:2.3.1
```

### 3. Map the tools you want the agent to have

`mcp-gate.json` maps each tool to a policy action and names the argument
that identifies the resource. Every other tool the server offers stays
hidden:

```json
{
  "version": 1,
  "agent_uid": "claude-files",
  "tools": {
    "read_text_file": { "action": "file.read",  "resource_type": "file",      "resource_id_arg": "path" },
    "list_directory": { "action": "file.list",  "resource_type": "directory", "resource_id_arg": "path" },
    "write_file":     { "action": "file.write", "resource_type": "file",      "resource_id_arg": "path" }
  }
}
```

### 4. Point the host at the MCP gate

The same entry works in a Claude Code project's `.mcp.json` and in Claude
Desktop's `claude_desktop_config.json`. Here it wraps the reference
filesystem server:

```json
{
  "mcpServers": {
    "files": {
      "command": "/path/to/archytan-mcp-gate",
      "args": ["--config", "/path/to/mcp-gate.json", "--",
               "npx", "-y", "@modelcontextprotocol/server-filesystem", "/path/to/files"],
      "env": {
        "ARCHYTAN_MCP_GATE_URL": "http://127.0.0.1:8421",
        "ARCHYTAN_MCP_GATE_CALLER_TOKEN_PATH": "/path/to/claude-files.token",
        "ARCHYTAN_MCP_GATE_PUBLIC_KEY_HEX": "<the public key keygen printed>"
      }
    }
  }
}
```

Plain `http` is accepted only to a gate on the same machine, because every
request carries the agent's credential.

### What the agent sees

With the setup above, the filesystem server offers 14 tools and the agent
is shown 3. Reading a file and listing the folder go through, each with a
signed ALLOW and a spent capability. A `write_file` call comes back to the
agent as a tool error it can read, `Refused by Archytan Lite: the gate did
not allow this action`, and the file is untouched. A call to an unmapped
tool such as `move_file` is refused before it reaches the gate. All three
decisions, the refusal included, are in the signed log that
`--verify-chain` checks.

The server only ever serves `/path/to/files`, the folder in its command.
Claude Code tells MCP servers about its workspace through MCP roots, and
the filesystem server would otherwise swap its folder for that workspace.
Since 2.3.1 the MCP gate keeps roots away from the server, so a host
cannot widen what the operator configured; download 2.3.1 or later if you
use an earlier MCP gate. The policy decides which actions the agent may
take, and each receipt records the path, but the policy does not match
paths: the server's own arguments set its reach.

The MCP gate protects what the agent reaches through it. Make it the only
path: list the server only through the MCP gate's entry, and give the
server's own credentials to that entry alone. An agent with a
general-purpose shell can still start the server by hand.

## Configuration

Set by environment variable. A missing required one stops the gate at
startup rather than falling back to a default.

| Variable | Purpose |
|---|---|
| `ARCHYTAN_LITE_DB_PATH` | Required. The SQLite file for receipts and idempotency locks. |
| `ARCHYTAN_LITE_SIGNING_KEY_PATH` | Required. The Ed25519 signing key that `keygen` wrote. |
| `ARCHYTAN_LITE_POLICY_PATH` | Required. Your `policy.json`. |
| `ARCHYTAN_LITE_INSTANCE_URN` | Required. Stamped into every receipt as `operator_urn`. |
| `ARCHYTAN_LITE_CALLERS_PATH` | One of these two. The callers file binding each credential to one role. Recommended. |
| `ARCHYTAN_LITE_CALLER_TOKEN` | One of these two. A single shared token; the role then comes from the request body, unverified. |
| `ARCHYTAN_LITE_ADDR` | Listen address. Default `:8421`. |
| `ARCHYTAN_LITE_CAPABILITIES` | `on` to attach a single-use capability to every ALLOW. Default `off`. |
| `ARCHYTAN_LITE_CAPABILITY_TTL` | How long a capability stays redeemable. Default `30s`. |
| `ARCHYTAN_LITE_RATE_LIMIT_RPS`, `ARCHYTAN_LITE_RATE_LIMIT_BURST` | Per-client request limits before a `429`. Defaults `20` and `40`. |
| `ARCHYTAN_LITE_TRUST_PROXY_HEADERS` | `true` only behind a reverse proxy that sets `X-Forwarded-For` itself. Default `false`. |
| `ARCHYTAN_LITE_MODE` | `observe` logs a would-be BLOCK and allows it, for rolling out a new policy against real traffic. Also needs `ARCHYTAN_LITE_OBSERVE_MODE_CONFIRM` set to the exact phrase the startup error names. Default `enforce`. |
| `ARCHYTAN_LITE_LICENSE_PATH` | A paid plan's license file. A missing or expired license never affects authorization. |
| `ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX` | For `--verify-chain` and `--export-chain`: every public key that ever signed a receipt in this database, comma-separated. `--export-chain` adds the current key itself. |

## Verify the image you pulled

Every release image is signed keyless by the release workflow, and the
signature is logged in Sigstore's public transparency log:

```sh
COSIGN_REPOSITORY=ghcr.io/high-archytech-solutions/archytan-lite-signatures \
cosign verify ghcr.io/high-archytech-solutions/archytan-lite:latest \
  --certificate-identity-regexp '^https://github.com/High-ArchyTech-Solutions/archytan-lite/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Images up to 2.2.0 keep their signature in the image's own package: leave
`COSIGN_REPOSITORY` unset to verify those.

## Releases, questions and security reports

- **New versions and security fixes:** watch this repository and choose
  *Custom → Releases* to be notified. Release notes live under
  [Releases](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/releases).
- **Questions and bugs:** [open an issue](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/issues/new/choose).
- **Vulnerabilities:** report them privately, as [SECURITY.md](SECURITY.md) describes.
- **Adoption numbers:** image pulls, npm and PyPI downloads, and this
  repository's stars and traffic, recorded daily in
  [adoption.csv](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/blob/metrics/adoption.csv)
  on the `metrics` branch.

## License

The contents of this repository (documentation, examples, schemas and
scripts) are MIT-licensed; see [LICENSE](LICENSE). The gate itself, its
container images and binaries, is licensed under the
[Archytan Lite license](ARCHYTAN-LITE-LICENSE.txt): free to run on any
number of self-hosted instances for your own internal business purposes,
with paid plans for support and more at
[high-archy.tech/lite](https://high-archy.tech/lite#pricing).
