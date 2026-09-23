# Audit

Defects found in the VectorDBBench clients and in this harness while producing
the results on the board, and what was changed for each. Every client fix is on
[infino-ai/VectorDBBench](https://github.com/infino-ai/VectorDBBench) at
`open-leaderboard`, and each result file records the repository, ref and commit
it was measured with.

## Client defects

Sixteen defects in client code had to be fixed before these engines produced a
usable result:

| Client | Defect | Effect |
|---|---|---|
| Chroma | `ChromaIndexConfig` inherits `host: SecretStr = "localhost"` and is constructed without a host, so the field keeps a plain string | pydantic's secret serializer raises while the result file is written, and every run ends holding no result |
| MariaDB | the two f-strings building `select_sql` join with no space | every unfiltered search sends `FROM bench.vdbbench_tableORDER by ...` and fails on syntax |
| MariaDB | one cursor shared across the loader's four workers | the load fails and retries until it gives up |
| ClickHouse | one `clickhouse_connect` session shared across the loader's four workers | ClickHouse refuses concurrent queries inside a session, so the load never finishes |
| OpenSearch (self-hosted) | the `sq` encoder is sent without the `bits` parameter that OpenSearch 3.6 and later require, on both the plain and the `--clip` path | index creation returns 400, the leg exits zero, and every metric in the result file is zero |
| Redis | the `redis` extra carries no upper bound, and the client imports `redis.commands.search.indexDefinition`, which redis-py renamed to `index_definition` in 5.0 | a fresh install resolves redis-py 8.1.0 and the client cannot be imported at all |
| Vespa | both rank profiles declare an empty first phase and inherit the default profile, so a query carrying only a `nearestNeighbor` operator scores every hit the same under nativeRank | the search reaches the HNSW index and the ordering then discards what it found: recall 0.087 at ef 100, rising only to 0.098 at ef 500 |
| Chroma | `search_param` returns a flat `{"ef_search": n}` where `optimize` hands it to `collection.modify(configuration=...)`, which takes the nested shape `index_param` already builds. `optimize` is also the only place ef_search is applied, and the runner calls it only while loading | ef_search never reaches the collection, and even corrected it cannot be varied without reloading a million vectors: recall 0.8773 at every one of ef 100, 150, 200, 300 and 500 |
| pgvector | the binary COPY path for a halfvec table writes a numpy float16 array, and psycopg asks a halfvec value for `to_binary()` | the first batch aborts the transaction and the load ends with zero rows; the run then searches an empty table and reports 124,406 qps at recall 0 |
| pgvecto.rs | `PgVectoRSConfig.user_name` is declared `str` while every command passes a `SecretStr` | no pgvecto.rs command can build its config, so the leg never starts |
| MariaDB | `optimize()` is declared `optimize(self)` while every performance case calls `db.optimize(data_size=...)` | the call raises `TypeError`, so the `ALTER TABLE ... ADD VECTOR KEY` inside it never runs and the table is searched with no vector index: recall 1.0000 at 9.4 qps, p99 1.15 s, unchanged across the sweep |
| Weaviate | `insert_embeddings` opens `with self.client.batch as batch:` on every call, and the loader's four worker threads share one client, so they share one batch object. Leaving that context flushes it and shuts down its executor | a thread still inside the context submits to a dead executor and raises `RuntimeError: cannot schedule new futures after shutdown`, which the `WeaviateBaseError` clause does not catch, so the load fails. It is timing dependent: of five loads in one leg, four failed on one pass and three on the next |
| ClickHouse | `search_param()` returns `{"metric_type": ..., "params": {"ef": ef}}` and `search_embedding` reads `metric_type` out of it and nothing else | the ef the sweep sets is computed and never sent, so the index answers at its own default at every value: recall 0.9901 at each of ef 100, 150, 200, 300 and 500, measured here |
| Weaviate | the `weaviate` extra carries no upper bound, and the client calls `weaviate.AuthApiKey` and type-hints `weaviate.Client`, both of which weaviate-client removed in v4 | a fresh install resolves v4 and the client raises on the attribute before it reaches the server |
| Elasticsearch | the `elastic` extra carries no upper bound | a fresh install resolves client 9.5.1, which sends `Accept: application/vnd.elasticsearch+json; compatible-with=9`. Every 8.x server answers `media_type_header_exception`, `Accept version must be either version 8 or 7, but found 9`. The drop fails, then the serial search fails five times, and the result file holds zeros |
| Vespa | `search_embedding` sets `targetHits` to k in the YQL and then calls `query()` with no `hits` parameter, so Vespa applies its default of 10. `search_documents`, in the same file, passes `"hits": k` | at k=100 recall cannot exceed 0.1000, and once the ranking was fixed every point of the sweep returned exactly that |

Each sits on the only code path the standard performance cases take. Every fix
is on the fork, and each leg records the repository, ref and commit it was
measured with. Three need no code at all. Pinning redis-py below 5 restores the
module name the client imports, pinning weaviate-client below 4 restores the
`weaviate.Client` the client calls, and pinning the Elasticsearch client to the
server's version restores a media type the server accepts. In all three an extra
with no upper bound resolves to a newer release than the client was written
against.

Three of the sixteen are one mistake: a handle that is not thread safe, used
from the loader's four worker threads. MariaDB shares a cursor, ClickHouse
shares a session, Weaviate shares a batch. The first two fail every time. The
third depends on thread interleaving, which is why it read as flakiness until
the log showed the same `RuntimeError` at each failed point.

ClickHouse needs its claim stated narrowly. What was established is that the
client computes a search parameter and never sends it, that all five points of
the sweep returned identical recall, and that 8.9 qps at 0.9896 recall with a
p99 of 127 ms is what scanning a million vectors looks like on this machine.
Whether the `vector_similarity` index was built and simply never chosen by the
planner was not established: an EXPLAIN on a small table showed no granule
pruning either way, which is what a single index granule over the whole table
looks like and settles nothing. The rows are kept off the board, since the 1M
sweep is one point five times and the 10M leg produced nothing, and the five 1M
result files stay in `results/clickhouse`. What ClickHouse does with an index
the query actually uses is not measured here.

The MariaDB defect has a second half worth recording. With `optimize()` fixed,
the `ALTER TABLE ... ADD VECTOR KEY` it calls took 2,297 seconds to build an
mhnsw index over a million 768-dimension vectors, against 650 seconds to insert
them, and it ran single threaded at four percent of a sixteen core machine. The
index that build produces is worth the wait: 2,632.9 qps at 0.9551 recall,
against 9.4 qps at recall 1.0000 for the same table scanned without it.

One more behaviour shapes every curve here. VectorDBBench writes one file per `task_label`, so a parameter sweep that reuses
a label overwrites each earlier point and leaves only the last. A sweep run that
way reports every engine at its slowest setting. Each point on this board
carries its search parameter in its own label.

## Harness defects

Bringing an engine up: a bind mount docker created as root under an engine running as uid 1000, a Vespa config server that refuses
to start when `--network host` shows it the cloud FQDN, a wait on a Vespa port
that only opens after the client deploys its application, and the TiDB config
files its compose file mounts and nothing wrote. Configuring one: a
pgvectorscale rescore depth below k, which caps recall whatever else is swept; a
pgvector index built on a halfvec expression while the table stayed at full
precision, so the query ordered by something the index did not cover and
Postgres scanned; a Postgres container given 8 GB of shared memory while
`maintenance_work_mem` asked a parallel index build for 16 GB, which fails the
build and leaves the table to be scanned; and several flags named or typed
differently from the guess, which the client rejects at startup and costs a VM
rather than a number.

VectorChord's CLI offers `rabitq4` and `rabitq8`, and vchord 0.4.3 defines no operator class of either name, so index
creation fails with `operator class "rabitq4_cosine_ops" does not exist` and the
run scans. The choice is the harness's; that the client offers a value its own
shipped extension rejects is not.

pgvecto.rs was pinned to extension 0.2.0, whose `vector` type declares no binary receive or send function at all
(`typreceive` and `typsend` both come back as `-`), while the client uses a
binary COPY to load and a binary query to search. Every insert failed with `no
binary input function available for type vector` and the aborted transaction
took the rest of the run with it. 0.3.0 and 0.4.0 both declare
`_vectors_vecf32_recv` and `_vectors_vecf32_send`, so the client was right and
the pin was wrong.

`apache/doris:fe-4.1.4` bundles OpenJDK 17.0.2, built in January 2022, and that JVM builds its cgroup controller map from
`/proc/cgroups`. The kernel on these machines lists thirteen controllers there
and `memory` is not among them, so the lookup returns null, the JVM throws
before `main`, BDB JE fails its static init, and the frontend logs `error to
open replicated environment. will exit.` and does. Nothing in the frontend's
own logs names a cgroup. The leg passes `-XX:-UseContainerSupport`, which is
the only code path that reads that file, and the heap is pinned in `fe.conf`
anyway.

The Doris client needs Doris 4.x. 2.1.0 and 3.0.5
and 3.1.4 all answer `Unknown system variable 'hnsw_ef_search'`, and 3.1.4,
the newest 3.x, answers `mismatched input 'ANN' expecting {BITMAP, INVERTED,
NGRAM_BF}` to the index DDL. 4.1.4 has `hnsw_ef_search` at a default of 32 and
accepts `USING ANN`. Every released 4.x accepts it.

The sweep: VectorDBBench calls `optimize()` only on an invocation that loads, and the Weaviate client applies `ef` there, so five
points that skipped the load all queried at the value the first point set and
returned 0.8693 recall five times. Weaviate is the only engine here whose
search parameter lives in `optimize()` alone, and its leg now reloads and
rebuilds on every point. Chroma keeps its parameter there too and runs as a
single point, for the separate reason in the list above.

`oceanbase/oceanbase-ce` runs `MODE=MINI` unless told otherwise, and MINI means an observer with 6 GB. A
million 768-dimension vectors with an HNSW index over them do not fit, so the
search fails with `4013`, `No memory or reach tenant memory limit`, and the leg
writes zeros. Elasticsearch gets a 31 GB heap on the same 64 GB machine. A board
that takes the image default measures a 6 GB database against a 31 GB one and
calls the difference an engine. This leg runs `MODE=NORMAL` with a 40 GB
observer and a 12 GB tenant, the largest tenant the image's bootstrap will
create.

CockroachDB was the same shape of mistake and went unnoticed because it produced
a plausible number. `--cache` defaults to 128 MiB, and the first ranked row for
it, 125.5 qps at 0.9435 recall, was measured with that. Re-run with 22 GB of
block cache and 16 GB for SQL, the same beam size returns 183.4 qps at 0.9435.
The default was costing it 46% of its throughput at the same recall, and
nothing about the first number looked wrong. Every other engine on this board was
given tens of gigabytes: a 31 GB heap for Elasticsearch and OpenSearch, 16 GB of
`shared_buffers` for the Postgres family, a 32 GB buffer pool for MariaDB, 56 GB
for Redis. Checking the whole set found these two and nothing else.

The same check found a third, in storage rather than memory. The Postgres family
mounts its data directory on the machine's local NVMe through one hard-coded
pair of docker arguments, and `timescale/timescaledb-ha` keeps `PGDATA`
somewhere that pair does not reach. So pgvectorscale alone kept its data on the
OS disk while pgvector, VectorChord and pgvecto.rs ran on NVMe, and its 719.5
qps was measured that way. The mount is now read from whichever image is
running, and on NVMe the same configuration returns 751.1 qps at 0.9450 recall
with the index built in 1,725 s instead of 2,039 s. pgvector, VectorChord and
pgvecto.rs keep the rows they have, because the old arguments and the new ones
both put their data on the same NVMe.

A fourth default was the harness's own. VectorDBBench hands the client 100 rows
per `insert_embeddings` call, and the Doris client turns each call into one
stream load, so a million rows is ten thousand stream loads: 96 rows a second,
a 2.9 hour load where every other engine here loads in 8 to 23 minutes. That
leg passes `--insert-batch-size 10000`, and its row says so, because it is the
only engine on this board not loading at the default.

Two of those are the same mistake in different clothes. A setting that
quietly stops an index being used costs nothing at startup and produces a
plausible number, which is why the board checks whether a swept parameter
changed anything before it ranks a row.

## Memory at 10M

The 1M board holds one machine constant and that works, because a million
768-dimension vectors are 2.9 GiB and every engine has room. At 10M the corpus
is 28.6 GiB on a 64 GB machine, and holding the machine constant stops being a
neutral choice: it decides the result for any engine that keeps vectors at full
precision.

Redis is the clearest case. The kernel killed it during the load:

```
Out of memory: Killed process 6215 (redis-server)
  total-vm:66198676kB, anon-rss:55114776kB
```

55.1 GB resident, against a corpus of 28.6 GiB. RediSearch keeps the vectors in
the hash fields and the HNSW index keeps its own copy, so 10M at float32 wants
roughly twice the corpus, and the loader asking for memory at the same time was
what tipped the machine over. `maxmemory 56gb` was set for the 1M run, where
Redis used a few gigabytes; at 10M it is above what the machine can give.

Weaviate, also float32, produced a weaker form of the same evidence, and the
difference is worth keeping. Azure reported the VM running for the whole leg,
while sshd could not complete a banner exchange for over ninety minutes,
including on a connection given a 180 second timeout. That is what a machine
under severe memory pressure looks like from outside, and it is all that was
observed: the logs were on the far side of the unreachable sshd, so there is no
kernel record here of a process being killed the way there is for Redis.

OpenSearch is the third, and the one that shows the arithmetic plainly. Its 1M
leg runs a 31 GB heap with the k-NN circuit breaker at 70% of the machine, which
already permits more than the machine has; at 1M nothing claims it, so nothing
breaks. At 10M the off-heap FAISS index is real, and the container was killed
with exit 137 while searching. The 10M leg runs a 16 GB heap and a 50% breaker
so the two together stay inside 64 GB.

OceanBase is a fourth, and it cannot be fixed on this machine. The image's
bootstrap refuses to create a tenant larger than 12 GB: 16, 20 and 24 all fail
on `create resource pool`, at a 48 GB observer as well as a 40 GB one. 12 GB
serves 1M comfortably and does not serve 10M, and the leg wrote five result
files with no measurement while its container stayed up for three hours and the
host stopped answering ssh. The exact error is not recorded, because the harness
deleted the machine before the log was copied off it.

## What the code enforces

Four of the rules in `METHODOLOGY.md` are enforced in code:

- The runner reads the version back out of the running engine after the
  benchmark and writes it into every result file, so a number and the version
  that produced it cannot be separated downstream.
- The runner exits non-zero when no point in a leg measured both a throughput
  and a recall above zero. A case that cannot reach its engine is a warning
  inside VectorDBBench, so without this a failed leg reports success and writes
  a file of zeros.
- `scripts/collect_runs.py` drops a row that cannot name its measurement date,
  its engine version or its machine, drops a row whose throughput or recall is
  zero, and drops a row measured on any machine other than the baseline.
- `scripts/build_leaderboard_data.py` compares recall across a series and
  withholds the rank where no value of the search parameter changed it.
