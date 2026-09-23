#!/usr/bin/env bash
# Runs one peer engine through VectorDBBench on a freshly provisioned VM.
#
# scripts/azure_fleet.py writes it into the VM's cloud-init with the leg's
# settings as environment variables, and the VM runs it at boot. Everything it
# needs beyond the base Ubuntu image it installs itself, so the VM image is not
# a hidden variable between engines.
#
# Required env:
#   ENGINE_ID        matrix id, e.g. pgvector
#   ENGINE_FN        matrix id with hyphens turned into underscores
#   ENGINE_IMAGE     container image, empty for embedded engines
#   VDB_CMD          vectordbbench subcommand, e.g. pgvectorhnsw
#   VDB_ARGS         engine-specific flags, space separated
#   EF_FLAG          search-time parameter flag, empty for a single run
#   EF_SWEEP         space separated values for EF_FLAG
#   CASE             VDBBench case, e.g. Performance768D1M
#   K                top-k
#   TASK_LABEL       groups the sweep into one recall/QPS curve
#   DB_LABEL         series label, defaults to "<vm_size>-<index_config>"
#   NUM_CONC         concurrency levels
#   VM_SIZE          recorded into the result so the hardware is never implicit
#   PIP_EXTRA        the pyproject extra to install, e.g. chromadb
#   APT_EXTRA        extra distro packages this client's wheel needs
#   PIP_PINS         versions this client needs that its extra does not bound
#   VDB_REPO/VDB_REF the VectorDBBench fork and ref to install

set -euo pipefail

: "${ENGINE_ID:?}" "${ENGINE_FN:?}" "${VDB_CMD:?}" "${CASE:?}" "${TASK_LABEL:?}"
ENGINE_IMAGE=${ENGINE_IMAGE:-}
VDB_ARGS=${VDB_ARGS:-}
EF_FLAG=${EF_FLAG:-}
EF_SWEEP=${EF_SWEEP:-}
RELOAD_EACH_POINT=${RELOAD_EACH_POINT:-no}
EF_VALUE_PREFIX=${EF_VALUE_PREFIX:-}
K=${K:-100}
NUM_CONC=${NUM_CONC:-1,5,10,20,30,40,60,80}
CONC_DURATION=${CONC_DURATION:-30}
VM_SIZE=${VM_SIZE:-unknown}
CLOUD=${CLOUD:-gcp}
DB_LABEL=${DB_LABEL:-}
PIP_EXTRA=${PIP_EXTRA:-}
PIP_PINS=${PIP_PINS:-}
APT_EXTRA=${APT_EXTRA:-}
VDB_REPO=${VDB_REPO:-infino-ai/VectorDBBench}
VDB_REF=${VDB_REF:-open-leaderboard}
export BENCH_DIR=${BENCH_DIR:-/mnt/bench}
# The 10M corpus is about 30 GB of parquet. VectorDBBench caches it under /tmp
# by default, which is the 128 GB OS disk that also holds the docker images, so
# it goes on the local NVMe with everything else this leg writes.
export DATASET_LOCAL_DIR=${DATASET_LOCAL_DIR:-$BENCH_DIR/dataset}
export HARNESS_DIR=${HARNESS_DIR:-$HOME/harness}
RESULTS=/tmp/vdb_results
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "=== $ENGINE_ID on $VM_SIZE ($CLOUD), case=$CASE k=$K task_label=$TASK_LABEL"

# ---------------------------------------------------------------- host setup
sudo mkdir -p "$BENCH_DIR" "$RESULTS"
sudo chown -R "$USER" "$BENCH_DIR" "$RESULTS"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends \
  ca-certificates curl gnupg git jq python3-venv python3-pip python3-dev build-essential \
  postgresql-client mariadb-client ${APT_EXTRA} >/dev/null

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh >/dev/null
fi
sudo systemctl start docker

# vm.max_map_count for OpenSearch / any Lucene-backed engine; harmless elsewhere.
sudo sysctl -w vm.max_map_count=262144 >/dev/null
sudo sysctl -w vm.swappiness=1 >/dev/null

# ------------------------------------------------------- VectorDBBench client
git clone --depth 1 --branch "$VDB_REF" "https://github.com/${VDB_REPO}.git" "$HOME/vdbbench"
cd "$HOME/vdbbench"
python3 -m venv "$HOME/venv"
# shellcheck disable=SC1091
source "$HOME/venv/bin/activate"
pip install -q --upgrade pip
if [ -n "$PIP_EXTRA" ]; then
  pip install -q -e ".[${PIP_EXTRA}]"
else
  pip install -q -e .
fi
# An extra with no upper bound can resolve to a release the client predates.
if [ -n "$PIP_PINS" ]; then
  echo "--- pinning $PIP_PINS"
  pip install -q "$PIP_PINS"
fi
VDB_COMMIT=$(git rev-parse HEAD)

# ------------------------------------------------------------- engine bring-up
# shellcheck disable=SC1091
source "$HARNESS_DIR/scripts/engines.sh"
# An engine that never becomes healthy is the most common failure, and the
# teardown below removes the container that holds the reason. Save the
# container state and logs beside the results first.
save_diagnostics() {
  set +e
  { echo "=== containers ==="; sudo docker ps -a;
    for c in $(sudo docker ps -aq); do
      echo "=== logs $(sudo docker inspect --format "{{.Name}} {{.Config.Image}}" "$c") ===";
      echo "--- first 60 lines (where a panic says why) ---";
      sudo docker logs "$c" 2>&1 | head -60;
      echo "--- last 60 lines ---";
      sudo docker logs --tail 60 "$c" 2>&1;
    done; } > "$RESULTS/engine_diagnostics_${ENGINE_ID}.txt" 2>&1
  "down_${ENGINE_FN}" >/dev/null 2>&1
}
trap save_diagnostics EXIT

"up_${ENGINE_FN}"
ENGINE_VERSION="$("version_${ENGINE_FN}" 2>/dev/null | head -1 | tr -d '\n' || true)"
: "${ENGINE_VERSION:=unknown}"
echo "=== $ENGINE_ID reports version: $ENGINE_VERSION"

if [ -z "$DB_LABEL" ]; then
  DB_LABEL="${VM_SIZE}"
fi

# --------------------------------------------------------------- the sweep
# One vectordbbench invocation per search-time parameter value. They share a
# task_label, so the viewer joins them into a single recall/QPS curve. Only the
# first leg loads and builds; the rest reuse the built index.
#
# A client that applies its search parameter in optimize() is the exception,
# because VectorDBBench calls optimize() only on an invocation that loads. Such
# a sweep reuses whatever the first point set and every point returns the same
# recall, so those engines reload on every point and pay the load five times.
run_one() {  # $1 = ef value or empty, $2 = drop-old flag
  local efval=$1 drop=$2
  # Each invocation writes result_<date>_<task_label>_<db>.json. A sweep that
  # reused one label would overwrite every earlier point and leave only the
  # last, so each point carries the search parameter in its label.
  local label="$TASK_LABEL"
  [ -n "$efval" ] && label="${TASK_LABEL}_ef${efval}"
  local -a cmd=(vectordbbench "$VDB_CMD"
    --case-type "$CASE" --k "$K"
    --db-label "$DB_LABEL" --task-label "$label"
    --num-concurrency "$NUM_CONC" --concurrency-duration "$CONC_DURATION"
    --search-serial --search-concurrent)
  if [ "$drop" = "yes" ]; then cmd+=(--drop-old --load); else cmd+=(--skip-drop-old --skip-load); fi
  # shellcheck disable=SC2206
  cmd+=($VDB_ARGS)
  # Doris takes its search parameter as one key=value argument, so the flag
  # carries a prefix and the label keeps the bare number.
  if [ -n "$efval" ] && [ -n "$EF_FLAG" ]; then cmd+=("$EF_FLAG" "${EF_VALUE_PREFIX}${efval}"); fi
  echo "--- ${cmd[*]}"
  "${cmd[@]}"
}

if [ -z "$EF_SWEEP" ]; then
  run_one "" yes
else
  first=yes
  for ef in $EF_SWEEP; do
    if [ "$first" = yes ] || [ "$RELOAD_EACH_POINT" = yes ]; then
      run_one "$ef" yes; first=no
    else
      run_one "$ef" no
    fi
  done
fi

# ------------------------------------------------------- collect and annotate
# VectorDBBench writes under the installed package's results dir. Copy every
# result this run produced, then stamp the version the engine actually reported,
# the machine it ran on, the dataset case, and the client commit. A result that
# cannot say when and on what it was measured does not go on the board.
# VectorDBBench writes into the installed package's results directory. This is
# an editable install, so that directory sits inside the clone, and the clone
# ships result files of its own. Copying the tree would hand
# other people's numbers this machine's metadata, so only files carrying this
# run's task label are taken.
RESULTS="$RESULTS" TASK_LABEL="$TASK_LABEL" python3 - <<'PY'
import glob, json, os, shutil
results = os.environ["RESULTS"]
want = os.environ["TASK_LABEL"]
os.makedirs(results, exist_ok=True)
taken = skipped = 0
for path in glob.glob(os.path.expanduser("~/vdbbench/**/result_*.json"), recursive=True):
    try:
        doc = json.load(open(path))
    except Exception:
        continue
    if not str(doc.get("task_label") or "").startswith(want):
        skipped += 1
        continue
    shutil.copy(path, os.path.join(results, os.path.basename(path)))
    taken += 1
print(f"collected {taken} result files for task_label={want}*, left {skipped} belonging to other runs")
PY

python3 - <<PY
import glob, json, os, platform, subprocess

meta = {
    "measured_at": "${STARTED_AT}",
    "measured_until": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip(),
    "engine_id": "${ENGINE_ID}",
    "engine_version_reported": """${ENGINE_VERSION}""".strip(),
    "engine_image": "${ENGINE_IMAGE}",
    "cloud": "${CLOUD}",
    "vm_size": "${VM_SIZE}",
    "case": "${CASE}",
    "k": int("${K}"),
    "task_label": "${TASK_LABEL}",
    "db_label": """${DB_LABEL}""".strip(),
    "num_concurrency": "${NUM_CONC}",
    "ef_flag": "${EF_FLAG}",
    "ef_sweep": "${EF_SWEEP}".split(),
    "vdbbench_repo": "${VDB_REPO}",
    "vdbbench_ref": "${VDB_REF}",
    "vdbbench_commit": "${VDB_COMMIT}",
    "cpu_model": next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), platform.processor()),
    "cpu_count": os.cpu_count(),
    "mem_total_kb": int(open("/proc/meminfo").readline().split()[1]),
    "kernel": platform.release(),
}
os.makedirs("${RESULTS}", exist_ok=True)
with open("${RESULTS}/run_meta_${ENGINE_ID}.json", "w") as fh:
    json.dump(meta, fh, indent=2, sort_keys=True)

# Write the reported version into every result file so the number and the
# version can never be separated downstream.
patched = 0
for path in glob.glob("${RESULTS}/result_*.json"):
    doc = json.load(open(path))
    for r in doc.get("results", []):
        dc = r.setdefault("task_config", {}).setdefault("db_config", {})
        if not dc.get("version"):
            dc["version"] = meta["engine_version_reported"]
        dc["note"] = (dc.get("note") or "") or f'{meta["vm_size"]} {meta["cloud"]}'
    doc["run_meta"] = meta
    json.dump(doc, open(path, "w"), indent=2, sort_keys=True)
    patched += 1
print(f"annotated {patched} result files")
print(json.dumps(meta, indent=2, sort_keys=True))
PY

ls -la "$RESULTS"

# A case that cannot reach its engine is a warning inside VectorDBBench, not
# an error: the run writes a result file full of zeros and exits clean. The
# leg then reports done rc=0 and the board quietly loses an engine.
python3 - <<PY
import glob, json, sys
live = 0
for path in glob.glob("${RESULTS}/result_*.json"):
    for r in json.load(open(path)).get("results", []):
        m = r.get("metrics", {})
        if (m.get("qps") or 0) > 0 and (m.get("recall") or 0) > 0:
            live += 1
print(f"{live} points measured something")
sys.exit(0 if live else 1)
PY
if [ $? -ne 0 ]; then
  echo "::error::every point measured zero, so this leg has no result"
  exit 1
fi

