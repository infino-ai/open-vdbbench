# WARM preliminary results

Raw `vectordbbench warm` output for Performance768D1M (Cohere 1M, 768D, cosine, k=100) on an Apple M4, 10 cores, 32 GB, macOS 27.0, a shared machine. Client: warm-python 0.6.4 (PyPI), source tag py-v0.6.4. One file per `--ef-search` value (120, 150, 200, 300, 500, 800, and a repeat `rep2-ef800`); the file without an ef suffix is the load run at ef 128.

Edits to the CLI output: `task_label` and `db_label` carried an abbreviated commit hash that did not identify the wheel's source and now read `py0.6.4`; the `note` of the ef 128 file now names the wheel and tag; the local index directory in `path` is replaced by `<index dir>`. No measured value was changed.

QPS and latency in these files were measured with load average 7 to 25 and are unreliable to several-fold. Recall is deterministic. `probe.py` and `probe-output.txt` are an offline recall probe of the same saved index at ef 100 to 2000 (recall 0.8565 at ef 100, 0.9975 at ef 2000).

These are not board results. A collection run for this engine replaces this directory.
