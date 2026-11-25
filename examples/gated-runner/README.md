# The gated runner

An example deployment where, as shipped, an agent's only route to its
workspace is the runner, which acts only on capabilities from Archytan Lite.
That holds as long as nothing else on the Docker host, such as the Docker API
over TCP, listens on the one host address the agent can reach (see [What holds
it](#what-holds-it)). The runner doesn't run a command or write a file until
it has spent a capability for every path the command names. A command granted
`code.run` (running code; see [How commands are
sorted](#how-commands-are-sorted)) can still change anything in the workspace
once it runs (see [Limits](#limits)).

Archytan Lite is the gate: it decides, per caller, action and resource,
whether something is allowed (see the [main README](../../README.md)). When it
allows something it issues a **capability**, a single-use token bound to one
action on one resource that expires after 30 seconds by default. Whoever acts
**redeems** (spends) the capability with the gate first; the gate refuses a
token that is forged, expired, already spent, or for a different action or
resource.

This example has four containers:

```
agent ──authorize──▶ gate (Archytan Lite)
  │                    ▲
  └──── run ──▶ runner ┘ redeems, with its own credential
                  │
                  └─ Unix socket, on a volume only these two mount
                  ▼
               sandbox: the executor, no network, read-only root,
                        the workspace at /work
```

- **gate**: Archytan Lite, with capabilities on
  (`ARCHYTAN_LITE_CAPABILITIES=on`).
- **runner**: `archytan-lite-runner`, the HTTP API the agent calls.
- **sandbox**: where commands run. Its first process is the **executor**
  (`archytan-lite-runner exec`, as long as Docker adds no init; see
  [What holds it](#what-holds-it)), which runs what the runner asks.
- **agent**: a stand-in for your agent, with Python and `agent/gated.py`.

## Try it

You need Docker with Compose v2, a bash shell (Git Bash works on Windows; the
scripts set `MSYS_NO_PATHCONV=1` themselves, and Docker commands of your own
with an argument starting with `/` need it too, so the path reaches Docker
unchanged), and network access the first time, to pull the Archytan Lite and
Python images and build the sandbox and agent images. The default image is
Archytan Lite 2.8.0, the first release with the runner. To use one you built
from source, run `docker build -t archytan-lite:dev .` at the root of this
repository (two folders up), come back here, and run
`export LITE_IMAGE=archytan-lite:dev` so the commands below use it (the
`callergen` and `keygen` examples under "Your own deployment" name the
published image). From this folder (`examples/gated-runner`):

```sh
./setup.sh                     # the gate's signing key and two credentials
docker compose up -d --build --wait
docker compose exec -T agent python gated.py ls -la
```

`setup.sh` writes to `./secrets` (the `STATE_DIR` variable changes that): the
agent's and the runner's credentials, the gate's public key, and
`callers/callers.json`, the callers file that registers the credentials with
the gate (each with a role and a digest of its token). It puts the gate's
signing key in the `gate-data` volume.

Run again, `setup.sh` keeps what exists. If `public.key` is gone it reads it
back from the signing key; if a credential or the callers file is gone it
makes both credentials again and restarts a running gate, runner and agent
(the gate and the runner read them only when they start, and a container
sees a token file made anew only after it restarts).

`docker compose down` stops everything and keeps the workspace, the signing
key and the receipts (the gate's signed record of each decision);
`docker compose down -v` deletes those volumes too, so run `./setup.sh` again
(it makes a new signing key) before the next `up`. `./secrets` stays until you
remove it.

The **workspace** is the folder commands act on, `/work` in the sandbox. It
starts empty, so `ls -la` lists only `.` and `..` under its `total` line. To
put a file in it, go through the gate the same way:

```sh
docker compose exec -T agent python -c "import gated; print(gated.write('notes.md', '# Notes'))"
docker compose exec -T agent python gated.py cat notes.md
docker compose exec -T agent python gated.py rm notes.md     # refused: file.delete
```

A plan is the runner's list of what a command needs: its action and every path
it names. A decision is `ALLOW` (every capability was spent and the sandbox
was asked to act; the status table below says when it still didn't run) or
`BLOCK` (refused; nothing ran). `gated.write` and `gated.run` return a status
and an answer:

- the runner's answer from `/v1/run`, like
  `(200, {... 'decision': 'ALLOW' ...})`, or another of its answers: 403, 500
  or 503 with `'decision': 'BLOCK'` (nothing ran, though a capability may
  already be spent), or 502 with `'decision': 'ALLOW'` (every capability was
  spent and the command may have run); the status table below says when;
- 403 with `'decision': 'BLOCK'` when the plan or a capability is refused
  before `/v1/run` is called. The gate gives a bad or missing credential and a
  request it can't read the same 403 as a policy refusal, so a refusal from
  the gate ends with its trace id (an id the gate gives each request and puts
  on its log lines for it). In `docker compose logs gate`, the
  `authorize: rejected` line with that `trace_id` says which: its `reason` is
  the credential or body problem, or `policy decision: BLOCK` for a refusal by
  policy (a resource outside the role's grants, or a role the credential
  doesn't hold, also gets a line of its own). A gate without
  `ARCHYTAN_LITE_CAPABILITIES=on` allows but issues no capability, and `gated`
  reports that as a BLOCK naming the setting;
- status 0 with `{'error': ...}` when the gate or the runner can't be reached
  ("Archytan Lite didn't answer" for the gate); after `/v1/run` was sent it can
  also mean the connection was lost once the capabilities were spent, so the
  command may have run;
- otherwise the status of whichever answered without deciding, with
  `{'error': ...}`: for example 429 when rate-limited, a 5xx from the gate, or
  the runner's 400 for a malformed request. `gated` puts no decision in
  these answers.

The refused `rm` prints
`[BLOCK] Archytan Lite refused file.delete on /work (gate trace trc_...)`
and exits 1. The refusal is for `file.delete`, which the example policy grants
the agent's role nowhere; it names `/work` because the working folder (the
folder a command runs in, `/work` unless it names another) is the first path
every command asks about.

`./test-attacks.sh` starts a separate copy of the whole example, under its
own Compose project (`archytan-gated-runner-test`) and state folder
(`.test-state`). From inside that copy's agent container it tries these:

- the Docker socket, the workspace, the executor's socket, the sandbox's
  address, whether the host's address answers, the default Docker API ports on
  that address and by the host's name, a service on the host's own network
  (reported, since the agent can reach it), a port the host publishes (with a
  control: a container on Docker's default network must reach it), and the
  internet;
- a run with no capabilities, with replayed or forged ones, with one issued
  for another path or another action, with ones the agent already spent,
  and without one for a folder glued to an option (`cp -tDIR`);
- a request body with a key twice;
- the gate refusing what the policy doesn't grant (`file.delete`, inline
  code, a network program), with its trace id in the refusal;
- reading and writing through a symbolic link;
- a process left running after its command, code in the sandbox reaching for
  the network, code taking the executor's socket, and code asking the
  executor directly;
- the gate stopped between asking for capabilities and spending them, and
  the client then reporting that the gate didn't answer (status 0).

It also runs allowed commands, which must succeed: writing `hello.txt` and
reading it back, and the helper scripts some attempts write and run. Most
attempts are built to create a file named `canary-*` (a marker that shows the
attempt got through) if they get through; the delete attempt goes after
`hello.txt`, a file the agent wrote earlier. Afterwards the script checks the
workspace from inside the sandbox. It prints `ALL PASSED` and exits 0 only if
the allowed commands worked, every attempt failed, no canary exists and
`hello.txt` is still there; if an attempt gets through or a check fails, it
prints `FAILED` and exits 1, and the `FAIL` lines above it say which. If the
test copy fails to start, or a Docker step outside the attacks fails (starting
its listeners, stopping or restarting the gate, listing the workspace), it
stops at that error with a nonzero exit and prints neither. It removes its
copy and state when it ends, and keeps the images it built (`KEEP=1` leaves
the copy and state; remove them later with
`COMPOSE_PROJECT_NAME=archytan-gated-runner-test STATE_DIR=./.test-state docker compose down -v`
and `rm -rf .test-state`), and it never touches a deployment you started
yourself. It needs ports 18997 and 18998 free on the host, for two throwaway
listeners.

## How a command runs

1. The agent sends the command to the runner's `POST /v1/plan`. The runner
   answers with the plan: the action (`file.read`, `file.write`,
   `file.delete`, `code.run`, `net.use` or `exec.unknown`) and every path the
   command names, the working folder included (a write's plan is the file's
   path alone).
2. The agent asks Archytan Lite for a capability for that action on each path:
   `POST /v1/authorize` on the gate, with its own token as
   `Authorization: Bearer`, and a body naming the action, the actor
   (`{"uid": "agent", "role": "assistant"}`, with the role the credential
   holds), the resource (`{"type": "path", "id": "/work/notes.md"}`) and a
   fresh `idempotency_key` (any unique string the caller makes up). A retry
   with the same key gets the same capability back. A retry while the first
   request is still being decided gets 409. Once the gate has cleared the
   expired capability from its database (at least an hour after it expires by
   default, `ARCHYTAN_LITE_CAPABILITY_RETENTION`), a retry gets 410 and needs
   a new key. An ALLOW carries the token in `capability.token`; it must be
   spent within 30 seconds by default (`ARCHYTAN_LITE_CAPABILITY_TTL` on the
   gate). The main README's [Call it from your
   code](../../README.md#call-it-from-your-code) section points to the
   clients that make it.
3. The agent sends the command and the capabilities to `POST /v1/run`. The
   runner works out the action and paths again, from the command it's
   about to run; it never takes them from the caller. Then:
   - it asks the executor to check the command without running it: the
     program is installed, the working folder exists, and no path goes
     through a symbolic link. A refusal here spends nothing;
   - it redeems every capability with Archytan Lite, one by one;
   - it asks the executor to run the command.

A capability for reading `/work/a` can't be spent on deleting `/work/a` or on
reading `/work/b`: the runner names the action and path, and the gate checks
the token against them. If any capability is refused, or the gate doesn't
answer, the command doesn't run. Capabilities spent before the refusal stay
spent; ask for new ones.

`agent/gated.py` does all three steps in one call (`gated.run`,
`gated.write`), with Python's standard library only.

## Held actions

If the gate's policy holds an action for a person's approval (Archytan Lite
2.9.0, `hold` in the policy), the gate answers `/v1/authorize` with 202 and
no capability. `agent/gated.py` treats that as "not yet": it asks for every
path first, and if any is held it reads each hold (`GET /v1/holds/{id}`, with
the agent's token, every 2 seconds) until none is open. Nothing runs while
it waits, and nothing runs unless every held path is approved. If any hold
is denied, expires, is refused by the gate or isn't answered in time, the
whole command is refused, and the reason names the path and the hold's
state.

When all are approved, the agent asks again for the paths that weren't held
(a capability lasts 30 seconds from when it's minted, and the wait may have
used that up), and then collects each held path by sending its original
request again, same body and same `idempotency_key`. Only then are the
capabilities handed to the runner. The agent doesn't verify receipt
signatures, but it accepts a 202 only as a hold with a hold id that matches
a HOLD receipt; any other 202 is a refusal.

The agent waits at most `HOLD_WAIT_SECONDS` (default 300) from when it starts
waiting, or until the earliest hold expires, whichever comes first. `0` means
don't wait: a held action is refused at once. `HOLD_POLL_SECONDS` (default 2)
sets how often holds are read. The example's own policy holds nothing; the
waiting logic is tested without Docker by `python -I agent/test_gated_holds.py`.

## The runner's API

The runner doesn't authenticate callers. Anything that can reach it (in this
example, anything on the `agents` Compose network, which the agent, the gate
and the runner share) can call it, and a command runs
only if the caller sends a capability for every path. Request bodies are
JSON, read strictly: an unknown key (one differing only in case counts), a key
given twice, invalid UTF-8, an unpaired `\u` surrogate escape, anything after
the object, or a body over about 2 MiB is refused with 400.

`POST /v1/plan` and `POST /v1/run` take exactly one of `command` and `write`
(`stdin`, optional, goes only with a command):

```json
{"command": {"program": "grep", "args": ["-n", "TODO"], "cwd": "/work"},
 "stdin": "line one\nTODO: line two\n",
 "capabilities": {"/work": "<token>"}}
```

```json
{"write": {"path": "notes.md", "content": "# Notes\n"},
 "capabilities": {"/work/notes.md": "<token>"}}
```

- A write replaces the whole file in place; a new file gets mode 644 (with the
  usual umask).
- `cwd` defaults to the workspace (`/work`), must be absolute, and must be in
  the one form any path must have (see the "Approved paths" paragraph under
  [What holds it](#what-holds-it)). A `cwd` of `/` is refused (a path that
  names only the root). The workspace setting must have that form too, and not
  be `/`; the runner won't start otherwise. An argument can't contain a NUL
  byte, and `stdin` goes only with a command (with a write it's a 400). A
  relative write path is joined to the workspace, after a leading `./` is
  dropped; a write path ending in `/` is refused. The folder a write goes into
  must already exist, and the target, if it exists, must be a regular file.
- `capabilities` maps each path in the plan to its token. `/v1/plan` ignores
  it.
- Limits: 64 arguments of up to 4096 bytes each, 32 paths per command (the
  working folder included), 1024 bytes per path (a longer path is refused
  before the gate is asked, since a redemption carries the path twice and
  Archytan Lite reads at most 4096 bytes of a request), 256 KiB of `stdin`,
  256 KiB of write content, and about 2 MiB for the whole body, so a request
  at every limit at once, full of characters JSON escapes, gets a 400 before
  anything is spent.

`/v1/plan` answers 200 with
`{"action", "resource_type": "path", "resources", "refused"}`; `refused` is
present, with the reason, when the runner won't run the command whatever the
gate says. A malformed plan request gets 400, and a rate-limited one 429, as
on `/v1/run`.

`/v1/run` answers with these fields (`reason`, `missing`, `redeemed` and
`result` only when set):

```
decision, reason, action, resource_type, resources, missing, redeemed, result
```

Each `redeemed` entry is `{"resource_id", "cap_id", "receipt_id"}`; the
`receipt_id` is the gate's receipt (its signed record of a decision; see
"What's on record" under [Limits](#limits)) for the authorization that
capability came from.

| Status | Decision | When |
|---|---|---|
| 200 | `ALLOW` | Every capability was spent and the executor was asked to act. `result` holds `exit_code`, `stdout`, `stderr`, and `stdout_truncated`, `stderr_truncated`, `timed_out` when they apply. A command stopped by a signal has exit code 128 plus the signal. If `result.error` is set, the executor couldn't do it after all and the capabilities are spent: a program that wouldn't start didn't run, and a write that failed partway (a full disk, say) may have left the file created, empty or partly written. |
| 400 | | Malformed request. The body is only `{"error": "..."}`. |
| 403 | `BLOCK` | Refused: the plan (`reason`), a missing capability (`missing`), the executor's check, or Archytan Lite refusing a capability. `redeemed` lists any capabilities spent before the refusal. A refused redemption's `reason` carries the gate's error, or its trace id when the gate gave none. The gate logs that trace id on a `redeem: capability refused` line when the grant no longer holds (the policy, the callers file or the caller's role changed since it was issued), on an `authorize: rejected` line for another refusal, and not at all when it rate-limited the redemption. |
| 429 | | Rate limited by the runner. The body is only `{"error": "..."}`. |
| 500 | `BLOCK` | The runner couldn't build its request to the gate. It checks `ARCHYTAN_RUNNER_GATE_URL` at startup, so this shouldn't happen; if it did, it would fail on the first path, before anything is spent. |
| 502 | `ALLOW` | Every capability was spent, then the executor didn't answer. The command may or may not have run. |
| 503 | `BLOCK` | The executor didn't answer the check (nothing spent); the gate didn't answer or answered with a server error (`redeemed` lists any capabilities already spent); or another command held the runner for 10 seconds. |

Two of the gate's own rules can refuse a redemption. First, the gate
rate-limits each address (the agent's requests for capabilities count against
the agent's address in the same way), by default 20 requests per second with
bursts of 40 (`ARCHYTAN_LITE_RATE_LIMIT_RPS` and
`ARCHYTAN_LITE_RATE_LIMIT_BURST`), and every path is one redemption from the
runner's address. The runner caps a command at 32 paths so one command fits in
the default burst. The burst is shared by every command from the runner's
address and refills at 20 a second, so commands that name many paths, sent
close together, can run out of it: the gate refuses a redemption partway
through, the runner answers 403, and the capabilities already spent stay
spent. A busy deployment should raise the gate's limits or pace its commands.
(The runner's own rate limit, `ARCHYTAN_RUNNER_RATE_LIMIT_RPS`, is a separate
limiter with the same default numbers; it answers 429.)

Second, the gate checks each grant again when it's spent: a policy reload
between asking and running refuses a capability the new policy wouldn't grant.
(A gate in observe mode, `ARCHYTAN_LITE_MODE=observe` with
`ARCHYTAN_LITE_OBSERVE_MODE_CONFIRM`, logs a refusal that comes from the
policy and redeems anyway. It also issues a capability for a request its
policy refuses (all but a role escalation or a path that could be read more
than one way), so the agent and the runner could then get one for what the
policy doesn't grant. This example's gate runs in the default enforce mode,
and its protections need it. `ARCHYTAN_LITE_MODE` in the main README's
[Configuration](../../README.md#configuration) table explains it.)

`GET /v1/healthz` answers 200 when the executor answers and the gate's own
health check answers 200, 503 otherwise, with `{"status", "executor", "gate"}`.

## The example policy

A caller's **role** is what the policy grants actions to. The gate takes it
from the caller's credential, which holds exactly one role: a request body
that claims another role is refused as an escalation attempt (see [Bind roles to
credentials](../../README.md#bind-roles-to-credentials-before-an-agent-calls-the-gate)
in the main README). `config/policy.json` grants the agent's role, `assistant`,
`file.read`, `file.write` and `code.run` on `/work/**`. `file.delete`,
`net.use` and `exec.unknown` are granted only to `owner`, which no credential
holds here, so the gate refuses them. The runner's role, `runner`, appears in
no grant. Edit the policy, then reload it without a restart:
`docker compose kill -s HUP gate`. If the edited policy doesn't load, the gate
logs "policy reload FAILED" and keeps the old one, so check
`docker compose logs gate` after a reload. The callers file is read only at
startup, so a new credential needs `docker compose restart gate`.

## What holds it

These hold for this example as shipped. They depend on the settings in
`docker-compose.yml`, `sandbox/Dockerfile` and `config/policy.json`, on Docker
adding no init of its own (below), and on the gate staying in its default
enforce mode; a deployment of your own has to copy them all, including the
sandbox's limits of 256 processes, 1 GB of memory and 2 CPUs.

**Apart from the host's own address, the agent's only routes are the gate and
the runner.** The Docker host here is the machine the Docker engine runs on
(on Docker Desktop, its Linux VM), and the results below are for that VM. A
service on Windows or macOS itself wasn't tested from the agent's network;
check yours. The agent container has no Docker socket and doesn't mount the
workspace. Its Compose network, `agents`, is `internal: true`: no route to the
internet, and a port the Docker host publishes on all its addresses (Docker's
default) isn't reachable from it (measured on Docker Desktop, where a
container on Docker's default network does reach it). The host's own address
on that network (its gateway;
`docker network inspect archytan-gated-runner_agents` shows it) still answers,
though, and a service listening on the Docker host's own network was reachable
from the agent (measured on Docker Desktop; `test-attacks.sh` reports both on
every run, so check your host). Keep the Docker API off TCP, or on the host's
loopback only, and treat any other service on the host as reachable.
`test-attacks.sh` reports whether the address answers (a closed port on it
refusing a connection at once; a NOTE if it doesn't), reports a service it
starts on the host's own network (a NOTE when the agent reaches it, as it did
on Docker Desktop when measured), and checks that the default Docker API
ports, 2375 and 2376, can't be reached there or by the name
`host.docker.internal`, and that the agent can't reach a port the host
publishes on all its addresses while a container on Docker's default network
can. A port published to one specific host address isn't tested; check it on
your host. A real agent needs its model's API. A second network with egress
gives it that, and on Docker's default network a port the host publishes is
reachable (the control in `test-attacks.sh` shows it). Assume the same on a
network you add for egress, and any service listening on the host's addresses
too, so the protections in this paragraph then hold only through your own
firewall rules. Neither network reaches the sandbox, which has no network.

**The runner can't grant anything.** Its own credential's role, `runner`,
appears in no grant, so in the gate's default enforce mode it can't obtain a
capability of its own (observe mode would issue it one). The gate lets it
spend any capability the gate issued; here the agent is the only caller that
can obtain one. The agent can spend its own capabilities at the gate too,
which runs nothing and leaves them spent. The runner doesn't authenticate
callers, so anything on the `agents` network can call it, and still needs
capabilities for every path.

**The sandbox has no network** (`network_mode: none`). The runner reaches the
executor only through a Unix socket on the `runner-socket` volume, which only
the runner and the sandbox mount. Code running in the sandbox has no network
connection to the gate, the runner, the agent or the internet.

**Where code in the sandbox can write.** The root filesystem is read-only
(`read_only: true`), with every Linux capability dropped (`cap_drop: [ALL]`).
Writable: the workspace at `/work`, in-memory folders at `/tmp` and
`/home/agent` (64 MB each) and `/dev/shm` (Docker's default size), and the
socket folder `/run/archytan-runner`. Commands run as the same user as the
executor, so code can reach the socket, and delete or replace it. Asked
directly, the executor refuses requests from inside the sandbox, apart from a
health-check ping (the kernel tells it which process is asking, and the
runner's is one the sandbox can't see). That holds even if the executor isn't
the sandbox's first process. After every command, before it answers the
runner, the executor checks the socket is still the one it made and makes a
new one if not. A run holds the runner until that answer comes, so the next
command reaches the real executor, or is refused if the socket couldn't be
remade (the executor logs it). That needs the executor to be the sandbox's
first process (below), which leaves nothing running that could replace the
socket again. While a command runs, a replaced socket could at most answer the
runner's health check.

**Approved paths are the paths the command's arguments name.** The runner
lists every argument that might name a file, resolved the way the program
resolves it: relative to the working folder, with a leading `./` and any
trailing `/` dropped and `.` meaning the working folder. Nothing else is
cleaned. A path must be in the one form Archytan Lite accepts for a resource
id (the name of what a capability is for; here, a path), or the runner refuses
the whole command itself, before it asks the gate: letters, digits and
combining marks from any script, the separator `/`, and only
`space - _ . ~ + = , @ : # ( )` besides. So `..`, a doubled `/` between names
(`a//b`), globs, quotes, `$ ; | & < > ! ^ %`, a name ending in a dot or
starting or ending with a space, and Windows device names like `nul` are all
refused. That applies to every argument that could name a file:
`git commit -m "Fix bug!"` is refused for its `!`. A named path that goes
through a symbolic link is refused too, so a link can't point an approved name
somewhere else. A program can still open a file its arguments don't name; see
[Limits](#limits).

**Commands leave no process running their own code, if the executor is the
sandbox's first process.** That needs no `--init` on the container and no
default init in the Docker daemon (`"init": true` in `daemon.json`): either
puts a small init program first, as PID 1, in the executor's place. Neither is
set here. To check, `docker compose exec -T sandbox cat /proc/1/comm` should
print `archytan-lite-r` (the name, cut to 15 characters); the executor's first
log line (`docker compose logs sandbox`) also gives
`sweeps_leftover_processes`: true when it's PID 1 and sweeps, false when it
isn't and leftovers can survive. When a command's own process exits, it stops
the command's process group and then every other process in the sandbox with
SIGKILL, and waits up to 3 seconds for them to go (logging a warning if one
hasn't), all before it answers the runner: until that kill lands, what a
command left running is still part of its run, and can act as the command
could. A killed process runs none of its own code again; at most, one the
kernel is holding in a system call finishes that call, so a write already in
progress can still land in `/work` just after the command ends. A request that
leftover code queued on the executor's socket before the command ended is
refused, as above. Started any other way (with `--init`, say), it can stop
only the command's process group, and a process that leaves the group
survives.

**One command at a time**, with a timeout (`ARCHYTAN_RUNNER_TIMEOUT`, default
60s), and output capped at 1 MiB per stream and returned as text (bytes that
aren't valid UTF-8 come back as U+FFFD). Commands get a fixed `PATH`, `LANG`
and `LC_ALL`, and the executor's `HOME` (`/home/agent` here; `/tmp` if it's
unset or `/`), and nothing else from its environment. Capabilities last 30
seconds by default from when the gate issues them, and the runner spends them
only after the slot (up to 10 seconds; 503 if it's still busy), the check, and
the spending of the ones before: one that has expired by then is refused
(403), and any spent before it stay spent.

## How commands are sorted

The runner never hands a command to a shell: one program, a plain name (a
lowercase letter or digit, then lowercase letters, digits and `. _ + -`; no
path), and its arguments as given. No pipes, redirects, globs, variables or
`~`. (`sh` and `bash` can still be run as programs, with a script file named
with a `/`, as `code.run`.)

- `file.read`: ls, cat, head, tail, wc, grep, tree, file, stat, du, sort,
  uniq, diff, cut, jq, pwd, echo, basename, dirname, md5sum, sha256sum, nl,
  tr, date, find (unless its primaries make it another action, below)
- `file.write`: mkdir, touch, cp, mv, and the read programs when an option
  makes them write (`sort -o` or `--output`, `sort -T` or
  `--temporary-directory`, `tree -o`, `uniq IN OUT`, `file -C` or
  `--compile`, `find -fprint`, `-fprint0`, `-fprintf`, `-fls`)
- `file.delete`: rm, rmdir, `find -delete`
- `code.run`: awk and sed, always; python3, python, sh, bash and perl only in
  the form `program [options] script [arguments]`, with options the runner
  knows (or `python3 -m` and `python -m`, which run a module: `mod.py` or
  `mod/__main__.py` found from the working folder, the standard library or the
  installed packages; some
  modules take code in their arguments, as `timeit` does, or read it from
  standard input, as `code` and `pdb` do, and the runner can't read that code
  first); `find -exec`, `-execdir`, `-ok` and `-okdir` (whose arguments are
  all asked about as paths, so the usual `{}` form, which isn't a valid path,
  is refused), and `sort --compress-program`. **git is `code.run` too**: a
  repository's own config can name programs for git to run (hooks,
  `core.fsmonitor`, diff drivers), and that config is a file in the workspace.
- `net.use`: curl, wget, ssh, scp, sftp, rsync, nc, ncat, ping, pip, pip3,
  npm, apt, apt-get
- `exec.unknown`: everything else; any option the runner doesn't know (below);
  `date -s` or `--set`, or a date operand that isn't `+FORMAT` (that sets the
  clock too); `tree -R` (it writes `00Tree.html` into folders the command
  doesn't name); `find -delete` together with a writing primary; a jq filter
  that mentions `import`, `include` or `modulemeta` (which load modules from a
  search path), or `jq -f`; python3, python, sh, bash and perl in any other
  form: inline code (`-c` and `+c` for sh and bash, `-c` for python, `-e` for
  perl, also inside a group like `-xc`), code read from standard input (no
  script file, `-` in its place, `sh -s`, `python3 -i`, perl's debugger `-d`),
  perl's one-liner switches, a perl `-M` with more than a module name
  (`-M'strict; system(...)'`, `-Mlib=..`), or an option the runner doesn't
  know; options that make a program read the names of files from a file or
  from standard input (`--files0-from` of sort, wc and du,
  `find -files0-from`, `md5sum -c` and `sha256sum -c`, `file -f`), since those
  files are never named; and programs that launch another program (env, xargs,
  timeout, nohup, nice, stdbuf, setsid, chroot, sudo, su)

These lists give the action for each program. They don't say what's installed:
a program the sandbox image doesn't have is refused by the executor's check
("isn't installed in the sandbox") before anything is spent. The example's
sandbox is Python 3.12 on Debian with git, jq, tree and file added.

A command has one action, and it applies to every path the command names:
`cp a b` needs `file.write` on `a` as well as `b`.

**Options the runner knows.** For every program in the `file.read`,
`file.write` and `file.delete` lists but echo, tr, basename, dirname and pwd
(which open nothing, whatever their options), and for find, the runner has a
table of the program's options and what each one's value is (a file, or
something else such as a count, pattern or format). It reads the arguments the
way the program does: grouped short options (`sort -ro out`), a value glued on
(`cp -tDIR`) or in the next argument whatever it starts with (`sort -o -x`
writes `-x`), long options and their abbreviations (`sort --out=x`), `--`
ending the options, a lone `-`, and head's and tail's old count form
(`head -2c f` prints two bytes of `f`). An option that isn't in the table
makes the command `exec.unknown`, so these programs run only with options the
runner can account for. Then every operand and every file value is asked about
as a path, and a list of files is asked about file by file (`file -m a:b`).
Only these are left out: the values of options that take no file (`head -n 5`,
`date -d tomorrow`), the pattern of grep unless `-e` or `-f` gives it, jq's
filter, date's `+FORMAT`, the values of `find -name` and its kin, and the
arguments of echo, tr, basename, dirname and pwd.

The interpreters' options are read the way each one reads them (in sh and
bash, every `o` or `O` in a group takes the next argument: `bash -ox pipefail`
has no script file), and that same reading names the paths: the folders and
files their options name (`perl -I DIR`, `bash --rcfile FILE`), then the
script and every argument after it that might name a file. A shell script must
be named with a `/` in it (`bash ./build.sh`): bash looks a bare name up on
`PATH` when the working folder has no such file, so `bash ldd` would run
`/usr/bin/ldd` while the runner asked about `/work/ldd`. perl gets a small set
of options only: `-w`, `-W`, `-X`, `-T`, `-t`, `-c`, `-I DIR` and `-M` or `-m`
with a module name (an import list can move the module path, as `-Mlib=..`
does). Its one-liner switches (`-n`, `-p`, `-a`, `-l`, `-0`, `-C`, `-i`, `-x`,
`-F`, `-s`, `-g`) and any option with whitespace inside are refused, since
perl reads them in ways that hand it code or paths the runner can't see
(`"-i -e..."` runs `-e`; `-F` pastes its pattern into the code perl
generates). `python3 -X pycache_prefix=...` is refused too. For sed, awk and
git, which are `code.run` with any options, every argument that might name a
file is asked about; the script of sed and awk is left out when it comes
first, after only options the runner knows and their values (sed's `-n`, `-r`,
`-E`, `-s`, `-u`, `-z` and `-l N`; awk's `-F`, `-v` and `-i`); after any other
option it's asked about as a path (`sed -i 's/a/b/' f` asks about
`/work/s/a/b`, and a script with a character a path can't hold is refused),
and so it's after awk's `-f`, `-W` or `-E`, which can name a program file.
Their code can open files the command doesn't name (sed's `w FILE`, awk's
`print > "FILE"`, anything a script does); `code.run` covers anything the
sandbox allows.

## Limits

- **`code.run` is arbitrary code inside the sandbox.** Granting it means the
  code can do anything the sandbox allows, including deleting files in the
  workspace, whatever `file.delete` says. The sandbox is what bounds it. Code
  can also write the sandbox's home folder (`/home/agent`, in memory), which
  some programs read without being told to (jq's `~/.jq`, file's `~/.magic`,
  python3's user site-packages):
  a later command's behaviour can depend on what code left there.
- **A folder stands for everything in it.** Programs that walk folders
  (`ls -R`, `grep -r`, `rm -r`, `cp -r`, `du`, `tree`, `find`) touch what's
  inside a folder you named, and a program given a folder as its target acts
  inside it (`cp a dir` and `mv a dir` make `dir/a`; `diff a dir` reads
  `dir/a`). Only the folder is checked. Grant a folder only where its whole
  contents may be acted on. Programs told to follow links (`grep -R`,
  `find -L` or `-follow`, `cp -L`, `tree -l`, `du -L`, `ls -L`), and
  `diff -r`, which follows them unless given `--no-dereference`, can read
  through a link inside a named folder, and a program writing into a named
  folder writes through a link already there (`cp a dir`, with `dir/a` a
  link); in this sandbox that reaches only the sandbox's own files.
- **Some writes land on names derived from the ones given.** `mkdir -p a/b`
  also makes `a`, and `rmdir -p a/b` also removes it; `cp --parents` makes the
  folders it copies through; `cp -b` and `mv -b` leave a backup named like the
  target with `~` (or `-S`'s suffix) on the end; `file -C -m x` writes `x.mgc`
  in the working folder, and `file -m x` reads `x.mgc` when it's there. The
  capability covers only the named path. With `file.write` on all of `/work`,
  as here, that changes nothing; under a narrower grant, grant the folder
  those names land in.
- **`file.write` includes overwriting and moving.** To keep a file from
  being replaced or moved, don't grant `file.write` on it, and don't grant
  `code.run` anywhere that code could reach it (in this example, all of
  `/work`).
- **Case.** Archytan Lite compares paths exactly, so a grant on one spelling
  doesn't cover another. A folder mounted from Windows or macOS ignores case
  by default, so `/work/SECRET` and `/work/secret` can be one file there
  while the gate treats them as two names. Keep the workspace on a Linux
  volume, as here.
- **A write can land after its command ends.** A process the sweep kills
  while the kernel holds it in a write finishes that write, so it can land
  in `/work` just after the command ends. The runner doesn't check for it.
- **Disk.** Nothing caps how much the workspace holds: `/work` is a named
  volume with no quota, and the 256 KiB limit is per write, so `code.run`
  or a series of writes can fill the Docker host's disk. Give the volume a
  quota in a deployment of your own.
- **The host is trusted.** Anyone who can run Docker on the host can reach the
  sandbox directly. When it makes the token files in `./secrets`, `setup.sh`
  sets them to mode 644, readable by every user on a Linux or macOS host, so
  containers running as other users can read them (on Windows the folder's
  permissions decide); on a shared host, keep `STATE_DIR` somewhere only you
  can read and give the tokens to the containers another way. The runner
  limits what the agent can do to the workspace, within the limits above. It
  places no limit on the host's users.
- **What's on record.** Archytan Lite signs a receipt (a signed record,
  chained to the one before it) for every decision its policy makes, ALLOW or
  BLOCK. Spending a capability is recorded in the gate's log
  (`docker compose logs gate`), outside the receipt chain. The runner logs
  each run request it refuses or carries out (`docker compose logs runner`),
  including a run refused for its plan, but not malformed or rate-limited
  requests, a request abandoned while it waited for the runner, or a call to
  `/v1/plan`. A client that stops before the run, as `gated.py` does when the
  plan or a capability is refused, leaves no trace in the runner's log; when
  the gate's policy refused a request for a capability, its receipt for that
  refusal is in the chain (a refusal the policy decided is a signed decision,
  like an allowed one). A request the gate turned away before deciding (a bad
  credential, a body it couldn't read) and a refused redemption are in the
  gate's log alone, and one it rate-limited isn't recorded at all. The main
  README's [Keep the log honest](../../README.md#keep-the-log-honest) section
  says how to check the receipt chain. A check that passes shows the receipts there are
  unaltered and in order; without checkpoints, which this example doesn't set
  up, it can't show that receipts were cut from the end (see
  [Checkpoints](../../README.md#checkpoints)
  in the main README). The signing key is in the `gate-data` volume, so anyone
  who can run Docker on the host can read it and sign a rewritten chain that
  still passes.

## Your own deployment

Start from this example's `docker-compose.yml` and `config/policy.json` and
change what you need; the notes below say what each part depends on.

**The gate** is configured as in the main README's
[Configuration](../../README.md#configuration) section, plus three things:
`ARCHYTAN_LITE_CAPABILITIES=on`; a callers file (`ARCHYTAN_LITE_CALLERS_PATH`)
with a credential for the agent and one for the runner; and a policy that
grants the agent's role what it may do and the runner's role nothing. Every
action the agent's role gets must be a scoped grant, as in
`config/policy.json`: `grants` with `resource_ids` such as `/work/**` (the main README's [Gate an agent's MCP
tools](../../README.md#2-run-a-gate-the-agent-cannot-talk-its-way-past)
section shows grants). A rule that lists roles in `allowed_roles`, without `grants`,
lets them act on any path, in the sandbox and outside `/work`. The callers
file is `{"version": 1, "callers": [ ... ]}`, one record per credential; the
main README's "Bind roles to credentials" section shows it. Each credential comes from
`callergen`, in the Archytan Lite image, which makes a caller credential and
its callers-file record: run it once with `-caller-id agent -role assistant`
and once with `-caller-id runner -role runner` (below). It prints the token
once and the record to add. With `-json` it prints one object,
`{"token":"...","record":{...}}`: the record goes into the callers file's
`callers` list, and the token to its caller and nowhere else. The runner's
token goes in the file `ARCHYTAN_RUNNER_TOKEN_PATH` names. `setup.sh` shows
both steps:

```sh
docker run --rm --entrypoint callergen ghcr.io/high-archytech-solutions/archytan-lite:2.9.0 \
  -caller-id runner -role runner -json
```

The gate's signing key comes from `keygen`, the same way, written to the
volume the gate keeps its data in. `my-gate-data` is a name of your
choosing; the gate's container has to mount the same volume at `/data`. (In
this example Compose names it `archytan-gated-runner_gate-data`.)

```sh
docker run --rm -v my-gate-data:/data --entrypoint keygen ghcr.io/high-archytech-solutions/archytan-lite:2.9.0 \
  -out /data/signing_key.pem
```

`keygen` prints the public key; keep it (`setup.sh` saves it as `public.key`),
since checking the receipt chain needs it. If it's lost,
`keygen -public-of /data/signing_key.pem`, run the same way, prints it again
(`archytan-lite --verify-chain` with `ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX`,
in the main README's "Keep the log honest" and "Configuration"). The gate reads the callers
file only when it starts. `setup.sh` does all of this for the example.

**The runner** is in the Archytan Lite image but isn't its entrypoint: run
the image with `--entrypoint /usr/local/bin/archytan-lite-runner`. It needs
the gate's URL, its own token, the socket volume, and a network it shares
with the gate and the agent (and nothing else). Mount the volume with
`nocopy` (so Docker doesn't fill it from the runner's image), and start the
runner after the sandbox: Docker gives a new named
volume the owner of the folder it's mounted on in the first container that
mounts it, and the executor, which runs as the sandbox's user, has to be
able to create its socket there.

**The sandbox** can start from any Linux image with the tools your agent
needs:

- copy in the executor:

  ```dockerfile
  COPY --from=ghcr.io/high-archytech-solutions/archytan-lite:2.9.0 \
    /usr/local/bin/archytan-lite-runner /usr/local/bin/
  ```

- make `archytan-lite-runner exec` its entrypoint, and don't start it with
  `--init` (or on a Docker daemon set to add an init by default): the
  executor has to be the first process to stop what commands leave behind;
- mount the workspace at `/work` (a named volume, so it stays a Linux
  filesystem) and the socket volume at `/run/archytan-runner`;
- mount the socket volume in the runner and the sandbox only: the executor
  takes requests from any process it can't see, as the runner's is, so
  another container with that volume could run commands without a
  capability;
- run it as an ordinary user that owns `/work` and `/run/archytan-runner` in
  the image, so fresh volumes mounted there are writable by it, and give
  that user a writable home (here, an in-memory `/home/agent`);
- start it as `docker-compose.yml` does: no network, a read-only root, all
  Linux capabilities dropped, `no-new-privileges`, and limits on processes,
  memory and CPU (here 256, 1 GB and 2).

## Settings

Runner (`archytan-lite-runner`):

| Variable | Meaning |
|---|---|
| `ARCHYTAN_RUNNER_GATE_URL` | Archytan Lite, as an `http://` or `https://` URL, e.g. `http://gate:8421`. Required; the runner won't start with anything else. |
| `ARCHYTAN_RUNNER_TOKEN_PATH` | File holding the runner's own caller token. Required; a missing or empty file stops the runner at startup. |
| `ARCHYTAN_RUNNER_ADDR` | Default `:8431`. |
| `ARCHYTAN_RUNNER_SOCKET` | The executor's socket. Default `/run/archytan-runner/exec.sock`. |
| `ARCHYTAN_RUNNER_WORKSPACE` | Working folder when a command names none, and the folder a relative write path is joined to. Default `/work`; an absolute path other than `/`, in the one form any path must have, or the runner won't start. |
| `ARCHYTAN_RUNNER_TIMEOUT` | Longest a command may run, as a Go duration (`90s`, `2m`). Default `60s`. A value that doesn't parse or isn't positive stops the runner at startup. |
| `ARCHYTAN_RUNNER_RATE_LIMIT_RPS` | Requests per second per caller address, a positive number. Default 20, with bursts of twice that. Anything else stops the runner at startup. |

The runner's other timeouts are fixed. It gives the gate 5 seconds per call
and the executor 10 seconds for its check, and answers 502 if the executor
hasn't answered the command within `ARCHYTAN_RUNNER_TIMEOUT` plus 15 seconds,
once every capability is spent. A client gets 3 seconds to send its headers,
10 to send the whole request (at most about 2 MiB). The runner has the longest
a run can take plus 30 seconds to write its answer: 285 seconds with the
defaults. The longest run is 10 seconds for the slot, 10 for the check, 5 per
path for the gate (32 paths at most), and `ARCHYTAN_RUNNER_TIMEOUT` plus 15
for the command: 255 seconds with the defaults.

Executor (`archytan-lite-runner exec`): `ARCHYTAN_RUNNER_SOCKET`, and
`ARCHYTAN_RUNNER_TIMEOUT`, the longest it lets any command run whatever the
runner asks (set both halves to the same value). Both halves take
`-healthcheck`; the runner's asks `127.0.0.1` on the port of
`ARCHYTAN_RUNNER_ADDR`, so keep the runner listening on all addresses or on
loopback.

The example's own settings: `LITE_IMAGE` (the Archytan Lite image),
`STATE_DIR` (where `setup.sh` writes credentials, default `./secrets`) and
`COMPOSE_PROJECT_NAME` (the Compose project, which names the containers and
volumes; default `archytan-gated-runner`). The stand-in agent reads
`GATE_URL`, `RUNNER_URL` and `AGENT_TOKEN_PATH`; an agent of your own needs
the same three things. It also reads `HOLD_WAIT_SECONDS` and
`HOLD_POLL_SECONDS` (see Held actions), which only matter when the policy holds
an action.
