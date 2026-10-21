# The MCP challenge

One command puts an agent behind Archytan Lite and the MCP gate, then tries
to get past them. It needs only Docker, and takes about half a minute once
the images are downloaded.

```sh
curl -fsSLO https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/challenge/try-mcp.sh
bash try-mcp.sh
```

In Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/challenge/try-mcp.ps1 -OutFile try-mcp.ps1 -UseBasicParsing
powershell -ExecutionPolicy Bypass -File try-mcp.ps1
```

Run on its own, each script fetches [challenge.mjs](challenge.mjs) from this
folder; from a clone, it uses the copy beside it. This repository runs both
scripts on every change and once a week.

## What it does

1. Creates two Docker volumes, the gate's signing key, and a credential for
   the agent with `callergen`, bound to the role `files_agent`.
2. Writes the policy, where `files_agent` may read and list files and only
   `editor` may write, the callers file, and an `mcp-gate.json` that maps
   three of the filesystem server's tools and names the server.
3. Starts the gate on 127.0.0.1:8431 with credential-bound roles and
   single-use capabilities on.
4. Downloads the MCP gate 2.7.0 for Linux and checks it against the
   release's `SHA256SUMS`.
5. Puts the reference MCP filesystem server (1.0.0) behind the MCP gate and
   runs an agent session through it: two allowed calls, a write the agent's
   role doesn't allow, and a call to a tool that was never mapped.
6. Makes the attacks straight at the gate's API with the agent's own
   credential: a claimed role, a capability spent on another action, on
   another file and twice, and a capability spent after the policy changed.
7. Prints the gate's own log line for each refusal and verifies the signed
   decision log.

Every answer is checked as it arrives, and the script stops at the first
one that differs from what it expects. A run ends like this:

```text
== 5. The agent's session, through the MCP gate
  The filesystem server offers 14 tools. Through the MCP gate the agent sees 3:
  read_text_file, write_file, list_directory

  read_text_file readme.txt                ALLOW    signed receipt rcpt_9d89…, capability spent
  list_directory files/                    ALLOW    signed receipt rcpt_5fdd…, capability spent
  write_file readme.txt                    REFUSED  "Refused by Archytan Lite: the gate did not allow this action", file unchanged
  move_file (not mapped)                   REFUSED  never shown to the agent, never reached the gate or the server

== 6. The attacks, straight at the gate's API with the agent's own credential
  Claim the role editor and ask to write   403      BLOCK
  Ask honestly to read readme.txt          200      ALLOW, capability cap_e3f4…
    spend it on a write to readme.txt      403      BLOCK
    spend it on reading notes.txt          403      BLOCK
    spend it on what it was issued for     200      ALLOW, spent
    spend it again                         403      BLOCK
  Ask to list files/                       200      ALLOW, capability cap_463c…
    take file.list from files_agent, reload          policy reloaded with SIGHUP, no restart
    spend the capability now               403      BLOCK

== 7. The gate's own log says why each attack was refused, and the decision log verifies
  authorize: role escalation attempt refused caller_id=challenge-agent credential_role=files_agent claimed_role=editor action=file.write
  authorize: rejected reason="capability rejected: capability: token does not authorize this action on this resource"
  redeem: capability already spent caller_id=challenge-agent action=file.read resource_id=/challenge/files/readme.txt
  redeem: capability refused: the grant it carries would not be made now ... reason="role \"files_agent\" is no longer allowed action \"file.list\""
  chain OK: 6 receipt(s) verified, unbroken from genesis
```

## Your turn

The gate keeps running with the same policy, and the script prints the
agent's token and the gate's public key. Some places to start:

- **Claim another role.** The script prints a request that claims `admin`.
  Expect a 403, and a log line naming both the role claimed and the role the
  credential holds.
- **Reuse a capability.** Ask for an ALLOW, then redeem its capability with
  another action, another resource, twice, or after it expires (30 seconds).
- **Change the policy under it.** Copy the policy out, edit it, copy it back
  and send the gate a SIGHUP, then spend a capability issued before:

  ```sh
  export MSYS_NO_PATHCONV=1   # Git Bash on Windows only: pass /c paths to Docker as written
  docker run --rm -v archytan-mcp-challenge-work:/c node:22-slim cat /c/config/policy.json > policy.json
  # edit policy.json
  docker run --rm -i -v archytan-mcp-challenge-work:/c node:22-slim sh -c 'cat > /c/config/policy.json' < policy.json
  docker kill -s HUP archytan-mcp-challenge
  ```

- **Put your own agent in front.** Download the
  [MCP gate](https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate#install)
  for your machine, save the printed token to a file, write an
  `mcp-gate.json` that names a tool server on your machine, and point your
  MCP host at the MCP gate with `ARCHYTAN_MCP_GATE_URL=http://127.0.0.1:8431`,
  the token file and the public key. Then ask the agent for things its role
  doesn't allow, or plant instructions in a file it reads.
- **Make the MCP gate and the server disagree.** Build a message the two
  could read differently, the class of bug behind
  [GHSA-9ff5-c4g4-rx92](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/security/advisories/GHSA-9ff5-c4g4-rx92).
  The MCP gate's
  [source](https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate) is
  open, so you can read exactly how it parses.

## What it doesn't cover

- The MCP gate governs only what reaches the tool server through it. An
  agent with a general-purpose shell can start the server by hand; the
  [MCP gate's README](https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate#what-it-doesnt-cover)
  says how to make it the only path.
- The policy decides which actions a role may take, and doesn't match paths:
  `file.read` lets `files_agent` read any file the filesystem server serves.
- It's a local demonstration. The gate listens on 127.0.0.1 over plain
  HTTP, and the script prints the agent's token so you can use it.
- The MCP gate download is checked against the release's `SHA256SUMS`.
  To check that file's signature as well, follow the
  [MCP gate's install steps](https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate#install).

## Tell us what happened

- **Something got through:** report it privately, never in a public issue,
  to the [MCP gate](https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate/security/advisories/new)
  or the [gate](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/security/advisories/new).
  We'll credit you in the advisory if you'd like.
- **Everything else**, from "it all held" to "it stopped at step 3": the
  [challenge report](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/issues/new?template=challenge-report.yml).
- **You'd like help putting it in front of your own MCP server:** write to
  contact@high-archy.tech and we'll set it up with you on a 30-minute call.

## Clean up

```sh
docker rm -f archytan-mcp-challenge
docker volume rm archytan-mcp-challenge archytan-mcp-challenge-work
```
