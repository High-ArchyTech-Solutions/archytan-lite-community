// The Archytan Lite MCP challenge, the part that runs inside a node
// container. try-mcp.sh and try-mcp.ps1 start it twice:
//
//   setup  reads callergen's output on stdin and writes the agent's
//          credential, the policy, the callers file, mcp-gate.json and the
//          files the agent works on, into /challenge.
//   run    shares the gate container's network and process namespace. It
//          downloads the MCP gate and checks it against the release's
//          SHA256SUMS, puts the reference filesystem server behind it, drives
//          an agent session through it, then makes the attacks the challenge
//          names straight against the gate's API.
//
// Every answer is checked as it arrives. The first one that differs from
// what this file says stops the run with a non-zero exit.
//
// Plain Node 22: no packages, so there's nothing to install or trust beyond
// the node image itself.

import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const MCP_GATE_VERSION = "2.8.0";
const FILESYSTEM_SERVER = "@modelcontextprotocol/server-filesystem@1.0.0";
const ROOT = "/challenge";
const CONFIG = `${ROOT}/config`;
const FILES = `${ROOT}/files`;
const GATE = process.env.GATE_URL || "http://127.0.0.1:8421";
const HOST_PORT = process.env.HOST_PORT || "8431";

// Every action is scoped to the files folder: the roles may act on what's in
// it and on nothing else.
const SCOPE = `${FILES}/**`;
const POLICY = {
  version: 1,
  actions: [
    { action: "file.read", grants: [{ roles: ["files_agent", "editor"], resource_ids: [SCOPE] }] },
    { action: "file.list", grants: [{ roles: ["files_agent", "editor"], resource_ids: [SCOPE] }] },
    { action: "file.write", grants: [{ roles: ["editor"], resource_ids: [SCOPE] }] },
  ],
};

function fail(message) {
  console.error(`\nunexpected answer: ${message}`);
  process.exit(1);
}

function expect(condition, message) {
  if (!condition) fail(message);
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

// ---------------------------------------------------------------- setup

async function setup() {
  const out = await readStdin();
  const token = out.match(/store it now:\s+(\S+)/)?.[1];
  // The record is printed as indented JSON over several lines.
  const record = out.match(/\{[^{}]*"token_sha256"[^{}]*\}/)?.[0];
  expect(token && record, `callergen printed something else:\n${out}`);

  mkdirSync(CONFIG, { recursive: true });
  mkdirSync(FILES, { recursive: true });
  writeFileSync(`${CONFIG}/callers.json`, JSON.stringify({ version: 1, callers: [JSON.parse(record)] }, null, 2) + "\n");
  writeFileSync(`${CONFIG}/agent.token`, token + "\n", { mode: 0o600 });
  writeFileSync(`${CONFIG}/policy.json`, JSON.stringify(POLICY, null, 2) + "\n");
  writeFileSync(
    `${CONFIG}/mcp-gate.json`,
    JSON.stringify(
      {
        version: 1,
        agent_uid: "challenge-agent",
        server: { command: "npx", args: ["-y", FILESYSTEM_SERVER, FILES] },
        tools: {
          read_text_file: { action: "file.read", resource_type: "file", resource_id_arg: "path" },
          list_directory: { action: "file.list", resource_type: "directory", resource_id_arg: "path" },
          write_file: { action: "file.write", resource_type: "file", resource_id_arg: "path" },
        },
      },
      null,
      2,
    ) + "\n",
  );
  writeFileSync(`${FILES}/readme.txt`, "The agent may read this file. Its role may not write it.\n");
  writeFileSync(`${FILES}/notes.txt`, "Another file in the same folder.\n");
  console.log("wrote config/policy.json, config/callers.json, config/mcp-gate.json and the agent's token");
}

// ------------------------------------------------------------------ run

async function download(url) {
  const res = await fetch(url);
  expect(res.ok, `download ${url}: HTTP ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

async function fetchMCPGate() {
  const arch = { x64: "amd64", arm64: "arm64" }[process.arch];
  expect(arch, `no MCP gate build for ${process.arch}`);
  const name = `archytan-mcp-gate-linux-${arch}`;
  const base = `https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate/releases/download/v${MCP_GATE_VERSION}`;
  const sums = (await download(`${base}/SHA256SUMS`)).toString("utf8");
  const want = sums.split("\n").find((l) => l.trim().endsWith(` ${name}`))?.split(/\s+/)[0];
  expect(want, `SHA256SUMS has no line for ${name}`);
  const path = `${ROOT}/bin/archytan-mcp-gate`;
  if (existsSync(path) && createHash("sha256").update(readFileSync(path)).digest("hex") === want) return path;
  const bin = await download(`${base}/${name}`);
  const got = createHash("sha256").update(bin).digest("hex");
  expect(got === want, `${name} has SHA-256 ${got}, but the release's SHA256SUMS says ${want}`);
  mkdirSync(`${ROOT}/bin`, { recursive: true });
  writeFileSync(path, bin);
  chmodSync(path, 0o755);
  return path;
}

// A minimal MCP host: newline-delimited JSON-RPC over the child's stdio.
function session(command, args, env = {}) {
  const child = spawn(command, args, { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
  const waiting = new Map();
  const stderr = [];
  let buffered = "";
  let nextID = 0;
  child.stdout.on("data", (data) => {
    buffered += data;
    for (let i; (i = buffered.indexOf("\n")) >= 0; ) {
      const line = buffered.slice(0, i).trim();
      buffered = buffered.slice(i + 1);
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }
      const resolve = waiting.get(message.id);
      if (resolve) {
        waiting.delete(message.id);
        resolve(message);
      }
    }
  });
  child.stderr.on("data", (data) => stderr.push(...data.toString().split("\n").filter(Boolean)));
  const request = (method, params, timeoutMs = 180000) =>
    new Promise((resolve, reject) => {
      const id = ++nextID;
      const timer = setTimeout(() => reject(new Error(`no answer to ${method}`)), timeoutMs);
      waiting.set(id, (m) => {
        clearTimeout(timer);
        resolve(m);
      });
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  const start = async () => {
    const init = await request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "archytan-mcp-challenge", version: "1" },
    });
    expect(init.result, `initialize failed: ${JSON.stringify(init)}`);
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
  };
  const tools = async () => (await request("tools/list", {})).result.tools.map((t) => t.name);
  const call = async (name, args) => {
    const m = await request("tools/call", { name, arguments: args });
    expect(m.result, `tools/call ${name} got no result: ${JSON.stringify(m)}`);
    return { isError: m.result.isError === true, text: m.result.content?.[0]?.text ?? "" };
  };
  const close = () =>
    new Promise((resolve) => {
      child.on("exit", resolve);
      child.stdin.end();
      setTimeout(() => child.kill(), 5000).unref();
    });
  return { start, tools, call, close, stderr };
}

async function api(path, body) {
  const token = readFileSync(`${CONFIG}/agent.token`, "utf8").trim();
  const res = await fetch(`${GATE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

const authorize = (action, role, type, id) =>
  api("/v1/authorize", {
    action,
    actor: { uid: "challenge-agent", role },
    resource: { type, id },
    idempotency_key: randomUUID(),
  });
const redeem = (token, action, type, id) =>
  api("/v1/redeem", { token, action, resource_type: type, resource_id: id });

function row(label, status, detail) {
  console.log(`  ${label.padEnd(40)} ${String(status).padEnd(8)} ${detail}`);
}

// The gate is PID 1 in its container, which this one shares: a SIGHUP
// makes it reload policy.json without a restart.
async function reloadPolicy(policy) {
  writeFileSync(`${CONFIG}/policy.json`, JSON.stringify(policy, null, 2) + "\n");
  process.kill(1, "SIGHUP");
  await new Promise((r) => setTimeout(r, 1500));
}

async function run() {
  const readme = `${FILES}/readme.txt`;
  const notes = `${FILES}/notes.txt`;
  const before = readFileSync(readme, "utf8");

  console.log(`== 4. Download the MCP gate ${MCP_GATE_VERSION} and check it against the release's SHA256SUMS`);
  const gateBin = await fetchMCPGate();
  console.log(`  ${gateBin}: SHA-256 matches the release`);

  console.log("\n== 5. The agent's session, through the MCP gate");
  const direct = session("npx", ["-y", FILESYSTEM_SERVER, FILES], { npm_config_update_notifier: "false" });
  await direct.start();
  const serverTools = await direct.tools();
  await direct.close();

  const agent = session(gateBin, ["--config", `${CONFIG}/mcp-gate.json`], {
    ARCHYTAN_MCP_GATE_URL: GATE,
    ARCHYTAN_MCP_GATE_CALLER_TOKEN_PATH: `${CONFIG}/agent.token`,
    ARCHYTAN_MCP_GATE_PUBLIC_KEY_HEX: process.env.PUBKEY,
    npm_config_update_notifier: "false",
  });
  await agent.start();
  const shown = await agent.tools();
  const mapped = ["read_text_file", "list_directory", "write_file"];
  expect(
    shown.length === mapped.length && mapped.every((t) => shown.includes(t)),
    `the agent should see ${mapped.join(", ")}, and saw ${shown.join(", ")}`,
  );
  const unmapped = serverTools.includes("move_file") ? "move_file" : serverTools.find((t) => !mapped.includes(t));
  console.log(`  The filesystem server offers ${serverTools.length} tools. Through the MCP gate the agent sees ${shown.length}:`);
  console.log(`  ${shown.join(", ")}\n`);

  const read = await agent.call("read_text_file", { path: readme });
  expect(!read.isError && read.text.includes("may read"), `read_text_file: ${read.text}`);
  const list = await agent.call("list_directory", { path: FILES });
  expect(!list.isError && list.text.includes("readme.txt"), `list_directory: ${list.text}`);
  const outside = await agent.call("read_text_file", { path: "/etc/passwd" });
  expect(outside.isError && outside.text.startsWith("Refused by Archytan Lite"), `read_text_file /etc/passwd: ${outside.text}`);
  const write = await agent.call("write_file", { path: readme, content: "overwritten by the agent\n" });
  expect(write.isError && write.text.startsWith("Refused by Archytan Lite"), `write_file: ${write.text}`);
  expect(readFileSync(readme, "utf8") === before, "readme.txt changed although the write was refused");
  const hidden = await agent.call(unmapped, { source: readme, destination: `${FILES}/moved.txt` });
  expect(hidden.isError && existsSync(readme), `${unmapped}: ${hidden.text}`);
  await agent.close();

  const receipt = (tool) => agent.stderr.find((l) => l.includes(`ALLOWED tool="${tool}"`))?.match(/receipt=(\S+?):/)?.[1];
  row("read_text_file readme.txt", "ALLOW", `signed receipt ${receipt("read_text_file")}, capability spent`);
  row("list_directory files/", "ALLOW", `signed receipt ${receipt("list_directory")}, capability spent`);
  row("read_text_file /etc/passwd", "REFUSED", "outside the policy's scope, never reached the server");
  row("write_file readme.txt", "REFUSED", `"${write.text}", file unchanged`);
  row(`${unmapped} (not mapped)`, "REFUSED", "never shown to the agent, never reached the gate or the server");

  console.log("\n== 6. The attacks, straight at the gate's API with the agent's own credential");
  console.log("  The credential holds the role files_agent. Policy: files_agent may read and list inside /challenge/files; only editor may write.\n");

  const claim = await authorize("file.write", "editor", "file", readme);
  expect(claim.status === 403 && claim.body.decision === "BLOCK", `claimed role: ${JSON.stringify(claim)}`);
  row("Claim the role editor and ask to write", claim.status, claim.body.decision);

  const passwd = await authorize("file.read", "files_agent", "file", "/etc/passwd");
  expect(passwd.status === 403 && passwd.body.decision === "BLOCK", `read outside the scope: ${JSON.stringify(passwd)}`);
  row("Ask to read /etc/passwd", passwd.status, `${passwd.body.decision}, outside ${SCOPE}`);
  const climb = await authorize("file.read", "files_agent", "file", `${FILES}/../config/agent.token`);
  expect(climb.status === 403 && climb.body.decision === "BLOCK", `climb out of the scope: ${JSON.stringify(climb)}`);
  row("Ask to read files/../config/agent.token", climb.status, `${climb.body.decision}, refused before any match`);

  const grant = await authorize("file.read", "files_agent", "file", readme);
  expect(grant.status === 200 && grant.body.capability?.token, `honest read: ${JSON.stringify(grant)}`);
  row("Ask honestly to read readme.txt", grant.status, `ALLOW, capability ${grant.body.capability.cap_id}`);
  const cap = grant.body.capability.token;
  const asWrite = await redeem(cap, "file.write", "file", readme);
  expect(asWrite.status === 403, `spend as a write: ${JSON.stringify(asWrite)}`);
  row("  spend it on a write to readme.txt", asWrite.status, asWrite.body.decision);
  const otherFile = await redeem(cap, "file.read", "file", notes);
  expect(otherFile.status === 403, `spend on notes.txt: ${JSON.stringify(otherFile)}`);
  row("  spend it on reading notes.txt", otherFile.status, otherFile.body.decision);
  const honest = await redeem(cap, "file.read", "file", readme);
  expect(honest.status === 200 && honest.body.decision === "ALLOW", `spend as issued: ${JSON.stringify(honest)}`);
  row("  spend it on what it was issued for", honest.status, "ALLOW, spent");
  const again = await redeem(cap, "file.read", "file", readme);
  expect(again.status === 403, `spend twice: ${JSON.stringify(again)}`);
  row("  spend it again", again.status, again.body.decision);

  const listGrant = await authorize("file.list", "files_agent", "directory", FILES);
  expect(listGrant.status === 200 && listGrant.body.capability?.token, `list: ${JSON.stringify(listGrant)}`);
  row("Ask to list files/", listGrant.status, `ALLOW, capability ${listGrant.body.capability.cap_id}`);
  await reloadPolicy({
    ...POLICY,
    actions: POLICY.actions.map((a) =>
      a.action === "file.list" ? { ...a, grants: [{ roles: ["editor"], resource_ids: [SCOPE] }] } : a,
    ),
  });
  const revoked = await redeem(listGrant.body.capability.token, "file.list", "directory", FILES);
  await reloadPolicy(POLICY);
  expect(revoked.status === 403, `spend after the policy changed: ${JSON.stringify(revoked)}`);
  row("  take file.list from files_agent, reload", "", "policy reloaded with SIGHUP, no restart");
  row("  spend the capability now", revoked.status, revoked.body.decision);
  console.log("\n  The policy is back as it was, so the gate is ready for your own attempts.");
}

const phase = process.argv[2];
if (phase === "setup") await setup();
else if (phase === "run") await run();
else fail(`usage: node challenge.mjs setup|run (got ${phase})`);
