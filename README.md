# Open Vector Database Benchmark

An open vector database leaderboard, published at
<https://infino.ai/open-vdbbench>. Every engine runs the same
[VectorDBBench](https://github.com/infino-ai/VectorDBBench) cases on the same
Azure VM size, and every row on the board links to the result file it came
from.

## How results are published

Results are updated whenever an engine submits a change. Every functioning
submission is run and published, whatever it scores. A submission is
functioning when the engine loads the dataset, answers the searches and writes a
result file.

Pull requests without issues are merged within one month of being opened.

Numbers come only from our run on the reference machine.

## Test setup

| setting | value |
|---|---|
| machine | Azure `Standard_D16ads_v7`, 16 vCPU, 64 GB, local NVMe, one VM per engine |
| client | VectorDBBench, on the same VM as the engine |
| datasets | Cohere 1M and Cohere 10M, 768 dimensions, cosine |
| cases | `Performance768D1M`, `Performance768D10M` |
| query | top 100 |
| concurrency | 1, 5, 10, 20, 30, 40, 60, 80, 30 seconds each |
| sweep | one full run per value of the engine's search parameter |
| rank | highest QPS at recall ≥ 0.90 across the sweep |

`METHODOLOGY.md` has the full rules and every engine's configuration.

## Submit a result

A submission is a pull request that tells the harness how to run an engine. A
new engine, a version bump and a configuration change all take the same steps.

1. Check that the engine has a working client in
   [infino-ai/VectorDBBench](https://github.com/infino-ai/VectorDBBench) on
   the `open-leaderboard` branch. If it has none, or the client needs a fix,
   open a pull request there first.
2. Add or edit the engine's entry in `harness/matrix.json`. For a version bump
   this is the image tag and nothing else.
3. For a new engine, add `up_<id>`, `version_<id>` and `down_<id>` to
   `harness/scripts/engines.sh`. They start the engine and block until it
   answers, print the version it reports at runtime, and stop it. An engine
   that needs several containers adds a compose file under `harness/compose/`.
4. Run `python3 scripts/check_matrix.py path/to/VectorDBBench` against your
   client checkout. It checks every flag in the entry against the client's CLI.
5. Open a pull request here saying what changed and which configuration you
   want measured.

We run the engine at 1M and 10M on the reference machine, commit the result
files under `results/<engine>/`, and rebuild the board.

### Matrix fields

| field | holds |
|---|---|
| `id` | lowercase, hyphenated; `engines.sh` uses it with underscores |
| `display` | the name on the board |
| `cmd` | the `vectordbbench` subcommand |
| `image` | a container image pinned to an exact tag, `compose`, or `null` for an engine that runs in-process |
| `port` | the port the client connects to |
| `pip_extra`, `pip_pins` | the VectorDBBench extra to install, and exact package pins |
| `args` | flags passed to the subcommand |
| `ef_flag`, `ef_sweep` | the search parameter and the values to sweep |
| `index_config` | the index and compression, as shown on the board |
| `quantized` | `true` when vectors are stored compressed |
| `per_case` | overrides for one case, such as a different sweep at 10M |
| `vdb_repo`, `vdb_ref` | a fork or branch of VectorDBBench, when the client is not on `open-leaderboard` |

### Requirements

- The engine runs on the reference machine from a container image or a pip
  package.
- The image is pinned to an exact version tag.
- The sweep changes recall. A sweep that returns the same recall at every value
  never reached the index, and its rows are not ranked.

## Run it yourself

Rebuild the board from the committed data:

```bash
python3 scripts/build_leaderboard_data.py data/
python3 scripts/build_page.py .
```

`site/open-vdbbench.html` is the board infino.ai serves at `/open-vdbbench/`:
copy it over `src/data/open-vdbbench.html` in the website repo. `site/index.html`
is the same board as a standalone page.

Measure engines on Azure, one self-driving VM per engine:

```bash
python3 scripts/azure_fleet.py up pgvector qdrant
python3 scripts/azure_fleet.py status
python3 scripts/azure_fleet.py collect
python3 scripts/azure_fleet.py down
python3 scripts/collect_runs.py runs data
```

`AZURE_RG` names the resource group the VMs go in, and
`CASE=Performance768D10M` runs the 10M case. Credentials stay in `.secrets/`,
which is gitignored.

## Layout

```
harness/matrix.json           every engine: image, version, index, flags, sweep
harness/scripts/engines.sh    per-engine bring-up, version probe, teardown
harness/scripts/run_peer_vdbbench.sh  the runner each VM executes
harness/compose/              engines that need several containers
scripts/azure_fleet.py        provision, watch, collect and delete the VMs
scripts/check_matrix.py       matrix flags checked against the client sources
scripts/collect_runs.py       runs/ -> data/measured_results.json
scripts/build_leaderboard_data.py  data/ -> data/leaderboard_data.json
scripts/build_page.py         leaderboard_data.json -> site/
scripts/schema.py             the result row schema both builders share
results/<engine>/             the result file behind every row
data/                         normalized results the board is built from
METHODOLOGY.md                the rules and every engine's configuration
AUDIT.md                      client and harness defects found producing these results
```
