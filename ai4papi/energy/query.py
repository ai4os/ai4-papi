"""
Mimir power queries shared by the sweep (write path) and the live top-up.
"""

from ai4papi.clients import mimir
from ai4papi.energy import config


def fetch_power(
    cpu_q: str, gpu_q: str, start_ts: float, end_ts: float, step_s: int
) -> tuple[list, list]:
    """
    `(cpu_result, gpu_result)` range queries over [start, end]. A missing metric
    comes back as an empty result, not an error (a GPU-only site has no `scaph_*`
    series), so a query that fails is a real failure and propagates: the sweep
    skips that deployment and retries on the next pass, the live path reports
    `degraded`. Long windows are split in chunks.
    """
    start, end = config.dt(start_ts), config.dt(end_ts)
    chunked = end_ts - start_ts > mimir.MAX_POINTS_PER_QUERY * step_s
    qfn = mimir.query_range_chunked if chunked else mimir.query_range
    return qfn(cpu_q, start, end, step_s), qfn(gpu_q, start, end, step_s)
