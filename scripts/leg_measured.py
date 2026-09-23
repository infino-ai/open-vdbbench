#!/usr/bin/env python3
"""Print how many points of a collected leg measured both throughput and recall.

  leg_measured.py <stamp> <engine>

Zero means the leg wrote result files and none of them holds a measurement,
which is the case where the machine's logs are worth keeping.
"""
import glob
import json
import os
import sys


def main(stamp, engine):
    n = 0
    for path in glob.glob(os.path.join("runs", stamp, engine, "result_*.json")):
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in doc.get("results") or []:
            m = r.get("metrics") or {}
            if (m.get("qps") or 0) > 0 and (m.get("recall") or 0) > 0:
                n += 1
    print(n)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: leg_measured.py <stamp> <engine>")
    main(sys.argv[1], sys.argv[2])
