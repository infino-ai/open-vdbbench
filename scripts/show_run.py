#!/usr/bin/env python3
"""Print what a run measured, per engine, from the collected result files.

Reads runs/<stamp>/<engine>/result_*.json, so it works after the VMs are gone.

  show_run.py [stamp] [engine ...]
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "runs")


def latest():
    stamps = [d for d in os.listdir(RUNS)
              if os.path.isdir(os.path.join(RUNS, d))]
    if not stamps:
        sys.exit("no runs yet")
    return sorted(stamps)[-1]


def main(argv):
    stamp = argv[0] if argv and not argv[0].startswith("-") else latest()
    want = set(argv[1:])
    base = os.path.join(RUNS, stamp)
    if not os.path.isdir(base):
        sys.exit(f"no such run: {stamp}")

    print(f"run {stamp}\n")
    for engine in sorted(os.listdir(base)):
        d = os.path.join(base, engine)
        if not os.path.isdir(d) or (want and engine not in want):
            continue
        files = sorted(glob.glob(os.path.join(d, "result_*.json")))
        if not files:
            continue
        rows, meta = [], {}
        for path in files:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            meta = doc.get("run_meta") or meta
            label = str(doc.get("task_label") or "")
            point = label.split("_ef")[-1] if "_ef" in label else "single"
            for r in doc.get("results", []):
                mt = r.get("metrics", {})
                rows.append((point, mt.get("qps") or 0, mt.get("recall") or 0,
                             mt.get("serial_latency_p99") or 0,
                             mt.get("load_duration") or 0))
        rows.sort(key=lambda x: (len(x[0]), x[0]))

        # A point that measured nothing is a failed run, and the collector
        # drops it. It must not count as the sweep having moved either.
        real = [r for r in rows if r[1] and r[2]]
        best = max((r for r in real if r[2] >= 0.90), key=lambda r: r[1], default=None)
        moved = len({round(r[2], 4) for r in real}) > 1
        print(f"{engine}  {meta.get('engine_version_reported', '?')}"
              f"  {meta.get('vm_size', '?')}  {meta.get('vdbbench_ref', '?')}")
        for point, qps, recall, p99, load in rows:
            print(f"    {point:<8} qps={qps:<10.1f} recall={recall:.4f} "
                  f"p99={p99 * 1000:.1f}ms" + (f"  load={load:.0f}s" if load else ""))
        if not real:
            print("    -> no rank: the run measured nothing")
        elif not moved and len(real) > 2:
            print("    -> no rank: recall identical at every point in the sweep")
        elif best:
            print(f"    -> {best[1]:.1f} qps at recall {best[2]:.4f}")
        else:
            print(f"    -> no rank: best recall {max(r[2] for r in real):.4f}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
