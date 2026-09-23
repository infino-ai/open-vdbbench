#!/usr/bin/env python3
"""Check every flag in harness/matrix.json against the client's real CLI.

A typo here costs a whole VM-hour before it fails, so it is checked before the
run, not during it.

  python3 scripts/check_matrix.py path/to/VectorDBBench

This reads the client sources, so it catches typos, flags a client never
declares, and options a command marks required that the matrix leaves out. It
cannot tell which shared options a given command actually composes in, so it is
a cheap first pass; `azure_fleet.py verify` runs the real CLI with the real
arguments and is the check that decides.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:", re.M)
CMD_RE = re.compile(r"@cli\.command\(([^)]*)\)(.*?)def\s+(\w+)", re.S)


def opts(src):
    """Every option name a click decorator declares in this source.

    Boolean options are declared as one string holding both names,
    "--ssl/--no-ssl", so each half is pulled out separately.
    """
    found = set()
    for quoted in re.findall(r'"(--[^"]*)"', src):
        for part in quoted.split("/"):
            part = part.strip()
            if re.fullmatch(r"--[a-z0-9\-]+", part):
                found.add(part)
    return found


def class_table(sources):
    """Map each TypedDict to its bases and the options it marks required.

    Commands compose their options by inheriting TypedDicts, so a required
    option can be declared three classes away from the command that needs it.
    """
    table = {}
    for src in sources:
        marks = [(m.start(), m.group(1), m.group(2)) for m in CLASS_RE.finditer(src)]
        for i, (pos, name, bases) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
            body = src[pos:end]
            required = set()
            # Splitting on the call leaves one chunk per option, holding that
            # option's own arguments and nothing from the next one.
            for chunk in body.split("click.option(")[1:]:
                nm = re.search(r'"(--[a-z0-9\-]+)"', chunk)
                if nm and "required=True" in chunk:
                    required.add(nm.group(1))
            base_names = [b.strip() for b in bases.split(",") if b.strip()]
            table[name] = (base_names, required)
    return table


def required_opts(name, table, seen=None):
    seen = set() if seen is None else seen
    if name in seen or name not in table:
        return set()
    seen.add(name)
    bases, required = table[name]
    out = set(required)
    for b in bases:
        out |= required_opts(b, table, seen)
    return out


def main(upstream):
    cli = os.path.join(upstream, "vectordb_bench", "cli", "cli.py")
    clients = os.path.join(upstream, "vectordb_bench", "backend", "clients")
    if not os.path.isfile(cli):
        sys.exit(f"not a VectorDBBench checkout: {upstream}")

    shared_src = open(cli).read()
    shared = opts(shared_src)
    commands = {}
    for d in os.listdir(clients):
        f = os.path.join(clients, d, "cli.py")
        if not os.path.isfile(f):
            continue
        src = open(f).read()
        for m in CMD_RE.finditer(src):
            nm = re.search(r'name\s*=\s*"([^"]+)"', m.group(1))
            td = re.search(r"click_parameter_decorators_from_typed_dict\((\w+)\)", m.group(2))
            key = nm.group(1) if nm else m.group(3).lower()
            commands[key] = (d, td.group(1) if td else None)

    matrix = json.load(open(os.path.join(ROOT, "harness", "matrix.json")))
    fails = 0
    for e in matrix["engines"]:
        # A client that only exists on a fork branch cannot be checked against
        # this checkout. The leg records the ref it installed either way.
        if e.get("vdb_ref"):
            print(f"skip {e['id']} (installs {e.get('vdb_repo','')}@{e['vdb_ref']})")
            continue
        problems = []
        if e["cmd"] not in commands:
            problems.append(f"no such vectordbbench command: {e['cmd']}")
        elif commands[e["cmd"]][0] != e["client_dir"]:
            problems.append(f"cmd {e['cmd']} lives in {commands[e['cmd']][0]}, "
                            f"matrix says {e['client_dir']}")
        cpath = os.path.join(clients, e["client_dir"], "cli.py")
        csrc = open(cpath).read() if os.path.isfile(cpath) else ""
        allowed = shared | opts(csrc)
        used = [a for a in e["args"] if a.startswith("--")]
        if e.get("ef_flag"):
            used.append(e["ef_flag"])
        unknown = [f for f in used if f not in allowed]
        if unknown:
            problems.append(f"flags the client does not accept: {unknown}")

        # An option the command marks required and the matrix omits fails after
        # the engine is up and the dataset is loaded, which is the expensive
        # place to find out.
        td = commands.get(e["cmd"], (None, None))[1]
        if td:
            table = class_table([shared_src, csrc])
            missing = sorted(required_opts(td, table) - set(used))
            if missing:
                problems.append(f"{e['cmd']} requires options the matrix omits: {missing}")

        # A click option declared type=bool needs a value. Written as a bare
        # flag it takes the next option as that value, which the client rejects
        # only once the leg is already running.
        args = [str(a) for a in e["args"]]
        for i, a in enumerate(args):
            if not a.startswith("--"):
                continue
            decl = re.search(r'"' + re.escape(a) + r'"[^)]*?type=(\w+)', csrc, re.S)
            if decl and decl.group(1) == "bool":
                nxt = args[i + 1] if i + 1 < len(args) else None
                if nxt is None or nxt.startswith("--"):
                    problems.append(f"{a} takes a boolean value and was given "
                                    f"{nxt or '(nothing)'}")
        if e.get("ef_sweep") and not e.get("ef_flag"):
            problems.append("ef_sweep set with no ef_flag")

        eng = os.path.join(ROOT, "harness", "scripts", "engines.sh")
        fn = e["id"].replace("-", "_")
        shsrc = open(eng).read()
        for pre in ("up_", "version_", "down_"):
            if f"\n{pre}{fn}()" not in shsrc and f"{pre}{fn}()" not in shsrc:
                problems.append(f"engines.sh has no {pre}{fn}()")

        print(("FAIL " if problems else "ok   ") + e["id"])
        for p in problems:
            print(f"       {p}")
        fails += bool(problems)

    print(f"\n{len(matrix['engines'])} engines, {fails} with problems")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "upstream"))
