"""
Shared configuration and tiny helpers of the energy-accounting feature.
"""

import datetime

from cachetools import TTLCache

import ai4papi.conf as papiconf
from ai4papi.clients import mimir
from ai4papi.energy import store

# Nomad job-name prefixes that are deployments (as opposed to e.g. traefik)
JOB_PREFIXES = ("module", "tool", "batch", "try")
KEYS = ("energy_wh", "carbon_g", "water_l")  # TUE-normalized totals kept per accum
EMPTY = dict.fromkeys(KEYS, 0.0)

# Pseudo-namespace for the datacenter-level documents. Does not collide with any
# VO namespace nor with the deployment sweep's namespace loop.
CLUSTER_NS = "_cluster"


def get() -> dict:
    """The `energy` section of the main config."""
    return papiconf.MAIN_CONF["energy"]


def enabled() -> bool:
    return (
        bool(get().get("enabled"))
        and store.base_dir() is not None
        and bool(mimir.MIMIR_USER)
    )


def topup_cache(maxsize: int) -> TTLCache:
    """Cache for the live path: shorter than the dashboard poll, so the counter moves."""
    return TTLCache(maxsize=maxsize, ttl=get().get("topup_cache_seconds", 25))


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def dt(ts: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def floor(ts: float, step: int) -> float:
    return (int(ts) // step) * step


def tue(dc: str) -> float:
    return (papiconf.datacenters.get(dc) or {}).get("TUE") or get()[
        "default_tue_factor"
    ]


def cluster_dq() -> dict:
    """
    Data-quality config for the datacenter sweep: reuse `energy.data_quality`
    but drop the absolute `max_power_w` ceiling (it is calibrated per allocation
    / per GPU, far below a whole host). Negative-drop and the scale-free spike
    check still apply, per host / per GPU series.
    """
    return {**(get().get("data_quality") or {}), "max_power_w": 0}


def sum_totals(buckets) -> dict:
    return {k: sum(b[k] for b in buckets) for k in KEYS}


def add_totals(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in KEYS}


def log_safe(value: str) -> str:
    """Strip CR/LF from an identifier that may come straight from a request
    path or auth token before it is interpolated into a log message, so it
    cannot be used to forge log lines (CWE-117)."""
    return str(value).replace("\r", "").replace("\n", "")
