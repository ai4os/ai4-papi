"""
Common utilities between modules and tool deployments.
"""

import ai4papi.routers.v1.stats.deployments as stats


def add_green_affinities(nomad_conf: dict, vo: str):
    """
    Add a job affinities for greener datacenters. We add one affinity per datacenter.
    """
    datacenters = stats.get_cluster_stats(vo)["datacenters"]
    for k, v in datacenters.items():
        # Rescale affinity range from [0, 100] to [0, 30] to avoid interfering too much
        # with other constraints/affinities.
        affinity = int(v["affinity"] * 0.3)

        # Nomad does not allows affinity weight equal to zero
        if affinity == 0:
            continue

        nomad_conf["Affinities"].append(
            {
                "LTarget": "${node.datacenter}",
                "Operand": "=",
                "RTarget": k,
                "Weight": affinity,
            }
        )

    return nomad_conf
