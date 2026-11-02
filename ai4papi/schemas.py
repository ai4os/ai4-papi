from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel

TagList = Annotated[tuple | None, Query()]
VoList = Annotated[list[str] | None, Query()]


class ResourceStats(BaseModel):
    # Tracked Resources
    jobs_num: int = 0
    reallocations: int = 0
    cpu_total: int = 0
    cpu_used: int = 0
    gpu_total: int = 0
    gpu_used: int = 0
    ram_total: float = 0.0
    ram_used: float = 0.0
    disk_total: float = 0.0
    disk_used: float = 0.0

    # Nested Dict mapping a GPU model (e.g., "NVIDIA GeForce RTX 3090") to its specific stats
    class GpuModelStats(BaseModel):
        gpu_total: int = 0
        gpu_used: int = 0

    gpu_models: dict[str, GpuModelStats]


class NodeInfo(ResourceStats):
    name: str
    eligibility: str
    namespaces: str
    type: str
    status: str
    tags: str
    cpu_model: str


class EnergyTimeSeriesPoint(BaseModel):
    # Per-bucket increments only. A running total is `cumsum(series[].energy_wh)`
    # and matches `accumulated.energy_wh` (same for carbon and water).
    ts: str
    power_w: float | None = None  # mean over the window, normalized (x TUE)
    energy_wh: float | None = None  # normalized, incremental for the window
    carbon_g: float | None = None
    water_l: float | None = None
    live: bool = False  # point from the live tail (not yet persisted)


class EnergyStats(BaseModel):
    """
    Energy consumption and footprint (carbon and water, scope 2) of one entity,
    always with the live Mimir top-up applied. The same shape at every level:
    `energy` per deployment (list endpoints), `accumulated` in the per-deployment
    time series, `energy` per datacenter in `GET /stats/cluster`, and `energy`
    per user in `GET /stats/user`.

    Every value is normalized by the datacenter TUE; `tue_factor` is reported so a
    client that wants the raw meter figure divides by it (the raw is not sent).
    `coverage_ratio` / `datacenters` are `null` on the datacenter block;
    `deployments` is set only on the per-user aggregate.
    """

    energy_wh: float | None = None  # accumulated, normalized (x TUE)
    tue_factor: float | None = None  # normalized / raw (per-datacenter or blended)
    carbon_g: float | None = None  # gCO2eq
    water_l: float | None = None  # L
    power_w: float | None = (
        None  # current draw (last Mimir sample, ~30-60s old); None if stopped
    )
    since: str | None = None  # start of the accumulation window
    as_of: str | None = None  # store pointer timestamp
    live_as_of: str | None = None  # instant of the live top-up
    complete: bool = True  # False if it started late / clamped by Mimir retention
    degraded: bool = False  # the live Mimir top-up failed
    coverage_ratio: float | None = None  # per deployment / user: fraction with data
    datacenters: list[str] | None = None  # per deployment / user
    deployments: int | None = None  # per-user aggregate only
    series: list[EnergyTimeSeriesPoint] | None = (
        None  # equal time windows; only with full_info=true
    )


class EnergyTimeSeries(BaseModel):
    deployment_uuid: str
    start: str  # `accumulated.since`
    end: str  # live edge while running (`accumulated.live_as_of`)
    source: str  # "store" (archive only) | "mixed" (archive + live tail)
    series: list[EnergyTimeSeriesPoint]
    accumulated: EnergyStats


class DatacenterStats(BaseModel):
    # CSV metadata
    lat: float
    lon: float
    PUE: float

    # Dict mapping a node ID to its stats
    nodes: dict[str, NodeInfo]

    # Optional fields (added dynamically)
    footprints: dict[str, Any] | None = None  # from wattnet.GreenDirector
    energy: EnergyStats | None = None  # current power + accumulated energy/footprint


class ClusterStats(BaseModel):
    # Dict mapping Datacenter ID/Name to Datacenter metadata
    datacenters: dict[str, DatacenterStats]

    # Overall aggregated metrics
    cluster: ResourceStats

    # Added in get_cluster_stats()
    updated_at: str | None = None

    # Energy is reported per datacenter (`datacenters[<dc>].energy`); there is no
    # platform-wide total, clients sum the datacenters they care about.
