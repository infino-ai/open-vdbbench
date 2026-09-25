# Methodology

## The seven rules

1. No result without a date. Every row states when it was measured. A run
   whose measurement date cannot be established is not published.
2. No result without a version. The runner reads the version out of the
   running engine after the benchmark and writes it into the result file, so the
   number and the version cannot be separated later.
3. Compression on wherever the client supports it. If an engine's client
   supports a quantized index, the published run uses it, and every row shows
   the index and compression it ran with.
4. One machine per comparison. Every engine runs on `Standard_D16ads_v7`
   (16 vCPU, 64 GB, local NVMe), and a row measured on anything else is not
   published.
5. Every functioning submission is measured and published, whatever it scores.
6. Every row links to its source file.
7. A sweep that changed nothing is not a ranking. An engine that returns the
   same recall at every value of its search parameter did not reach its index,
   and its rows take no rank.

Rules 1, 2, 4 and 7 are enforced in code. `AUDIT.md` § What the code enforces
lists where.

## What is measured

| setting | value |
|---|---|
| cases | `Performance768D1M`, `Performance768D10M` |
| dataset | Cohere, 1,000,000 and 10,000,000 vectors, 768 dimensions |
| metric | cosine |
| top-k | 100 |
| concurrency | 1, 5, 10, 20, 30, 40, 60, 80 |
| duration per concurrency level | 30 s |
| machine | Azure `Standard_D16ads_v7`, 128 GB OS disk, 880 GiB local NVMe, Ubuntu 24.04, one VM per engine |
| client | VectorDBBench, on the same VM as the engine |
| region | `eastus` |
| equivalents | GCP `n2-standard-16`, AWS `m6i.4xlarge`, the same 16 vCPU / 64 GB class |

Each engine is swept across its search-time parameter (`ef_search`, `probes`,
`nprobe`, depending on the engine), one full run per value, which produces a
recall/QPS curve. Ranking takes the highest-QPS point that reached recall
≥ 0.90. An engine that never reaches 0.90 is listed with its best recall and no
rank.

Ranked points sit at whatever recall each engine reached above 0.90, so two
ranked rows can be compared at different recalls. The board's recall filter
re-picks every engine's point at 0.95 and 0.99.

## Index configuration

Build parameters are held constant where the engines express the same concept:
`M = 16`, `efConstruction = 300`. Graph engines that use different parameter
names get the nearest equivalent. Engines with a different kind of index
(DiskANN, IVF, RaBitQ) are configured at their documented defaults.

Each engine's index and compression, from `index_config` in
`harness/matrix.json`:

| Engine | Index and compression |
|---|---|
| Apache Doris | HNSW ann_index, float32 |
| Chroma | HNSW float32 |
| CockroachDB | C-SPANN vector index, float32 |
| Elasticsearch | int8_hnsw with rescore, oversample 2.0 |
| Infino | resident HNSW, Infino stores vectors as Sq16 internally (hnsw_ivf) |
| LanceDB | IVF_HNSW_SQ (scalar quantization) |
| MariaDB | HNSW, MariaDB stores vectors as binary16 internally |
| Milvus | HNSW_SQ, SQ4U with FP16 refinement, refine_k 2 |
| OceanBase | HNSW_SQ, M 16, efConstruction 300 |
| OpenSearch | HNSW + scalar quantization (fp16), force-merge to 1 segment |
| Qdrant | HNSW float32 |
| Redis | HNSW float32 (the client offers FLOAT32 or FLOAT64 only) |
| TiDB | HNSW on a TiFlash replica, float32 (the client sets no index parameters) |
| VectorChord | vchordrq (RaBitQ) with residual quantization |
| Vespa | HNSW float32 (the only quantized option, binary, has no rescoring) |
| Weaviate | HNSW float32 |
| pgvecto.rs | HNSW, no quantization, vectors 0.4.0 |
| pgvector | HNSW on halfvec (fp16) |
| pgvectorscale | StreamingDiskANN, memory_optimized storage (SBQ), rescore 400 |

The board's quantized and float32 filters follow what the run selects. A run
that picks a compressed index or vector type is quantized. Infino and MariaDB
take float32 vectors and compress them internally with no setting in the run,
so they are listed as float32 and the table above says how each stores them.
What the filters compare is the configuration a user chooses, and recall shows
what the storage costs.

The pgvectorscale rescore depth has to exceed k, or recall is capped by how many
candidates are checked at full precision. pgvector stores the table as halfvec
too, so the query orders by the same expression the index is built on.

The Infino and TiDB clients write no compression into their result files. The
Infino client runs in-process, and its search runs behind a loopback server so
the concurrency levels are processes, as they are for the other engines.

Postgres-family engines all get the same server settings (`shared_buffers 16GB`,
`maintenance_work_mem 16GB`, 16 parallel workers, JIT off).

## Running it

`scripts/azure_fleet.py` puts one engine on one VM. The whole leg travels in
cloud-init: the harness scripts, the engine's settings and the runner. The VM
starts the benchmark at boot and needs nothing from the machine that launched
it. `AZURE_RG` names the resource group the VMs go in.

```
python scripts/azure_fleet.py up                 # every engine in the matrix
python scripts/azure_fleet.py up pgvector redis  # a subset
python scripts/azure_fleet.py status             # per-leg progress
python scripts/azure_fleet.py verify             # parse each leg's real arguments with the real CLI
python scripts/azure_fleet.py collect            # results and logs into runs/<stamp>/
python scripts/azure_fleet.py relaunch <stamp> <engine>   # re-run a leg after a fix
python scripts/azure_fleet.py down               # delete everything the run created
```

`CASE=Performance768D10M` selects the 10M case. Teardown deletes the resource
ids recorded in `runs/<stamp>/fleet.json` and nothing else, so a run can share a
resource group with unrelated resources. Every VM also gets an Azure
auto-shutdown schedule, so a leg that wedges stops billing compute.

SSH is opened to the launching machine's address alone. `reopen` points that
rule at a new address.

`verify` is the check that decides whether a config is right. `check_matrix.py`
reads the client sources and cannot tell which shared options a given command
composes in, so it passes arguments the CLI then rejects. `verify` runs the real
command with the real arguments under `--dry-run`, which builds the whole task
config and stops before the benchmark.

Then fold the results in and rebuild:

```
python scripts/collect_runs.py runs/<stamp> data/
python scripts/build_leaderboard_data.py data/
python scripts/build_page.py .
```

## The engine code

Every leg installs `infino-ai/VectorDBBench` at `open-leaderboard`, which is
VectorDBBench plus the client fixes listed in `AUDIT.md`. Infino's leg installs
`open-leaderboard-infino`, which adds the Infino client. Each result file
records the repository, ref and commit it was measured with, so a number and
the code that produced it stay together.
