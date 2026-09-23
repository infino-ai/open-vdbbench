"""The shape of a result row, shared by collect_runs.py and build_leaderboard_data.py.

VectorDBBench's case ids, the configuration keys that carry compression, search
and build parameters, and the machine a row ran on.
"""
import re

CASES = {
    1: ("Capacity Test (128 Dim Repeated)", "SIFT", 500_000, 128),
    2: ("Capacity Test (960 Dim Repeated)", "GIST", 100_000, 960),
    3: ("Search Performance Test (100M Dataset, 768 Dim)", "LAION", 100_000_000, 768),
    4: ("Search Performance Test (10M Dataset, 768 Dim)", "Cohere", 10_000_000, 768),
    5: ("Search Performance Test (1M Dataset, 768 Dim)", "Cohere", 1_000_000, 768),
    6: ("Filtering Search Performance Test (10M Dataset, 768 Dim, Filter 1%)", "Cohere", 10_000_000, 768),
    7: ("Filtering Search Performance Test (1M Dataset, 768 Dim, Filter 1%)", "Cohere", 1_000_000, 768),
    8: ("Filtering Search Performance Test (10M Dataset, 768 Dim, Filter 99%)", "Cohere", 10_000_000, 768),
    9: ("Filtering Search Performance Test (1M Dataset, 768 Dim, Filter 99%)", "Cohere", 1_000_000, 768),
    10: ("Search Performance Test (500K Dataset, 1536 Dim)", "OpenAI", 500_000, 1536),
    11: ("Search Performance Test (5M Dataset, 1536 Dim)", "OpenAI", 5_000_000, 1536),
    12: ("Filtering Search Performance Test (500K Dataset, 1536 Dim, Filter 1%)", "OpenAI", 500_000, 1536),
    13: ("Filtering Search Performance Test (5M Dataset, 1536 Dim, Filter 1%)", "OpenAI", 5_000_000, 1536),
    14: ("Filtering Search Performance Test (500K Dataset, 1536 Dim, Filter 99%)", "OpenAI", 500_000, 1536),
    15: ("Filtering Search Performance Test (5M Dataset, 1536 Dim, Filter 99%)", "OpenAI", 5_000_000, 1536),
    17: ("Search Performance Test (1M Dataset, 1024 Dim)", "BioASQ", 1_000_000, 1024),
    20: ("Search Performance Test (10M Dataset, 1024 Dim)", "BioASQ", 10_000_000, 1024),
    50: ("Search Performance Test (50K Dataset, 1536 Dim)", "OpenAI", 50_000, 1536),
}

# Per-engine keys that carry the quantization or compression decision.
QUANT_KEYS = (
    "sq_type", "quantization_type", "quantizationType", "use_quant",
    "element_type", "compression", "encoder", "pq", "bq", "rabitq",
    "refine", "refine_type", "refine_k", "use_rescore", "oversample_ratio",
    "rescore_multiplier", "quantization_ratio",
)
SEARCH_KEYS = (
    "ef", "num_candidates", "ef_search", "efSearch", "nprobe", "search_list_size",
    "search_width", "reorder_size", "query_ef", "probes", "n_probes",
)
BUILD_KEYS = ("M", "m", "efConstruction", "ef_construction", "nlist", "num_neighbors",
              "max_degree", "build_list_size", "index_type", "index")


def pick(d, keys):
    return {k: d[k] for k in keys if k in d and d[k] not in (None, "", [], {})}


# The class a rank is awarded in: 16 vCPU, 64 GB.
REFERENCE_MACHINE = (16, 64)

# The one machine the board's rows are measured on. Standard_D16as_v7 is the
# same 16 vCPU and 64 GB with a network disk in place of local NVMe, so rows
# measured on it are not published beside these.
BASELINE_VM = "Standard_D16ads_v7"

# Instance types the harness runs, and what they are.
VM_SIZES = {
    "Standard_D16as_v7": (16, 64),
    "Standard_D16s_v5": (16, 64),
    "Standard_D16ads_v7": (16, 64),
    "n2-standard-16": (16, 64),
    "m6i.4xlarge": (16, 64),
}


def machine_of(label, vm_size=None):
    """What hardware a row ran on, from its VM size or a "16c64g" style label."""
    if vm_size and vm_size in VM_SIZES:
        v, g = VM_SIZES[vm_size]
        return {"vcpu": v, "ram_gb": g, "raw": vm_size, "disclosed": True}
    raw = str(label or "")
    m = re.search(r"(\d+(?:\.\d+)?)c(\d+)g", raw)
    if m:
        v = float(m.group(1))
        return {"vcpu": int(v) if v.is_integer() else v,
                "ram_gb": int(m.group(2)), "raw": raw, "disclosed": True}
    return {"vcpu": None, "ram_gb": None, "raw": raw, "disclosed": False}


def machine_class(mach):
    """same, different, or undisclosed, against the reference class."""
    if not mach.get("disclosed"):
        return "undisclosed"
    return "same" if (mach["vcpu"], mach["ram_gb"]) == REFERENCE_MACHINE else "different"


def quant_summary(cfg):
    """Describe the compression a result file says the run used.

    Three distinct answers:
      "not disclosed"  the config names no index and no compression
      "none (float32)" the config explicitly specifies full-precision vectors
      anything else    the compression that was configured
    """
    q = []
    # Clients name this field differently: sq_type, quantization_type,
    # quantizationType, or a plain quantization. Missing one reports a
    # compressed run as full precision.
    sq = (cfg.get("sq_type") or cfg.get("quantization_type")
          or cfg.get("quantizationType") or cfg.get("quantization"))
    # "vector" is the uncompressed storage type, so it names no compression.
    if sq and str(sq).lower() not in ("none", "null", "false", "vector"):
        q.append(str(sq))
    tq = cfg.get("table_quantization_type")
    if tq and str(tq).lower() not in ("none", "null", "false") and str(tq) != str(sq):
        q.append(f"table={tq}")
    et = cfg.get("element_type")
    if et and et != "float":
        q.append(str(et))
    if cfg.get("use_quant") is True:
        q.append("quantized")
    idx = str(cfg.get("index") or cfg.get("index_type") or "")
    # vchordrq is RaBitQ by construction; its operator class names the stored
    # type, so the quantization does not appear in the configuration at all.
    if idx.lower() == "vchordrq":
        q.insert(0, "RaBitQ")
    for tag in ("SQ", "PQ", "BQ", "RABITQ", "bbq", "int8", "int4", "halfvec", "bit"):
        if tag.lower() in idx.lower() and tag not in " ".join(q):
            q.append(tag)
    # pgvectorscale names its compression after the storage layout: the
    # memory_optimized layout is statistical binary quantization.
    if cfg.get("storage_layout") == "memory_optimized":
        q.append("SBQ")
    if cfg.get("residual_quantization") is True and "residual" not in " ".join(q):
        q.append("residual")
    if cfg.get("refine") and cfg.get("refine_type"):
        q.append(f"refine={cfg['refine_type']}")
    if cfg.get("query_rescore"):
        q.append(f"rescore={cfg['query_rescore']}")
    if cfg.get("use_rescore"):
        q.append("rescore")
    if q:
        return "+".join(q)

    # An auto-index leaves the compression to the engine, so the config
    # cannot tell you whether the run was quantized.
    if idx.lower().startswith("auto"):
        return "not disclosed (auto-index)"

    # Nothing compressed. Is that a stated choice or an undisclosed one?
    declares_index = bool(idx) or any(
        k in cfg for k in BUILD_KEYS if k not in ("index", "index_type"))
    declares_float = cfg.get("element_type") == "float" or cfg.get("data_type") == "float32"
    if declares_index or declares_float:
        return "none (float32)"
    return "not disclosed"
