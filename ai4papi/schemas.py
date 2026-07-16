from pydantic import BaseModel
from fastapi import Query
from typing import Annotated, Any, Dict, Optional


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

    gpu_models: Dict[str, GpuModelStats]


class NodeInfo(ResourceStats):
    name: str
    eligibility: str
    namespaces: str
    type: str
    status: str
    tags: str


class DatacenterStats(BaseModel):
    # CSV metadata
    lat: float
    lon: float
    PUE: float

    # Dict mapping a node ID to its stats
    nodes: Dict[str, NodeInfo]

    # Optional fields (added dynamically)
    footprints: Optional[Dict[str, Any]] = None  # fromwattnet.GreenDirector
    affinity: Optional[float] = None  # Added in get_cluster_stats()


class ClusterStats(BaseModel):
    # Dict mapping Datacenter ID/Name to Datacenter metadata
    datacenters: Dict[str, DatacenterStats]

    # Overall aggregated metrics
    cluster: ResourceStats

    # Added in get_cluster_stats()
    updated_at: Optional[str] = None
