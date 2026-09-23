#!/usr/bin/env bash
# Per-engine bring-up, version probe and teardown. Sourced by run_peer_vdbbench.sh.
#
# Contract: each engine defines up_<id>, version_<id> and down_<id>.
#   up_<id>       start the engine and block until it answers queries
#   version_<id>  echo the version the engine actually reports at runtime
#   down_<id>     stop it
# Ids use underscores (opensearch_selfhosted), matrix ids use hyphens.

set -uo pipefail

BENCH_DIR=${BENCH_DIR:-/mnt/bench}
DOCKER="sudo docker"

host_dir() {  # create bind-mount sources before docker does
  # A missing bind-mount source is created by dockerd as root, and an engine
  # that runs as a non-root user cannot then write into it. Creating it here,
  # as the unprivileged user the harness runs as, gives the engine a writable
  # directory.
  mkdir -p "$@"
}

wait_http() {  # url, tries, label
  local url=$1 tries=${2:-120} label=${3:-engine}
  for i in $(seq 1 "$tries"); do
    if curl -fsS -m 5 "$url" >/dev/null 2>&1; then echo "$label up after ${i}0s"; return 0; fi
    sleep 10
  done
  echo "::error::$label never became healthy at $url"; return 1
}

wait_tcp() {  # host, port, tries, label
  local host=$1 port=$2 tries=${3:-120} label=${4:-engine}
  for i in $(seq 1 "$tries"); do
    if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then exec 3>&- ; echo "$label port open after ${i}0s"; return 0; fi
    sleep 10
  done
  echo "::error::$label never opened $host:$port"; return 1
}

pg_wait() {  # tries
  for i in $(seq 1 "${1:-60}"); do
    if PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d postgres -c 'select 1' >/dev/null 2>&1; then
      echo "postgres up after ${i}0s"; return 0
    fi
    sleep 10
  done
  echo "::error::postgres never accepted connections"; return 1
}

pg_prepare() {  # create the bench database and the extension the client expects
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d postgres \
    -c "DROP DATABASE IF EXISTS bench" -c "CREATE DATABASE bench" >/dev/null
  for ext in "$@"; do
    PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench \
      -c "CREATE EXTENSION IF NOT EXISTS $ext CASCADE" >/dev/null
  done
  # Give the build the machine. These are load-time settings, identical for every
  # postgres-family engine, so the comparison between them stays fair.
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench >/dev/null <<'SQL'
ALTER SYSTEM SET shared_buffers = '16GB';
ALTER SYSTEM SET maintenance_work_mem = '16GB';
ALTER SYSTEM SET work_mem = '256MB';
ALTER SYSTEM SET effective_cache_size = '48GB';
ALTER SYSTEM SET max_parallel_maintenance_workers = 15;
ALTER SYSTEM SET max_parallel_workers = 16;
ALTER SYSTEM SET max_parallel_workers_per_gather = 8;
ALTER SYSTEM SET max_worker_processes = 32;
ALTER SYSTEM SET jit = off;
SQL
  $DOCKER restart pgengine >/dev/null && sleep 15 && pg_wait 30
}

# ----------------------------------------------------------------- OpenSearch
up_opensearch_selfhosted() {
  host_dir "$BENCH_DIR/os-data"
  # The heap and the k-NN circuit breaker have to add up to less than the
  # machine. A 31g heap plus an off-heap FAISS index for 10M vectors does
  # not, and the container was killed with 137 partway through searching.
  $DOCKER run -d --name engine --network host \
    -e discovery.type=single-node \
    -e DISABLE_SECURITY_PLUGIN=true \
    -e DISABLE_INSTALL_DEMO_CONFIG=true \
    -e "OPENSEARCH_JAVA_OPTS=-Xms${OS_HEAP:-31g} -Xmx${OS_HEAP:-31g}" \
    -e "knn.memory.circuit_breaker.limit=${OS_KNN_CB:-70%}" \
    --ulimit memlock=-1:-1 --ulimit nofile=65536:65536 \
    -v "$BENCH_DIR/os-data:/usr/share/opensearch/data" \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:9200 120 opensearch
}
version_opensearch_selfhosted() { curl -fsS http://localhost:9200 | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"]["number"])'; }
down_opensearch_selfhosted() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------------- Redis
up_redis() {
  host_dir "$BENCH_DIR/redis-data"
  $DOCKER run -d --name engine --network host \
    -v "$BENCH_DIR/redis-data:/data" \
    "$ENGINE_IMAGE" \
    redis-stack-server --save "" --appendonly no --maxmemory "${REDIS_MAXMEMORY:-56gb}" --maxmemory-policy noeviction >/dev/null
  wait_tcp 127.0.0.1 6379 60 redis
}
version_redis() { $DOCKER exec engine redis-cli INFO server | awk -F: '/^redis_version/{print $2}' | tr -d '\r'; }
down_redis() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# --------------------------------------------------------------------- Chroma
up_chroma() {
  host_dir "$BENCH_DIR/chroma-data"
  $DOCKER run -d --name engine --network host \
    -v "$BENCH_DIR/chroma-data:/data" \
    -e IS_PERSISTENT=TRUE -e ANONYMIZED_TELEMETRY=FALSE \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:8000/api/v2/heartbeat 120 chroma \
    || wait_http http://localhost:8000/api/v1/heartbeat 30 chroma
}
version_chroma() {
  curl -fsS http://localhost:8000/api/v2/version 2>/dev/null | tr -d '"' \
    || curl -fsS http://localhost:8000/api/v1/version | tr -d '"'
}
down_chroma() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------------- Vespa
up_vespa() {
  # With --network host the container sees the Azure FQDN and vespa refuses to
  # start a config server it cannot match to its own host list, so it gets its
  # own network namespace and a fixed hostname. /opt/vespa/var is left alone:
  # the image populates it, and an empty mount over it hides that.
  $DOCKER run -d --name engine --hostname vespa-node \
    -p 8080:8080 -p 19071:19071 \
    "$ENGINE_IMAGE" >/dev/null
  # The config server is the only thing up before an application exists. The
  # client deploys its own package through 19071, and 8080 answers only after
  # that, so waiting on it here just burns the timeout.
  wait_http http://localhost:19071/state/v1/health 120 vespa-config
}
version_vespa() { $DOCKER exec engine vespa-logfmt --version 2>/dev/null | head -1 || curl -fsS http://localhost:19071/state/v1/version | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version",""))'; }
down_vespa() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ----------------------------------------------------------------- ClickHouse
up_clickhouse() {
  host_dir "$BENCH_DIR/ch-conf" "$BENCH_DIR/ch-data"
  cat > "$BENCH_DIR/ch-conf/bench.xml" <<'XML'
<clickhouse>
  <max_server_memory_usage_to_ram_ratio>0.9</max_server_memory_usage_to_ram_ratio>
  <mark_cache_size>8589934592</mark_cache_size>
</clickhouse>
XML
  $DOCKER run -d --name engine --network host \
    --ulimit nofile=262144:262144 \
    -v "$BENCH_DIR/ch-data:/var/lib/clickhouse" \
    -v "$BENCH_DIR/ch-conf:/etc/clickhouse-server/config.d" \
    -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 -e CLICKHOUSE_PASSWORD=bench \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:8123/ping 60 clickhouse
  curl -fsS -u default:bench 'http://localhost:8123/' --data-binary \
    "SET allow_experimental_vector_similarity_index=1" >/dev/null 2>&1 || true
}
version_clickhouse() { curl -fsS -u default:bench 'http://localhost:8123/' --data-binary 'SELECT version()'; }
down_clickhouse() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# -------------------------------------------------------------------- LanceDB
# Embedded: no server. The client opens a table on local disk.
up_lancedb() { mkdir -p "$BENCH_DIR/lancedb"; echo "lancedb is embedded, no server to start"; }
version_lancedb() { python3 -c 'import lancedb;print(lancedb.__version__)' 2>/dev/null || pip show lancedb 2>/dev/null | awk '/^Version/{print $2}'; }
down_lancedb() { :; }

# -------------------------------------------------------------------- MariaDB
up_mariadb() {
  host_dir "$BENCH_DIR/mariadb-data"
  $DOCKER run -d --name engine --network host \
    -e MARIADB_ROOT_PASSWORD=bench \
    -v "$BENCH_DIR/mariadb-data:/var/lib/mysql" \
    "$ENGINE_IMAGE" \
    --innodb-buffer-pool-size=32G \
    --mhnsw-max-cache-size=16G \
    --max-allowed-packet=1G \
    --innodb-flush-log-at-trx-commit=0 >/dev/null
  wait_tcp 127.0.0.1 3306 90 mariadb
  for i in $(seq 1 30); do
    $DOCKER exec engine mariadb -uroot -pbench -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 5
  done
  $DOCKER exec engine mariadb -uroot -pbench -e "DROP DATABASE IF EXISTS bench; CREATE DATABASE bench;"
}
version_mariadb() { $DOCKER exec engine mariadb -uroot -pbench -N -B -e "SELECT VERSION()" | tr -d '\r'; }
down_mariadb() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ----------------------------------------------------------------------- TiDB
# Needs pd + tikv + tidb + tiflash; vector search is served from the TiFlash replica.
up_tidb() {
  host_dir "$BENCH_DIR/tidb" "$BENCH_DIR/tidb/pd" "$BENCH_DIR/tidb/tikv" "$BENCH_DIR/tidb/tiflash"
  cp "$HARNESS_DIR/compose/tidb.yml" "$BENCH_DIR/tidb/docker-compose.yml"
  host_dir "$BENCH_DIR/tidb/conf"
  cp "$HARNESS_DIR"/compose/tidb-conf/*.toml "$BENCH_DIR/tidb/conf/"
  (cd "$BENCH_DIR/tidb" && $DOCKER compose up -d)
  wait_tcp 127.0.0.1 4000 120 tidb
  for i in $(seq 1 60); do
    mysql -h 127.0.0.1 -P 4000 -u root -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 10
  done
  mysql -h 127.0.0.1 -P 4000 -u root -e "DROP DATABASE IF EXISTS bench; CREATE DATABASE bench;"
  # TiFlash must have a store registered before the client asks for a replica.
  for i in $(seq 1 60); do
    n=$(mysql -h 127.0.0.1 -P 4000 -u root -N -B -e \
      "SELECT COUNT(*) FROM information_schema.tiflash_replica" 2>/dev/null || echo 0)
    [ -n "$n" ] && break
    sleep 10
  done
}
version_tidb() { mysql -h 127.0.0.1 -P 4000 -u root -N -B -e "SELECT VERSION()"; }
down_tidb() { (cd "$BENCH_DIR/tidb" && $DOCKER compose down -v) >/dev/null 2>&1 || true; }

# ---------------------------------------------------------- Postgres family
_up_pg() {  # $@ = extra docker args
  $DOCKER run -d --name pgengine --network host \
    -e POSTGRES_PASSWORD=bench -e POSTGRES_USER=postgres \
    --shm-size=24g "$@" \
    "$ENGINE_IMAGE" >/dev/null
  pg_wait 60
}

# The official postgres image initialises as root and then hands the data
# directory to the postgres user, so a bind mount is fine.
# The data directory belongs on the local NVMe, and where that is depends on the
# image: pgvector/pgvector keeps PGDATA under /var/lib/postgresql/data, while
# timescale/timescaledb-ha keeps it under /home/postgres/pgdata. This used to be
# one hard-coded pair that pgvectorscale could not use, so pgvectorscale alone
# kept its data on the OS disk while its three siblings ran on NVMe. Asking the
# image puts all four on the same disk.
pg_disk() {  # sets _PG_DISK for the current $ENGINE_IMAGE
  local pgdata parent
  pgdata=$($DOCKER run --rm --entrypoint sh "$ENGINE_IMAGE" -c 'echo "${PGDATA:-}"'            2>/dev/null | tr -d '
')
  [ -n "$pgdata" ] || pgdata=/var/lib/postgresql/data/pgdata
  parent=$(dirname "$pgdata")
  host_dir "$BENCH_DIR/pg-data"
  sudo chmod 0777 "$BENCH_DIR/pg-data"
  echo "postgres data: $parent -> $BENCH_DIR/pg-data (PGDATA=$pgdata)"
  _PG_DISK=(-e "PGDATA=$pgdata" -v "$BENCH_DIR/pg-data:$parent")
}

up_vectorchord()   { pg_disk; _up_pg "${_PG_DISK[@]}"; pg_prepare vchord; }
version_vectorchord() {
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench -tAc \
    "SELECT current_setting('server_version') || ' / vchord ' || (SELECT extversion FROM pg_extension WHERE extname='vchord')"
}
down_vectorchord() { $DOCKER rm -f pgengine >/dev/null 2>&1 || true; }

up_pgvectorscale()   { pg_disk; _up_pg "${_PG_DISK[@]}"; pg_prepare vectorscale; }
version_pgvectorscale() {
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench -tAc \
    "SELECT current_setting('server_version') || ' / vectorscale ' || (SELECT extversion FROM pg_extension WHERE extname='vectorscale')"
}
down_pgvectorscale() { $DOCKER rm -f pgengine >/dev/null 2>&1 || true; }

up_pgvector()   { pg_disk; _up_pg "${_PG_DISK[@]}"; pg_prepare vector; }
version_pgvector() {
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench -tAc \
    "SELECT current_setting('server_version') || ' / pgvector ' || (SELECT extversion FROM pg_extension WHERE extname='vector')"
}
down_pgvector() { $DOCKER rm -f pgengine >/dev/null 2>&1 || true; }

# --------------------------------------------------------------------- Infino
# Embedded, like LanceDB. INFINO_BENCH_SERVE=1 puts search behind a loopback
# server so the concurrency ladder runs as processes, the way it does for every
# server engine on this board.
up_infino() { host_dir "$BENCH_DIR/infino"; echo "infino is embedded, no server to start"; }
version_infino() { python3 -c 'import infino;print(infino.__version__)' 2>/dev/null || pip show infino 2>/dev/null | awk '/^Version/{print $2}'; }
down_infino() { :; }

# --------------------------------------------------------------------- Milvus
# Milvus standalone, from harness/compose/milvus.yml.
up_milvus() {
  host_dir "$BENCH_DIR/milvus" "$BENCH_DIR/milvus/etcd" "$BENCH_DIR/milvus/minio" "$BENCH_DIR/milvus/data"
  cp "$HARNESS_DIR/compose/milvus.yml" "$BENCH_DIR/milvus/docker-compose.yml"
  (cd "$BENCH_DIR/milvus" && $DOCKER compose up -d)
  wait_http http://localhost:9091/healthz 120 milvus
}
version_milvus() {
  $DOCKER inspect --format '{{index .Config.Image}}' milvus-standalone-1 2>/dev/null | sed 's/.*://' \
    || $DOCKER ps --filter name=standalone --format '{{.Image}}' | sed 's/.*://' | head -1
}
down_milvus() { (cd "$BENCH_DIR/milvus" && $DOCKER compose down -v) >/dev/null 2>&1 || true; }

# -------------------------------------------------------------- Elasticsearch
# Self-hosted, so the quantized index types the harness defines can actually be
# used. Security off: this is a benchmark box with ssh open to one address.
up_elasticsearch() {
  host_dir "$BENCH_DIR/es-data"
  sudo chown -R 1000:1000 "$BENCH_DIR/es-data"
  $DOCKER run -d --name engine --network host \
    -e discovery.type=single-node \
    -e xpack.security.enabled=false \
    -e "ES_JAVA_OPTS=-Xms31g -Xmx31g" \
    --ulimit memlock=-1:-1 --ulimit nofile=65536:65536 \
    -v "$BENCH_DIR/es-data:/usr/share/elasticsearch/data" \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:9200 120 elasticsearch
}
version_elasticsearch() { curl -fsS http://localhost:9200 | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"]["number"])'; }
down_elasticsearch() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# --------------------------------------------------------------------- Qdrant
up_qdrant() {
  host_dir "$BENCH_DIR/qdrant-data"
  $DOCKER run -d --name engine --network host \
    -v "$BENCH_DIR/qdrant-data:/qdrant/storage" \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:6333/readyz 120 qdrant
}
version_qdrant() { curl -fsS http://localhost:6333/ | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version",""))'; }
down_qdrant() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------- CockroachDB
up_cockroachdb() {
  host_dir "$BENCH_DIR/crdb"
  # --cache defaults to 128 MiB, which is what a laptop gets. Cockroach's
  # own guidance for a dedicated node is a quarter of memory for each of
  # the two, and the sum here stays inside the 75% it warns about.
  $DOCKER run -d --name engine --network host \
    -v "$BENCH_DIR/crdb:/cockroach/cockroach-data" \
    "$ENGINE_IMAGE" start-single-node --insecure --http-addr=localhost:8080 \
    --cache=.35 --max-sql-memory=.25 >/dev/null
  wait_http http://localhost:8080/health?ready=1 90 cockroachdb
  for i in $(seq 1 30); do
    $DOCKER exec engine ./cockroach sql --insecure -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 5
  done
  $DOCKER exec engine ./cockroach sql --insecure \
    -e "DROP DATABASE IF EXISTS bench CASCADE; CREATE DATABASE bench;" >/dev/null
}
version_cockroachdb() { $DOCKER exec engine ./cockroach version 2>/dev/null | awk -F': *' '/Build Tag/{print $2; exit}'; }
down_cockroachdb() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ------------------------------------------------------------------ pgvecto.rs
up_pgvecto_rs()   { pg_disk; _up_pg "${_PG_DISK[@]}"; pg_prepare vectors; }
version_pgvecto_rs() {
  PGPASSWORD=bench psql -h 127.0.0.1 -U postgres -d bench -tAc \
    "SELECT current_setting('server_version') || ' / vectors ' || (SELECT extversion FROM pg_extension WHERE extname='vectors')"
}
down_pgvecto_rs() { $DOCKER rm -f pgengine >/dev/null 2>&1 || true; }

# -------------------------------------------------------------------- Weaviate
up_weaviate() {
  host_dir "$BENCH_DIR/weaviate"
  sudo chown -R 1000:1000 "$BENCH_DIR/weaviate"
  $DOCKER run -d --name engine --network host \
    -e AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true \
    -e PERSISTENCE_DATA_PATH=/var/lib/weaviate \
    -e DEFAULT_VECTORIZER_MODULE=none \
    -e CLUSTER_HOSTNAME=node1 \
    -e LIMIT_RESOURCES=false \
    -v "$BENCH_DIR/weaviate:/var/lib/weaviate" \
    "$ENGINE_IMAGE" >/dev/null
  wait_http http://localhost:8080/v1/.well-known/ready 120 weaviate
}
version_weaviate() { curl -fsS http://localhost:8080/v1/meta | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version",""))'; }
down_weaviate() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ------------------------------------------------------------------ OceanBase
# Bootstraps a whole cluster, which takes minutes rather than seconds.
up_oceanbase() {
  host_dir "$BENCH_DIR/ob"
  # MODE=MINI, the image default, gives the observer 6G and carves the
  # tenant out of that. A million 768-dimension vectors and an HNSW index
  # over them do not fit, and the search fails with 4013, no memory or
  # reach tenant memory limit.
  #
  # 12G is the largest tenant this image's bootstrap will create. Above it
  # the run dies on "create resource pool test_pool ... execute failed", at
  # a 40G observer and at a 48G one alike, so the ceiling is the image's
  # rather than the machine's. It holds the working set several times over.
  # OB_TENANT_MIN_CPU is left alone: setting it to 14 fails the tenant with
  # "min_cpu must less then max_cpu", and the image gives the tenant 13 of
  # the 16 cores by itself.
  # The deployer aborts with OBD-1007 unless the observer can open 20000
  # files, and the image leaves the default 1024 in place. The aio and
  # map-count limits below are only warnings, and neither sysctl is
  # namespaced, so they are raised on the host.
  sudo sysctl -qw fs.aio-max-nr=1048576 vm.max_map_count=655360 >/dev/null 2>&1 || true
  # Not --network host. The image bootstraps through OceanBase Deployer, which
  # ssh-es to the address it detects for itself; under host networking that is
  # the VM private IP and the connection fails with OBD-1013 username or
  # password error. In its own namespace it reaches its own localhost.
  $DOCKER run -d --name engine -p 2881:2881 --ulimit nofile=20480:20480 \
    -e MODE=NORMAL -e OB_TENANT_PASSWORD=bench \
    -e OB_MEMORY_LIMIT="${OB_MEMORY_LIMIT:-40G}" -e OB_SYSTEM_MEMORY="${OB_SYSTEM_MEMORY:-6G}" \
    -e OB_TENANT_MEMORY_SIZE="${OB_TENANT_MEMORY_SIZE:-12G}" \
    -e OB_DATAFILE_SIZE="${OB_DATAFILE_SIZE:-80G}" -e OB_LOG_DISK_SIZE="${OB_LOG_DISK_SIZE:-40G}" \
    -v "$BENCH_DIR/ob:/root/ob" \
    "$ENGINE_IMAGE" >/dev/null
  wait_tcp 127.0.0.1 2881 150 oceanbase
  # A failed precheck exits the container, and the port can answer for long
  # enough either way, so the probe alone proves nothing.
  [ "$($DOCKER inspect -f "{{.State.Running}}" engine 2>/dev/null)" = "true" ] || {
    echo "::error::oceanbase container exited during bootstrap"; return 1; }
  # The bootstrap can leave the port answering while the tenant does not yet
  # exist, and a run that starts then reports connection refused at every
  # point rather than saying the engine never came up.
  ok=no
  for i in $(seq 1 60); do
    mysql -h 127.0.0.1 -P 2881 -u root@test -pbench -e "SELECT 1" >/dev/null 2>&1       && { ok=yes; echo "oceanbase tenant usable after ${i}0s"; break; }
    sleep 10
  done
  [ "$ok" = yes ] || { echo "::error::oceanbase tenant never accepted a query"; return 1; }
  mysql -h 127.0.0.1 -P 2881 -u root@test -pbench \
    -e "DROP DATABASE IF EXISTS bench; CREATE DATABASE bench;" >/dev/null 2>&1 || true
}
version_oceanbase() { mysql -h 127.0.0.1 -P 2881 -u root@test -pbench -N -B -e "SELECT VERSION()" 2>/dev/null; }
down_oceanbase() { $DOCKER rm -f engine >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------------- Doris
# The all-in-one image runs the frontend and backend in one container.
up_doris() {
  host_dir "$BENCH_DIR/doris" "$BENCH_DIR/doris/fe-meta" "$BENCH_DIR/doris/fe-log" \
           "$BENCH_DIR/doris/fe-conf" "$BENCH_DIR/doris/be-storage" "$BENCH_DIR/doris/be-log"
  cp "$HARNESS_DIR/compose/doris.yml" "$BENCH_DIR/doris/docker-compose.yml"
  # The frontend bundles JDK 17.0.2, which builds its cgroup controller map
  # from /proc/cgroups. This kernel lists no memory controller there, so the
  # JVM dereferences a null entry, BDB JE fails its static init, and the
  # frontend exits before it opens the journal. Only container support reads
  # that map, and fe.conf pins the heap itself, so turning it off changes
  # nothing else. The appended line is single quoted, so fe.conf keeps the
  # reference and start_fe.sh expands it against the value it sourced above.
  local conf="$BENCH_DIR/doris/fe-conf/fe.conf"
  local fe_image
  fe_image=$(grep -oE "apache/doris:fe-[0-9.]+" "$BENCH_DIR/doris/docker-compose.yml" | head -1)
  [ -n "$fe_image" ] || { echo "::error::no doris frontend image in the compose file"; return 1; }
  $DOCKER run --rm --entrypoint cat "$fe_image" /opt/apache-doris/fe/conf/fe.conf > "$conf" || return 1
  grep -q "^JAVA_OPTS_FOR_JDK_17=" "$conf" || { echo "::error::fe.conf has no JDK 17 opts"; return 1; }
  echo '' >> "$conf"
  echo 'JAVA_OPTS_FOR_JDK_17="$JAVA_OPTS_FOR_JDK_17 -XX:-UseContainerSupport"' >> "$conf"
  chmod 666 "$conf"
  (cd "$BENCH_DIR/doris" && $DOCKER compose up -d)
  wait_tcp 127.0.0.1 9030 120 doris-fe
  for i in $(seq 1 60); do
    mysql -h 127.0.0.1 -P 9030 -u root -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 10
  done
  # A frontend with no live backend accepts SQL and then stores nothing.
  for i in $(seq 1 60); do
    alive=$(mysql -h 127.0.0.1 -P 9030 -u root -N -B -e "SHOW BACKENDS" 2>/dev/null | grep -ci true || true)
    [ "${alive:-0}" -ge 1 ] && { echo "doris backend alive after ${i}0s"; break; }
    sleep 10
  done
  [ "${alive:-0}" -ge 1 ] || { echo "::error::doris frontend has no live backend"; return 1; }
  mysql -h 127.0.0.1 -P 9030 -u root -e "DROP DATABASE IF EXISTS bench; CREATE DATABASE bench;" >/dev/null 2>&1 || true
}
version_doris() { mysql -h 127.0.0.1 -P 9030 -u root -N -B -e "SELECT @@version_comment" 2>/dev/null | head -1; }
down_doris() { (cd "$BENCH_DIR/doris" && $DOCKER compose down -v) >/dev/null 2>&1 || true; }
