#!/usr/bin/env python3
"""Fold a completed run directory into data/measured_results.json.

Normalizes the result files this harness produced into one schema for the
leaderboard builder. Every row keeps the run_meta the runner stamped on it, which is where the version,
machine and measurement timestamp live.

  collect_runs.py runs/<stamp> data/
"""
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import (CASES, BUILD_KEYS, SEARCH_KEYS, QUANT_KEYS,  # noqa: E402
                         BASELINE_VM, machine_of, pick, quant_summary)


def publish(path, engine):
    """Copy a result file into results/ and return its repo-relative path.

    Every row links to the JSON it came from, so the JSON has to be in the
    repository. runs/ holds raw output and is not committed.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest_dir = os.path.join(root, "results", engine)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    shutil.copyfile(path, dest)
    return f"results/{engine}/{os.path.basename(path)}"


def when(row):
    """When this row was measured, for deciding which of two waves is later."""
    meta = row.get("run_meta") or {}
    return str(meta.get("measured_at") or row.get("test_date") or "")


def main(rundir, datadir):
    rows = []
    foreign = 0
    # runs/<stamp>/<engine>/result_*.json. A recursive match means the
    # directory holding every stamp collects all of them, and one stamp
    # still collects just that run.
    paths = sorted(glob.glob(os.path.join(rundir, "**", "result_*.json"),
                             recursive=True))
    off_baseline = 0
    for path in paths:
        doc = json.load(open(path))
        meta = doc.get("run_meta") or {}
        engine = meta.get("engine_id") or os.path.basename(os.path.dirname(path))
        # VectorDBBench installs editable, so its results directory is inside
        # the clone, which ships result files of its own. Those
        # files sit beside the ones this run wrote and carry this run's stamped
        # metadata, so the task label is what separates them.
        label = str(doc.get("task_label") or "")
        if meta.get("task_label") and not label.startswith(meta["task_label"]):
            foreign += 1
            continue
        # Earlier waves ran on Standard_D16as_v7, which is the same 16 vCPU and
        # 64 GB with a Premium SSD instead of local NVMe. Nothing marks that
        # apart once the rows are side by side, so they are dropped here.
        if meta.get("vm_size") and meta["vm_size"] != BASELINE_VM:
            off_baseline += 1
            continue
        for r in doc.get("results", []):
            tc = r.get("task_config", {}) or {}
            dc = tc.get("db_config", {}) or {}
            cc = tc.get("db_case_config", {}) or {}
            cs = tc.get("case_config", {}) or {}
            mt = r.get("metrics", {}) or {}
            cid = cs.get("case_id")
            cname, dsname, dsrows, dsdim = CASES.get(cid, (str(cid), None, None, None))
            measured = (meta.get("measured_at") or "")[:10] or None
            rows.append({
                "source_file": f"results/{engine}/{os.path.basename(path)}",
                "_src_path": path,
                "_engine": engine,
                "vendor_dir": engine,
                "db": tc.get("db") or meta.get("engine_id"),
                "db_label": dc.get("db_label") or meta.get("db_label") or "",
                "machine": machine_of(dc.get("db_label") or meta.get("db_label") or "",
                                      meta.get("vm_size")),
                "version": dc.get("version") or meta.get("engine_version_reported") or "",
                "note": f'{meta.get("vm_size","")} {meta.get("cloud","")}'.strip(),
                "test_date": measured,
                "task_label": doc.get("task_label") or meta.get("task_label") or "",
                "run_id": doc.get("run_id") or "",
                "case_id": cid,
                "case": cname,
                "dataset": dsname,
                "dataset_rows": dsrows,
                "dim": dsdim,
                "k": cs.get("k"),
                "index": cc.get("index") or cc.get("index_type") or "",
                "metric": cc.get("metric_type") or "",
                "quantization": quant_summary(cc),
                "build_params": pick(cc, BUILD_KEYS),
                "search_params": pick(cc, SEARCH_KEYS),
                "quant_params": pick(cc, QUANT_KEYS),
                "full_index_config": {k: v for k, v in cc.items() if v not in (None, "", [], {})},
                "qps": mt.get("qps"),
                "recall": mt.get("recall"),
                "ndcg": mt.get("ndcg"),
                "latency_p99_s": mt.get("serial_latency_p99"),
                "latency_p95_s": mt.get("serial_latency_p95"),
                "load_duration_s": mt.get("load_duration"),
                "insert_duration_s": mt.get("insert_duration"),
                "optimize_duration_s": mt.get("optimize_duration"),
                "conc_num_list": mt.get("conc_num_list") or [],
                "conc_qps_list": mt.get("conc_qps_list") or [],
                "max_load_count": mt.get("max_load_count"),
                "run_meta": meta,
            })

    # A row that cannot say when, on what, and at which version it was measured
    # does not get published.
    #
    # A leg whose engine refused the index still exits zero and still writes a
    # result file, with every metric at zero. Those are failures wearing the
    # shape of a measurement, so they are dropped here rather than ranked.
    kept, dropped, empty = [], [], []
    for r in rows:
        if not r["qps"] or not r["recall"]:
            empty.append(r)
        elif r["test_date"] and r["version"] and (r.get("run_meta") or {}).get("vm_size"):
            kept.append(r)
        else:
            dropped.append(r)

    # Rebuilt from the run directory rather than merged into what was there
    # before, so a re-run replaces its own earlier numbers instead of publishing
    # both. Collecting several runs means passing the directory that holds them.
    # One row per engine, case and search-parameter value. A later wave of the
    # same point supersedes an earlier one rather than publishing both.
    # A curve comes from one run: one VM, one load, one index build. When an
    # engine and case were measured again, the newer run replaces the older one
    # whole, so no curve mixes points from two builds of the index.
    latest = {}
    for r in kept:
        key = (r.get("db"), r.get("case"))
        if key not in latest or when(r) > latest[key][1]:
            latest[key] = ((r.get("run_meta") or {}).get("task_label") or "", when(r))
    older = sum(1 for r in kept
                if ((r.get("run_meta") or {}).get("task_label") or "") != latest[(r.get("db"), r.get("case"))][0])
    kept = [r for r in kept
            if ((r.get("run_meta") or {}).get("task_label") or "") == latest[(r.get("db"), r.get("case"))][0]]

    best = {}
    superseded = older
    for r in kept:
        point = str(r.get("task_label") or "").split("_ef")[-1]
        key = (r.get("engine_id") or r.get("db"), r.get("case"), point)
        prev = best.get(key)
        if prev is None:
            best[key] = r
        else:
            superseded += 1
            # measured_at lives in run_meta, not on the row. Reading it off the
            # row made both sides None, so the first file the glob reached won
            # and an older wave outranked the re-run that replaced it.
            if when(r) > when(prev):
                best[key] = r

    out = os.path.join(datadir, "measured_results.json")
    merged = sorted(best.values(), key=lambda r: (str(r.get("db")), str(r.get("task_label"))))

    # Only a row the board publishes gets its file copied in beside it. Copying
    # first would leave results/ holding files for rows that were dropped for
    # measuring nothing, which reads as a published measurement of zero.
    touched = {}
    for r in merged:
        engine = r.pop("_engine")
        publish(r.pop("_src_path"), engine)
        touched.setdefault(engine, set()).add(os.path.basename(r["source_file"]))
    for r in rows:
        r.pop("_src_path", None)
        r.pop("_engine", None)

    # A sweep whose ladder changed leaves files behind for values it no longer
    # runs, and they would sit in the repository looking published. Only the
    # engines this pass wrote to are pruned, so collecting one run leaves the
    # others alone.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stale = 0
    for engine, keep in touched.items():
        d = os.path.join(root, "results", engine)
        for name in sorted(os.listdir(d) if os.path.isdir(d) else []):
            if name not in keep:
                os.remove(os.path.join(d, name))
                stale += 1
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=1, sort_keys=True)

    print(f"{len(rows)} rows read, {len(kept)} kept, "
          f"{len(dropped)} dropped for missing provenance, "
          f"{len(empty)} dropped for measuring nothing, "
          f"{foreign} files skipped as belonging to another run, "
          f"{off_baseline} off the baseline machine, "
          f"{superseded} superseded by a later wave, "
          f"{stale} stale files pruned from results/")
    for r in empty[:10]:
        print(f"  no measurement {r['db']} {r['source_file']}")
    for r in dropped[:10]:
        print(f"  dropped {r['source_file']} (date={r['test_date']} version={r['version']!r})")
    print(f"{len(merged)} total in {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "runs",
         sys.argv[2] if len(sys.argv) > 2 else "data")
