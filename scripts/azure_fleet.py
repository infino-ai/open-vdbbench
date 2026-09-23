#!/usr/bin/env python3
"""One self-driving VM per engine on Azure: provision, collect, delete.

Each VM carries its whole leg in cloud-init - the harness scripts, the engine's
settings and the runner - and starts the benchmark at boot. Nothing has to stay
running on the machine that launched it, so a laptop that sleeps, a dropped
network or an ended session does not interrupt a run.

  azure_fleet.py up [engine ...]   provision and start; writes runs/<stamp>/fleet.json
  azure_fleet.py up --into <stamp> [engine ...]   add legs to a run already going
  azure_fleet.py status [stamp]    per-leg progress
  azure_fleet.py verify [stamp]    parse every leg's real args with the real CLI
  azure_fleet.py relaunch <stamp> <engine ...>   re-run legs after a fix
  azure_fleet.py reopen [stamp]    point the SSH rule at this machine's current IP
  azure_fleet.py retire <stamp> <engine ...>   collect a finished leg and free its slot
  azure_fleet.py collect [stamp]   copy results back into runs/<stamp>/
  azure_fleet.py down [stamp]      delete every resource this run created
  azure_fleet.py sweep             delete every benchmark resource, any run

Teardown deletes the resource ids recorded in fleet.json and nothing else, so
the run can share a resource group with unrelated resources.

Env:
  AZURE_RG      resource group the VMs go in  (required)
  LOCATION      region                        (default eastus)
  MACHINE       VM size                       (default from matrix.json)
  TASK_LABEL    groups a sweep into one curve (default open_<UTC date>)
  VDB_REPO/REF  VectorDBBench to install      (default from matrix.json)
  DEADMAN_MIN   Azure auto-deallocate, minutes (default 240)
"""
import base64
import concurrent.futures as cf
import csv
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "harness")
RUNS = os.path.join(ROOT, "runs")
KEY = os.path.join(ROOT, ".secrets", "fleet_key")

RG = os.environ.get("AZURE_RG", "")
LOCATION = os.environ.get("LOCATION", "eastus")
# 240 minutes against a longest measured leg of 67, so a leak is bounded at
# roughly four hours of one wave rather than whatever the calendar allows.
DEADMAN_MIN = os.environ.get("DEADMAN_MIN", "240")
VDB_REPO = os.environ.get("VDB_REPO")
VDB_REF = os.environ.get("VDB_REF")
SSH_USER = "azureuser"
AZ = shutil.which("az") or "az"
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    # A leg under a heavy index build answers the TCP connect and then takes
    # its time sending the SSH banner, which a short timeout reports as a dead
    # host.
    "-o", "ConnectTimeout=30",
    "-o", "ServerAliveInterval=15",
    "-o", "BatchMode=yes",
    "-o", "LogLevel=ERROR",
]

MATRIX = json.load(open(os.path.join(HARNESS, "matrix.json")))
DEFAULTS = MATRIX["defaults"]
BY_ID = {e["id"]: e for e in MATRIX["engines"]}
MACHINE = os.environ.get("MACHINE", DEFAULTS["vm_size"])
# The case is per run, not per matrix. A 10M pass is the same twenty engines on
# the same machine with a different corpus, so it overrides this rather than
# editing the default every 1M run would then inherit.
CASE = os.environ.get("CASE", DEFAULTS["case"])


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def az(*args, parse=True, check=True):
    cmd = [AZ, *args]
    # Without an explicit output format az follows the user's configured
    # default, which may be table or tsv, and the JSON parse then fails.
    if parse and "-o" not in args and "--output" not in args:
        cmd += ["-o", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:4])} failed:\n{p.stderr.strip()}")
    if not parse:
        return p.stdout.strip()
    out = p.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------- ssh key
def ensure_key():
    if not os.path.exists(KEY):
        os.makedirs(os.path.dirname(KEY), exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", KEY, "-N", "", "-q"],
                       check=True)
        log("generated", KEY)
    if platform.system() == "Windows":
        # Windows OpenSSH reads the ACL, not the POSIX bits, and refuses a key
        # any other principal can read.
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", KEY, "/inheritance:r"], capture_output=True)
        subprocess.run(["icacls", KEY, "/grant:r", f"{user}:R"], capture_output=True)
    else:
        os.chmod(KEY, 0o600)
    return open(KEY + ".pub").read().strip()


def my_ip():
    with urllib.request.urlopen("https://api.ipify.org", timeout=15) as r:
        return r.read().decode().strip()


# ----------------------------------------------------------------- cloud-init
def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def read(*parts):
    with open(os.path.join(HARNESS, *parts), encoding="utf-8") as fh:
        return fh.read()


def for_case(engine):
    """The engine as this run's case needs it.

    One corpus can need a different sweep for the same engine. Weaviate reloads
    and rebuilds on every point because its ef is applied at load time, which at
    1M costs twelve minutes a point and at 10M costs hours.
    """
    over = (engine.get("per_case") or {}).get(CASE) or {}
    return {**engine, **over} if over else engine


def leg_env(engine, task_label):
    """The environment run_peer_vdbbench.sh expects, as a sourceable file."""
    engine = for_case(engine)
    args = [str(a) for a in engine.get("args", [])]
    # VDB_ARGS is word-split by the runner, so an empty value disappears and the
    # flag in front of it takes the next flag as its argument. Catch it here
    # rather than in a result file nobody can explain later.
    if "" in args:
        bad = args[args.index("") - 1] if args.index("") else "(first arg)"
        raise SystemExit(f"{engine['id']}: empty value after {bad} in matrix.json; "
                         "drop the flag or give it a value")
    kv = {
        "ENGINE_ID": engine["id"],
        "ENGINE_FN": engine["id"].replace("-", "_"),
        "ENGINE_IMAGE": engine.get("image") or "",
        "VDB_CMD": engine["cmd"],
        "VDB_ARGS": " ".join(args),
        "EF_FLAG": engine.get("ef_flag") or "",
        "EF_SWEEP": " ".join(str(a) for a in engine.get("ef_sweep", [])),
        "RELOAD_EACH_POINT": "yes" if engine.get("reload_each_point") else "no",
        "EF_VALUE_PREFIX": engine.get("ef_value_prefix", ""),
        "PIP_EXTRA": engine.get("pip_extra", ""),
        "APT_EXTRA": engine.get("apt_extra", ""),
        "PIP_PINS": engine.get("pip_pins", ""),
        "CASE": CASE,
        "K": str(DEFAULTS["k"]),
        "NUM_CONC": DEFAULTS["num_concurrency"],
        "CONC_DURATION": str(DEFAULTS["concurrency_duration"]),
        "TASK_LABEL": task_label,
        "VM_SIZE": MACHINE,
        "CLOUD": "azure",
        # The runner writes the repository, ref and commit it actually installed
        # into every result file, so a number and the code that produced it stay
        # together.
        "VDB_REPO": (engine.get("vdb_repo") or VDB_REPO
                     or DEFAULTS.get("vdb_repo") or "infino-ai/VectorDBBench"),
        "VDB_REF": (engine.get("vdb_ref") or VDB_REF
                    or DEFAULTS.get("vdb_ref") or "open-leaderboard"),
        "HARNESS_DIR": "/opt/harness",
        "BENCH_DIR": "/mnt/bench",
    }
    # A client that reads a switch from the environment declares it in the
    # matrix, so the leg records it the way it records every other setting.
    kv.update({k: str(v) for k, v in (engine.get("env") or {}).items()})
    lines = ["# generated by azure_fleet.py"]
    for k, v in kv.items():
        lines.append(f"export {k}={json.dumps(v)}")
    return "\n".join(lines) + "\n"


LEG_SH = """#!/usr/bin/env bash
# Runs this VM's leg, then records the outcome where `status` can read it.
set -uo pipefail
cd /opt/harness
. /opt/harness/leg.env
echo "leg starting $(date -u +%FT%TZ) engine=$ENGINE_ID" > /opt/harness/STARTED
bash /opt/harness/scripts/run_peer_vdbbench.sh
rc=$?
mkdir -p /opt/harness/results
cp -r /tmp/vdb_results/. /opt/harness/results/ 2>/dev/null || true
echo "$rc" > /opt/harness/RC
date -u +%FT%TZ > /opt/harness/DONE
echo "leg finished rc=$rc"
"""

BOOT_SH = """#!/usr/bin/env bash
# cloud-init entry point. Everything the leg needs is already on disk.
set -uo pipefail
exec >>/var/log/vdb-boot.log 2>&1
echo "boot $(date -u +%FT%TZ)"

# No guest-side shutdown. `shutdown -h` halts the OS but leaves the VM
# allocated, which Azure bills exactly like running, and it also makes the
# results unreachable until someone starts the machine again. The billing
# backstop is an Azure auto-shutdown schedule set at provision time, which
# deallocates.

chmod +x /opt/harness/*.sh /opt/harness/scripts/*.sh 2>/dev/null || true

# Benchmark data goes on the machine's local NVMe when it has one: a whole disk
# carrying no partitions and nothing mounted. On a machine without one this
# falls through and /mnt/bench stays on the OS disk.
mkdir -p /mnt/bench
LOCAL=""
for dev in $(lsblk -dn -o NAME,TYPE | awk '$2=="disk"{print $1}'); do
  [ "$(lsblk -n "/dev/$dev" | wc -l)" -eq 1 ] || continue
  [ -z "$(lsblk -no MOUNTPOINT "/dev/$dev" | tr -d ' 
')" ] || continue
  LOCAL="/dev/$dev"; break
done
if [ -n "$LOCAL" ]; then
  echo "local disk $LOCAL -> /mnt/bench"
  mkfs.ext4 -F -q "$LOCAL" && mount -o noatime "$LOCAL" /mnt/bench
  df -h /mnt/bench
else
  echo "no local disk found; /mnt/bench stays on the OS disk"
fi
chown -R azureuser:azureuser /opt/harness /mnt/bench
runuser -l azureuser -c 'bash /opt/harness/leg.sh' >>/var/log/vdb-leg.log 2>&1
echo "boot done $(date -u +%FT%TZ)"
"""


def cloud_init(engine, task_label):
    files = [
        ("/opt/harness/scripts/engines.sh", read("scripts", "engines.sh")),
        ("/opt/harness/scripts/run_peer_vdbbench.sh",
         read("scripts", "run_peer_vdbbench.sh")),
    ]
    # Compose files and the configs they mount, at the same relative paths.
    compose_root = os.path.join(HARNESS, "compose")
    for dirpath, _, names in os.walk(compose_root):
        for name in sorted(names):
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, compose_root).replace(os.sep, "/")
            with open(src, encoding="utf-8") as fh:
                files.append((f"/opt/harness/compose/{rel}", fh.read()))
    files += [
        ("/opt/harness/leg.env", leg_env(engine, task_label)),
        ("/opt/harness/leg.sh", LEG_SH),
        ("/opt/harness/boot.sh",
         BOOT_SH.replace("DEADMAN_MIN_PLACEHOLDER", str(DEADMAN_MIN))),
    ]
    out = ["#cloud-config", "write_files:"]
    for path, body in files:
        out += [f"  - path: {path}",
                "    permissions: '0755'",
                "    encoding: b64",
                f"    content: {b64(body)}"]
    out += ["runcmd:", "  - [ bash, /opt/harness/boot.sh ]", ""]
    return "\n".join(out)


# ----------------------------------------------------------------------- up
def vm_name(eid, stamp):
    return f"vdbb-{eid}-{stamp}"[:60]


def reopen(stamp, nsg=None, ip=None):
    """Point the run's SSH rule at the current public IP.

    A laptop that reconnects gets a different address, and every later status
    and collect would then time out against VMs that are working fine.
    """
    nsg = nsg or load_fleet(stamp)["nsg"]
    ip = ip or my_ip()
    az("network", "nsg", "rule", "create", "-g", RG, "--nsg-name", nsg,
       "-n", "allow-ssh", "--priority", "300", "--access", "Allow",
       "--protocol", "Tcp", "--direction", "Inbound",
       "--destination-port-ranges", "22",
       "--source-address-prefixes", f"{ip}/32", "-o", "none", parse=False)
    return ip


def up(engine_ids, into=None):
    os.makedirs(RUNS, exist_ok=True)
    prior = load_fleet(into) if into else None
    stamp = into or dt.datetime.now(dt.timezone.utc).strftime("%m%d%H%M")
    task_label = (prior["task_label"] if prior else
                  os.environ.get("TASK_LABEL",
                                 "open_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")))
    # A relaunch into an existing run must use that run's case. Without this a
    # relaunch with CASE unset would quietly measure the 1M corpus into a run
    # whose fleet.json and task label both say 10M.
    global CASE
    if prior and prior.get("case") and prior["case"] != CASE:
        log(f"run {stamp} is {prior['case']}, using that rather than {CASE}")
        CASE = prior["case"]

    out = os.path.join(RUNS, stamp)
    os.makedirs(out, exist_ok=True)

    ensure_key()
    ip = my_ip()
    log(f"rg={RG} region={LOCATION} size={MACHINE} case={CASE} stamp={stamp}")
    log(f"{len(engine_ids)} legs: {' '.join(engine_ids)}")
    log(f"ssh opened to {ip}/32 only")

    vnet = prior["vnet"] if prior else f"vdbb-{stamp}-vnet"
    nsg = prior["nsg"] if prior else f"vdbb-{stamp}-nsg"
    created = list(prior["legs"]) if prior else []

    if not prior:
        az("network", "nsg", "create", "-g", RG, "-n", nsg, "-l", LOCATION,
           "--tags", f"run={stamp}", "auto-delete=true", "-o", "none", parse=False)
        az("network", "vnet", "create", "-g", RG, "-n", vnet, "-l", LOCATION,
           "--address-prefixes", "10.42.0.0/16",
           "--subnet-name", "default", "--subnet-prefixes", "10.42.0.0/20",
           "--tags", f"run={stamp}", "auto-delete=true", "-o", "none", parse=False)
    reopen(stamp, nsg=nsg, ip=ip)
    log(f"network ready: {vnet} / {nsg}")

    tmp = os.path.join(out, "cloud-init")
    os.makedirs(tmp, exist_ok=True)

    def launch(eid):
        e = BY_ID[eid]
        name = vm_name(eid, stamp)
        ci = os.path.join(tmp, f"{eid}.yml")
        with open(ci, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(cloud_init(e, task_label))
        az("vm", "create", "-g", RG, "-n", name, "-l", LOCATION,
           "--image", "Ubuntu2404", "--size", MACHINE,
           "--admin-username", SSH_USER, "--ssh-key-values", KEY + ".pub",
           "--public-ip-sku", "Standard",
           "--os-disk-size-gb", str(DEFAULTS["disk_size_gb"]),
           "--storage-sku", "Premium_LRS",
           "--vnet-name", vnet, "--subnet", "default", "--nsg", nsg,
           "--os-disk-delete-option", "Delete",
           "--nic-delete-option", "Delete",
           "--custom-data", ci,
           "--tags", f"run={stamp}", f"engine={eid}", "auto-delete=true",
           "-o", "none", parse=False)
        # Billing backstop. A guest shutdown leaves the VM allocated and still
        # billed; this schedule deallocates it. It survives this process
        # exiting, which is the case that matters.
        off = (dt.datetime.now(dt.timezone.utc)
               + dt.timedelta(minutes=int(DEADMAN_MIN))).strftime("%H%M")
        az("vm", "auto-shutdown", "-g", RG, "-n", name, "--time", off,
           "-o", "none", parse=False, check=False)
        d = az("vm", "show", "-d", "-g", RG, "-n", name,
               "--query", "{ip:publicIps,id:id}")
        log(f"  up   {eid:<22} {name}  {d['ip']}")
        return {"engine": eid, "vm": name, "ip": d["ip"], "id": d["id"]}

    # created carries the legs this run already had, so the ones this call
    # actually launched are tracked separately. Billing the whole list would
    # add a row per leg on every relaunch.
    fresh = []
    with cf.ThreadPoolExecutor(max_workers=min(11, len(engine_ids))) as ex:
        futs = {ex.submit(launch, e): e for e in engine_ids}
        for f in cf.as_completed(futs):
            eid = futs[f]
            try:
                leg = f.result()
                fresh.append(leg)
                created.append(leg)
            except Exception as exc:
                log(f"  FAIL {eid}: {exc}")

    by_engine = {leg["engine"]: leg for leg in created}
    fleet = {
        "stamp": stamp,
        "started_at": (prior["started_at"] if prior else
                       dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        "rg": RG, "location": LOCATION, "machine": MACHINE,
        "task_label": task_label, "case": CASE, "k": DEFAULTS["k"],
        "vdbbench": f"{VDB_REPO}@{VDB_REF}",
        "vnet": vnet, "nsg": nsg,
        "legs": sorted(by_engine.values(), key=lambda x: x["engine"]),
    }
    with open(os.path.join(out, "fleet.json"), "w", encoding="utf-8") as fh:
        json.dump(fleet, fh, indent=2)
    ledger_add(stamp, fresh)
    log(f"{len(fleet['legs'])} legs in run {stamp} -> runs/{stamp}/fleet.json")
    log(f"watch:   python scripts/azure_fleet.py status {stamp}")
    log(f"collect: python scripts/azure_fleet.py collect {stamp}")
    log(f"delete:  python scripts/azure_fleet.py down {stamp}")
    return stamp


# --------------------------------------------------------------------- ledger
# runs/<stamp>/vm_ledger.csv records when each VM was created and deleted,
# which is what it was billed for.
LEDGER_COLS = ["vm", "engine", "size", "region", "created_at", "deleted_at"]


def ledger_add(stamp, legs):
    path = os.path.join(RUNS, stamp, "vm_ledger.csv")
    fresh = not os.path.exists(path)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        if fresh:
            w.writeheader()
        for leg in legs:
            w.writerow({"vm": leg["vm"], "engine": leg["engine"], "size": MACHINE,
                        "region": LOCATION, "created_at": now, "deleted_at": ""})


def ledger_close(stamp, vms):
    """Stamp a deletion time on the open rows for these VM names."""
    path = os.path.join(RUNS, stamp, "vm_ledger.csv")
    if not os.path.exists(path) or not vms:
        return
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # A relaunch reuses the VM name, so the file holds several rows for it and
    # only the newest open one is the VM being deleted. Closing every match
    # would stamp the row the relaunch is about to open.
    for vm in vms:
        open_rows = [r for r in rows if r["vm"] == vm and not r.get("deleted_at")]
        if open_rows:
            max(open_rows, key=lambda r: r.get("created_at") or "")["deleted_at"] = now
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        w.writeheader()
        w.writerows(rows)


def ledger_repair(stamp):
    """Fill a run's ledger from Azure for the VMs that still exist.

    Runs started before the ledger existed have none, and a VM that was already
    deleted cannot report when it was created, so this recovers what is still
    there and says how much it could not.
    """
    fleet = load_fleet(stamp)
    path = os.path.join(RUNS, stamp, "vm_ledger.csv")
    have = set()
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        have = {r["vm"] for r in rows}
    added, missing = 0, []
    for leg in fleet["legs"]:
        if leg["vm"] in have:
            continue
        d = az("vm", "show", "-d", "-g", RG, "-n", leg["vm"],
               "--query", "{created:timeCreated}", check=False)
        created = (d or {}).get("created")
        if not created:
            missing.append(leg["vm"])
            continue
        rows.append({"vm": leg["vm"], "engine": leg["engine"], "size": MACHINE,
                     "region": LOCATION,
                     "created_at": created.split(".")[0] + "Z", "deleted_at": ""})
        added += 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        w.writeheader()
        w.writerows(rows)
    log(f"ledger {stamp}: {len(rows)} rows, {added} recovered from Azure")
    if missing:
        log(f"  gone, so unbilled here: {missing}")


# ------------------------------------------------------------------- status
def latest_stamp():
    if not os.path.isdir(RUNS):
        sys.exit("no runs/ yet")
    cands = [d for d in os.listdir(RUNS)
             if os.path.exists(os.path.join(RUNS, d, "fleet.json"))]
    if not cands:
        sys.exit("no fleet.json in runs/")
    return sorted(cands)[-1]


def load_fleet(stamp):
    with open(os.path.join(RUNS, stamp, "fleet.json"), encoding="utf-8") as fh:
        return json.load(fh)


def ssh_out(ip, cmd, timeout=45):
    p = subprocess.run(["ssh", *SSH_OPTS, "-i", KEY, f"{SSH_USER}@{ip}", cmd],
                       capture_output=True, text=True, timeout=timeout)
    # capture_output should make both strings, and one came back None once,
    # which took down a whole collect and nearly lost a leg's results with it.
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


PROBE = (
    "if [ -f /opt/harness/DONE ]; then echo state=done rc=$(cat /opt/harness/RC 2>/dev/null); "
    "elif [ -f /opt/harness/STARTED ]; then echo state=running; "
    "else echo state=booting; fi; "
    "echo results=$(ls /tmp/vdb_results/result_*.json 2>/dev/null | wc -l); "
    "echo tail=\"$(tail -n 1 /var/log/vdb-leg.log 2>/dev/null | cut -c1-90)\""
)


def status(stamp):
    fleet = load_fleet(stamp)
    log(f"run {stamp}  started {fleet['started_at']}  {fleet['machine']}")
    rows = []

    def probe(leg):
        if leg.get("collected"):
            return leg["engine"], "collected", "", "results already copied back"
        try:
            rc, out, err = ssh_out(leg["ip"], PROBE)
        except subprocess.TimeoutExpired:
            return leg["engine"], "unreachable", "", "ssh timeout"
        if rc != 0:
            return leg["engine"], "unreachable", "", (err or "ssh failed")[:80]
        d = dict(kv.split("=", 1) for kv in out.splitlines() if "=" in kv)
        return leg["engine"], d.get("state", "?"), d.get("results", "0"), d.get("tail", "")

    with cf.ThreadPoolExecutor(max_workers=11) as ex:
        rows = list(ex.map(probe, fleet["legs"]))

    print(f"\n{'engine':<24}{'state':<14}{'results':<9}last line")
    print("-" * 100)
    for eng, st, n, tail in sorted(rows):
        print(f"{eng:<24}{st:<14}{n:<9}{tail}")
    done = sum(1 for _, st, _, _ in rows
               if st.startswith("done") or st == "collected")
    print(f"\n{done}/{len(rows)} legs finished")
    return done, len(rows)


# ------------------------------------------------------------------ collect
def verify(stamp, only=None):
    """Parse each leg's real arguments with the real CLI, on the VM.

    check_matrix.py reads the client sources and cannot tell which shared
    options a command actually composes in, so it passes arguments the CLI then
    rejects. --dry-run builds the whole task config and stops before the
    benchmark, which is the same check the run itself would make.
    """
    fleet = load_fleet(stamp)
    legs = [l for l in fleet["legs"] if not only or l["engine"] in only]

    def check(leg):
        e = BY_ID[leg["engine"]]
        args = " ".join(e["args"])
        ef = f'{e["ef_flag"]} {e["ef_sweep"][0]}' if e.get("ef_flag") and e.get("ef_sweep") else ""
        cmd = (f'source ~/venv/bin/activate 2>/dev/null && cd ~/vdbbench && '
               f'vectordbbench {e["cmd"]} --case-type {CASE} --k {DEFAULTS["k"]} '
               f'--db-label verify --task-label verify --num-concurrency 1 '
               f'--concurrency-duration 5 --search-serial --search-concurrent '
               f'--drop-old --load {args} {ef} --dry-run 2>&1 | tail -4')
        try:
            rc, out, err = ssh_out(leg["ip"], cmd, timeout=240)
        except subprocess.TimeoutExpired:
            return leg["engine"], "timeout", ""
        text = out or err
        if "Error" in text or "Usage:" in text or "Traceback" in text:
            bad = [l for l in text.splitlines()
                   if "Error" in l or "error" in l][-1:] or text.splitlines()[-1:]
            return leg["engine"], "REJECTED", bad[0][:150] if bad else ""
        if "TaskConfig(" in text:
            return leg["engine"], "ok", ""
        return leg["engine"], "unknown", text.splitlines()[-1][:150] if text else "no output"

    with cf.ThreadPoolExecutor(max_workers=11) as ex:
        rows = sorted(ex.map(check, legs))
    bad = 0
    for eng, state, detail in rows:
        print(f"{eng:<24}{state:<11}{detail}")
        bad += state != "ok"
    print(f"\n{len(rows)} legs, {bad} not accepted")
    return bad


def collect(stamp, only=None):
    fleet = load_fleet(stamp)
    out = os.path.join(RUNS, stamp)
    legs = [l for l in fleet["legs"]
            if (not only or l["engine"] in only) and not l.get("collected")]

    def grab(leg):
        d = os.path.join(out, leg["engine"])
        os.makedirs(d, exist_ok=True)
        got = 0
        for src in ("/opt/harness/results/.", "/tmp/vdb_results/."):
            p = subprocess.run(["scp", *SSH_OPTS, "-i", KEY, "-r",
                                f"{SSH_USER}@{leg['ip']}:{src}", d],
                               capture_output=True, text=True)
            if p.returncode == 0:
                break
        # A leg that errors per query writes millions of identical lines, so the
        # log is summarised on the VM rather than copied whole.
        for log_src, log_dst in (("/var/log/vdb-leg.log", "leg.log"),
                                 ("/var/log/vdb-boot.log", "boot.log")):
            try:
                rc, text, _ = ssh_out(
                    leg["ip"],
                    f"if [ -f {log_src} ]; then "
                    f"echo \"# {log_src}: $(wc -l < {log_src}) lines, \""
                    f"\"$(du -h {log_src} | cut -f1)\"; "
                    f"echo '# --- first 200 ---'; head -n 200 {log_src}; "
                    # head and tail alone lose the middle of a five-point leg,
                    # which is where the points that failed are.
                    f"echo '# --- boundaries and errors ---'; "
                    # -a because a long run writes null bytes and grep then
                    # refuses the file, -m so it stops at 400 matches instead
                    # of reading gigabytes, and the tail bounds the no-match
                    # case. Without these the pipeline outran its timeout on a
                    # 10M leg and the except left that leg with no log at all.
                    f"tail -c 300000000 {log_src} | grep -a -m 400 -nE "
                    f"'^--- vectordbbench|::error|reason=|Traceback|"
                    f"RuntimeError|MemoryError|Killed|No space left|"
                    f"cannot schedule'; "
                    f"echo '# --- last 2000 ---'; tail -n 2000 {log_src}; fi",
                    timeout=600)
                if rc == 0 and text:
                    with open(os.path.join(d, log_dst), "w", encoding="utf-8",
                              errors="replace") as fh:
                        fh.write(text)
            except subprocess.TimeoutExpired:
                pass
        # Legs launched before the runner learned to filter copy the clone's own
        # bundled result files alongside theirs. They carry this run's stamped
        # metadata, so the task label is what tells them apart.
        for name in list(os.listdir(d)):
            if not name.startswith("result_"):
                continue
            path = os.path.join(d, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (ValueError, OSError):
                continue
            label = (doc.get("run_meta") or {}).get("task_label")
            if label and not str(doc.get("task_label") or "").startswith(label):
                os.remove(path)
        got = len([f for f in os.listdir(d) if f.startswith("result_")])
        return leg["engine"], got

    with cf.ThreadPoolExecutor(max_workers=11) as ex:
        for eng, n in sorted(ex.map(grab, legs)):
            log(f"  {eng:<24}{n} result files")
    log(f"collected into runs/{stamp}/")


def retire(stamp, engines):
    """Collect a finished leg, delete its VM, and free its quota.

    Regional cores cap how many legs run at once, so a leg that has produced its
    results should hand its slot to the next engine rather than idle.
    """
    fleet = load_fleet(stamp)
    collect(stamp, only=set(engines))
    for leg in fleet["legs"]:
        if leg["engine"] not in engines or leg.get("collected"):
            continue
        az("resource", "delete", "--ids", leg["id"], "-o", "none",
           parse=False, check=False)
        for pip in (leg["vm"] + "PublicIP", leg["vm"] + "-pip"):
            az("network", "public-ip", "delete", "-g", RG, "-n", pip,
               "-o", "none", parse=False, check=False)
        leg["collected"] = True
        ledger_close(stamp, {leg["vm"]})
        log(f"  retired {leg['engine']} ({leg['vm']})")
    with open(os.path.join(RUNS, stamp, "fleet.json"), "w", encoding="utf-8") as fh:
        json.dump(fleet, fh, indent=2)


def relaunch(stamp, engines):
    """Keep whatever a failed leg produced, delete its VM, start it again.

    A leg that failed on a config or a client bug has to be re-run from a clean
    VM, and its logs are the evidence for why, so they come back first.
    """
    fleet = load_fleet(stamp)
    legs = [l for l in fleet["legs"] if l["engine"] in engines]
    missing = set(engines) - {l["engine"] for l in legs}
    if missing:
        log(f"not in this run, will be launched fresh: {sorted(missing)}")
    if legs:
        log(f"saving logs from {len(legs)} legs before replacing them")
        collect(stamp, only={l["engine"] for l in legs})
        for leg in legs:
            # The replacement run writes different filenames when the sweep
            # labels change, so last run's results would sit beside the new ones
            # and be collected as if they belonged to it. The logs stay.
            d = os.path.join(RUNS, stamp, leg["engine"])
            for name in (os.listdir(d) if os.path.isdir(d) else []):
                if name.startswith(("result_", "run_meta_")):
                    os.remove(os.path.join(d, name))
            az("resource", "delete", "--ids", leg["id"], "-o", "none",
               parse=False, check=False)
            for pip in (leg["vm"] + "PublicIP", leg["vm"] + "-pip"):
                az("network", "public-ip", "delete", "-g", RG, "-n", pip,
                   "-o", "none", parse=False, check=False)
            ledger_close(stamp, {leg["vm"]})
            log(f"  deleted {leg['vm']}")
    up(list(engines), into=stamp)


# --------------------------------------------------------------------- down
def sweep():
    """Delete every benchmark resource in the group, whatever run made it.

    A session that ends without tearing down leaves VMs allocated, and an
    allocated VM bills whether or not its OS is running. This is the one
    command that ends that, and it is safe to run at the start of any session
    because it only touches the vdbb- prefix.
    """
    res = az("resource", "list", "-g", RG) or []
    mine = [r for r in res if r.get("name", "").startswith("vdbb-")]
    if not mine:
        log("nothing to sweep")
        return
    log(f"sweeping {len(mine)} benchmark resources in {RG}")
    order = {"Microsoft.Compute/virtualMachines": 0}
    for r in sorted(mine, key=lambda r: order.get(r["type"], 1)):
        az("resource", "delete", "--ids", r["id"], "-o", "none",
           parse=False, check=False)
        log(f"  deleted {r['name']}")
    # A sweep is the leak path, which is exactly when the bill matters, so every
    # run's ledger gets the deletion time for the VMs this took away.
    gone = {r["name"] for r in mine}
    for stamp in sorted(os.listdir(RUNS) if os.path.isdir(RUNS) else []):
        ledger_close(stamp, gone)
    left = [r["name"] for r in (az("resource", "list", "-g", RG) or [])
            if r.get("name", "").startswith("vdbb-")]
    log(f"left: {left or 'none'}")


def down(stamp):
    """Delete exactly what this run created, by recorded resource id."""
    fleet = load_fleet(stamp)
    ids = [leg["id"] for leg in fleet["legs"] if not leg.get("collected")]
    if ids:
        log(f"deleting {len(ids)} VMs")
        az("resource", "delete", "--ids", *ids, "-o", "none",
           parse=False, check=False)
    ledger_close(stamp, {leg["vm"] for leg in fleet["legs"]})
    # The NIC and OS disk go with the VM (delete-option Delete). What can
    # outlive them is a public IP or a NIC from a VM whose create failed after
    # its network existed, and both carry the run in their name.
    #
    # Filtering happens here rather than in a --query expression: az is a batch
    # file on Windows, and cmd mangles the ?, ( and && a JMESPath filter needs.
    tagged = az("resource", "list", "-g", RG, "--tag", f"run={stamp}",
                "--query", "[].id", check=False) or []
    named = [r["id"] for r in (az("resource", "list", "-g", RG, check=False) or [])
             if r.get("name", "").startswith("vdbb-") and stamp in r.get("name", "")]
    rest = list(dict.fromkeys(tagged + named))
    if rest:
        log(f"deleting {len(rest)} remaining resources")
        for r in rest:  # ordered: dependants first
            az("resource", "delete", "--ids", r, "-o", "none",
               parse=False, check=False)
    for kind, name in (("vnet", fleet["vnet"]), ("nsg", fleet["nsg"])):
        az("network", kind, "delete", "-g", RG, "-n", name, "-o", "none",
           parse=False, check=False)
    log(f"run {stamp} torn down")


# --------------------------------------------------------------------- main
def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    if not RG:
        sys.exit("set AZURE_RG to the resource group the VMs go in")
    if cmd == "up":
        into = None
        if "--into" in rest:
            i = rest.index("--into")
            into = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        ids = rest or [e["id"] for e in MATRIX["engines"]]
        unknown = [i for i in ids if i not in BY_ID]
        if unknown:
            sys.exit(f"unknown engine ids: {unknown}")
        up(ids, into=into)
    elif cmd == "ledger":
        ledger_repair(rest[0] if rest else latest_stamp())
    elif cmd == "sweep":
        sweep()
    elif cmd == "reopen":
        log("ssh now open to", reopen(rest[0] if rest else latest_stamp()))
    elif cmd == "status":
        status(rest[0] if rest else latest_stamp())
    elif cmd == "collect":
        collect(rest[0] if rest else latest_stamp())
    elif cmd == "verify":
        sys.exit(1 if verify(rest[0] if rest else latest_stamp(),
                             only=set(rest[1:]) or None) else 0)
    elif cmd == "retire":
        if len(rest) < 2:
            sys.exit("retire needs a stamp and at least one engine")
        retire(rest[0], set(rest[1:]))
    elif cmd == "relaunch":
        if len(rest) < 2:
            sys.exit("relaunch needs a stamp and at least one engine")
        relaunch(rest[0], rest[1:])
    elif cmd == "down":
        down(rest[0] if rest else latest_stamp())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
