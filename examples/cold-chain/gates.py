r"""Runs the Archytan Lite gate for the cold-chain experiment: the published
image as a Linux container, the way Archytan Lite deploys in practice. Everything the
gate reads or writes lives in Docker volumes and moves with docker cp, so it
behaves the same on Windows, macOS and Linux, and a policy change reloads
live with SIGHUP.

The policy the gate runs under is kept in runs/<time>/policy/policy.json, so
a run's record shows exactly what was in force.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

DEFAULT_IMAGE = "ghcr.io/high-archytech-solutions/archytan-lite:2.6.0"


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def keys_volume(gate_dir):
    """The Docker volume holding this demo folder's gate signing key. Named
    after the folder, so two copies of the demo never share a key."""
    digest = hashlib.sha256(os.path.abspath(gate_dir).lower().encode()).hexdigest()[:10]
    return f"archytan-coldchain-keys-{digest}"


def docker(*args, check=True):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("the Docker gate needs Docker on the PATH")
    if check and result.returncode != 0:
        sys.exit(f"docker {args[0]} failed: {result.stderr.strip()}")
    return result


def key_in_volume(gate_dir, image):
    """Whether this folder's keys volume holds a signing key. The probe
    streams the key to nowhere, so it never reaches the host."""
    volume = keys_volume(gate_dir)
    probe = f"{volume}-probe"
    docker("rm", "-f", probe, check=False)
    docker("create", "--name", probe, "--mount", f"type=volume,source={volume},target=/keys,readonly", image)
    try:
        return subprocess.run(["docker", "cp", f"{probe}:/keys/signing_key.pem", "-"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    finally:
        docker("rm", probe, check=False)


def _wait_for_health(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/v1/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    sys.exit(f"the gate didn't come up at {url}")


class _Gate:
    def __init__(self, cfg, run_dir):
        self.cfg = cfg
        self.run_dir = run_dir
        self.policy_dir = os.path.join(run_dir, "policy")
        self.policy_file = os.path.join(self.policy_dir, "policy.json")
        os.makedirs(self.policy_dir, exist_ok=True)
        # Byte for byte, so the policy hash in every receipt is the hash of
        # gate\policy.json itself.
        shutil.copyfile(os.path.join(cfg["gate_dir"], "policy.json"), self.policy_file)

    def write_policy(self, policy):
        with open(self.policy_file, "w", encoding="utf-8") as f:
            json.dump(policy, f, indent=2)
            f.write("\n")

    def current_policy(self):
        with open(self.policy_file, encoding="utf-8") as f:
            return json.load(f)

    def _common_env(self, paths):
        return {
            "ARCHYTAN_LITE_CALLERS_PATH": paths["callers"],
            "ARCHYTAN_LITE_POLICY_PATH": paths["policy"],
            "ARCHYTAN_LITE_SIGNING_KEY_PATH": paths["key"],
            "ARCHYTAN_LITE_DB_PATH": paths["db"],
            "ARCHYTAN_LITE_INSTANCE_URN": self.cfg["instance_urn"],
        }

    def log_text(self):
        path = os.path.join(self.run_dir, "gate.log")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def close(self):
        pass


class DockerGate(_Gate):
    def __init__(self, cfg, run_dir, image):
        super().__init__(cfg, run_dir)
        self.image = image
        self.name = "archytan-coldchain-" + os.path.basename(run_dir)
        self.keys = keys_volume(cfg["gate_dir"])
        last = image.rsplit("/", 1)[-1]
        self.version = last.split("@", 1)[0].split(":", 1)[1] if ":" in last.split("@", 1)[0] else image
        inspect = docker("image", "inspect", image, check=False)
        if inspect.returncode != 0:
            docker("pull", "-q", image)
            inspect = docker("image", "inspect", image)
        info = json.loads(inspect.stdout)[0]
        self.image_id = info["Id"]
        self.digest = (info.get("RepoDigests") or [""])[0]
        self.created = info.get("Created", "")
        self.label = f"Archytan Lite {self.version}, the published image"
        self.started = False

    _PATHS = {"callers": "/data/callers.json", "policy": "/data/policy.json",
              "key": "/keys/signing_key.pem", "db": "/data/lite.db"}

    def _mounts(self):
        return ["--mount", f"type=volume,source={self.name},target=/data",
                "--mount", f"type=volume,source={self.keys},target=/keys,readonly"]

    def _env_args(self, env):
        args = []
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        return args

    def _ensure_key(self):
        if not key_in_volume(self.cfg["gate_dir"], self.image):
            sys.exit("no gate signing key: run setup_gate.py first")

    def start(self):
        self._ensure_key()
        host, port = self.cfg["addr"].rsplit(":", 1)
        env = dict(self._common_env(self._PATHS),
                   ARCHYTAN_LITE_CAPABILITIES="on",
                   # Every simulated store calls from the same address, so the
                   # per-address rate limit is raised for the demo.
                   ARCHYTAN_LITE_RATE_LIMIT_RPS="1000",
                   ARCHYTAN_LITE_RATE_LIMIT_BURST="2000")
        docker("create", "--name", self.name, "-p", f"{host}:{port}:8421",
               *self._mounts(), *self._env_args(env), self.image)
        docker("cp", os.path.join(self.cfg["gate_dir"], "callers.json"), f"{self.name}:/data/callers.json")
        self._copy_policy_in()
        docker("start", self.name)
        self.started = True
        _wait_for_health(self.cfg["url"])

    def _copy_policy_in(self):
        # docker cp returns once the file is in place, so a reload that
        # follows reads the new policy.
        docker("cp", self.policy_file, f"{self.name}:/data/policy.json")

    def _logs(self):
        result = docker("logs", self.name, check=False)
        return result.stdout + result.stderr

    def apply_policy(self, policy):
        self.write_policy(policy)
        before = self._logs().count("policy reloaded successfully")
        self._copy_policy_in()
        docker("kill", "--signal", "HUP", self.name)
        deadline = time.time() + 10
        while time.time() < deadline:
            logs = self._logs()
            if "policy reload FAILED" in logs:
                sys.exit("the gate refused the new policy; see docker logs " + self.name)
            if logs.count("policy reloaded successfully") > before:
                return ("reloads it live with SIGHUP", "no restart; the gate logged the reload")
            time.sleep(0.2)
        sys.exit("the gate didn't report reloading the policy")

    def stop(self):
        if not self.started:
            return
        docker("stop", "-t", "10", self.name, check=False)
        with open(os.path.join(self.run_dir, "gate.log"), "a", encoding="utf-8") as f:
            f.write(self._logs())
        for suffix in ("", "-wal", "-shm"):
            docker("cp", f"{self.name}:/data/lite.db{suffix}", self.run_dir, check=False)
        docker("rm", self.name, check=False)
        self.started = False

    def tool(self, *args):
        env = dict(self._common_env(self._PATHS), ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX=self.cfg["public_key"])
        return subprocess.run(["docker", "run", "--rm", *self._mounts(), *self._env_args(env), self.image, *args],
                              capture_output=True, text=True)

    def export_chain(self, evidence_dir):
        result = self.tool("--export-chain", "/data/evidence")
        if result.returncode != 0:
            sys.exit(f"--export-chain failed: {result.stderr.strip()}")
        helper = f"{self.name}-export"
        docker("create", "--name", helper, "--mount", f"type=volume,source={self.name},target=/data", self.image)
        try:
            docker("cp", f"{helper}:/data/evidence", os.path.dirname(evidence_dir))
        finally:
            docker("rm", helper, check=False)

    def provenance(self):
        record = {"kind": "published image", "image": self.image, "repo_digest": self.digest,
                  "image_id": self.image_id, "image_created": self.created}
        short = self.digest.split("@", 1)[-1][:19] if self.digest else self.image_id[:19]
        return record, f"published image {short}…", True

    def close(self):
        self.stop()
        docker("volume", "rm", self.name, check=False)


# How demo.py can run the gate, by --gate name.
GATES = {"docker": DockerGate}
