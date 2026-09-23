#!/usr/bin/env python3
"""Turn the normalized results into the leaderboard's data file.

Ranking rule, applied identically to every engine: within one case, for each
(engine, config series), take the run with the highest QPS among runs that
reached at least MIN_RECALL. An engine that never reaches MIN_RECALL is listed
with its best recall and no rank, rather than dropped.

Nothing here filters by vendor, and every row carries the date, version and
index config it was measured with. A row that cannot supply those is marked,
not hidden.
"""

import json
import re
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import BASELINE_VM, REFERENCE_MACHINE, machine_class  # noqa: E402

MIN_RECALL = 0.90

# Cases the board shows: Cohere 1M and 10M.
BOARD_CASES = [5, 4]


def months_between(iso, today):
    y, m, d = (int(x) for x in iso.split("-"))
    return (today.year - y) * 12 + (today.month - m)


def engine_names():
    """Matrix id to the name this board shows.

    The client reports its own class name, so a self-hosted OpenSearch row would
    read OSSOpenSearch and sit next to the managed OpenSearch rows under a name
    no reader would connect to either.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "harness", "matrix.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {e["id"]: e["display"] for e in json.load(fh)["engines"]}


def engine_configs():
    """Matrix id to the index configuration the harness passed.

    Result files describe their index unevenly: Infino and TiDB write none of it,
    and MariaDB's reads as float32 though the engine stores binary16. The table
    takes every row's description from the configuration the harness actually
    ran, so one rule covers every row.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "harness", "matrix.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {e["id"]: e.get("index_config") for e in json.load(fh)["engines"]}


def clean_version(raw):
    """The release number an engine reported, without the banner around it."""
    if not raw:
        return raw
    v = str(raw)
    m = re.search(r"/\s*(\w[\w.-]*)\s+([\d.]+)", v)
    pg = re.match(r"([\d.]+)\s*\(", v)
    if m and pg:
        return f"{m.group(1)} {m.group(2)} \u00b7 PostgreSQL {pg.group(1)}"
    for pat in (r"OceanBase_CE-v([\d.]+)", r"TiDB-v([\d.]+)", r"doris-([\d.]+(?:-rc\d+)?)",
                r"^([\d.]+)-MariaDB", r"version\s+([\d.]+)$"):
        m = re.search(pat, v)
        if m:
            return m.group(1)
    return v[1:] if re.fullmatch(r"v[\d.]+", v) else v


# Engines measured here and kept off the board. ClickHouse's client never sends
# the ef its sweep sets, so the 1M run is five copies of one unindexed point,
# and its 10M leg produced no result. Its rows stay in results/ and AUDIT.md.
OFF_BOARD = {"clickhouse"}


def peak_concurrency(r):
    """The concurrency at which a point's throughput peaked on the ladder."""
    c, q = r.get("conc_num_list") or [], r.get("conc_qps_list") or []
    if not c or len(c) != len(q):
        return None
    return c[max(range(len(q)), key=lambda i: q[i])]


def main(datadir="data"):
    names = engine_names()
    configs = engine_configs()
    with open(os.path.join(datadir, "measured_results.json"), encoding="utf-8") as fh:
        rows = [r for r in json.load(fh)
                if (r.get("run_meta") or {}).get("engine_id") not in OFF_BOARD]
    today = date.today()

    board = {}
    for cid in BOARD_CASES:
        runs = [r for r in rows if r["case_id"] == cid and r["qps"] and r["recall"]]
        if not runs:
            continue
        series = {}
        for r in runs:
            key = (r["db"], r["db_label"])
            series.setdefault(key, []).append(r)

        entries = []
        for (db, label), rs in series.items():
            qualified = [r for r in rs if r["recall"] >= MIN_RECALL]
            best = max(qualified, key=lambda r: r["qps"]) if qualified \
                else max(rs, key=lambda r: r["recall"])
            # Every point carries what produced it, so a reader who raises the
            # recall bar gets the parameters, date and file of the point that
            # then wins, rather than those of the 0.90 point.
            curve = sorted(
                ({"recall": r["recall"], "qps": r["qps"],
                  "p99_ms": round(r["latency_p99_s"] * 1000, 2) if r["latency_p99_s"] else None,
                  "params": r["search_params"],
                  "measured": r["test_date"],
                  "peak_c": peak_concurrency(r),
                  "source_file": r["source_file"]}
                 for r in rs), key=lambda p: p["recall"])
            age = months_between(best["test_date"], today) if best["test_date"] else None
            flags = []
            # A sweep across a search parameter that returns the same recall at
            # every value did not reach the index. The number is whatever the
            # engine does without one, so it is marked rather than ranked as an
            # ANN result.
            recalls = {round(r["recall"], 4) for r in rs if r["recall"] is not None}
            if len(rs) > 2 and len(recalls) == 1:
                flags.append("search parameter had no effect")
            if not best["version"]:
                flags.append("no version recorded")
            if age is not None and age >= 12:
                flags.append(f"{age} months old")
            if best["quantization"] == "none (float32)":
                flags.append("no quantization")
            shown = names.get((best.get("run_meta") or {}).get("engine_id"), db)
            entries.append({
                "db": db,
                "series": label,
                "display": f"{shown} {label}".strip(),
                "qps": best["qps"],
                "recall": best["recall"],
                "met_recall_bar": best["recall"] >= MIN_RECALL,
                "p99_ms": round(best["latency_p99_s"] * 1000, 2) if best["latency_p99_s"] else None,
                "measured": best["test_date"],
                "age_months": age,
                "version": clean_version(best["version"]) or None,
                "version_raw": best["version"] or None,
                "config": configs.get((best.get("run_meta") or {}).get("engine_id")),
                "index": best["index"] or None,
                "quantization": best["quantization"],
                "search_params": best["search_params"],
                "build_params": best["build_params"],
                "load_s": best["load_duration_s"],
                "source_file": best["source_file"],
                "runs_in_series": len(rs),
                "curve": curve,
                "engine_id": (best.get("run_meta") or {}).get("engine_id"),
                "machine": best.get("machine") or {},
                "hw": machine_class(best.get("machine") or {}),
                "vm": (best.get("run_meta") or {}).get("vm_size"),
                "flags": flags,
                "ladder": {"c": best.get("conc_num_list") or [],
                           "q": best.get("conc_qps_list") or []},
            })

        # A rank only means something between rows that ran on the same
        # hardware, so a row outside the reference class takes no rank.
        inert = [e for e in entries if "search parameter had no effect" in e["flags"]]
        rankable = [e for e in entries
                    if e["hw"] == "same" and e["met_recall_bar"] and e not in inert]
        ranked = sorted(rankable, key=lambda e: -e["qps"])
        for i, e in enumerate(ranked, 1):
            e["rank"] = i
            e["group"] = "ranked"
        rest = [e for e in entries if e not in ranked]
        for e in rest:
            if e["hw"] == "undisclosed":
                e["group"] = "undisclosed_hardware"
            elif e["hw"] == "different":
                e["group"] = "other_hardware"
            else:
                e["group"] = "no_rank"
        order = {"no_rank": 0, "other_hardware": 1, "undisclosed_hardware": 2}
        unranked = sorted(rest, key=lambda e: (order[e["group"]], -(e["qps"] or 0)))
        sample = next(r for r in runs if r["case_id"] == cid)
        board[str(cid)] = {
            "case_id": cid,
            "case": sample["case"],
            "dataset": sample["dataset"],
            "rows": sample["dataset_rows"],
            "dim": sample["dim"],
            "entries": ranked + unranked,
        }

    out = {
        "generated": today.isoformat(),
        "min_recall": MIN_RECALL,
        "reference_machine": {"vcpu": REFERENCE_MACHINE[0], "ram_gb": REFERENCE_MACHINE[1]},
        "baseline_vm": BASELINE_VM,
        "cases": board,
    }
    path = os.path.join(datadir, "leaderboard_data.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)

    hl = board.get("5")
    print(f"wrote {path}")
    if hl:
        print(f"\n{hl['case']} - ranked at recall >= {MIN_RECALL}")
        for e in hl["entries"]:
            r = e.get("rank", "-")
            print(f"  {str(r):>3}. {e['display']:42} qps={e['qps']:>9.1f} "
                  f"recall={e['recall']:.4f} measured={e['measured']} "
                  f"v={e['version'] or '-':<10} quant={e['quantization']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
