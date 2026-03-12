"""
Common utilities between modules and tool deployments.
"""

import ai4papi.routers.v1.stats.deployments as stats


def add_green_affinities(nomad_conf: dict, vo: str):
    """
    Add a job affinity for greener datacenters.

    We map linearly a carbon footprint into a datacenter Nomad affinity.
    It's an inverse relation:
    * Maximum carbon footprint should have minimum affinity (e.g. 0)
    * Minimum carbon footprint should have maximum affinity (e.g. 20)

    We keep the maximum affinity well below the theoretical maximum (100) to not
    interfere too much with other constraints/affinities.

    Carbon footprint is computed as (energy quality * PUE).
    """
    datacenters = stats.get_cluster_stats(vo)["datacenters"]

    # Affinity range
    af_min, af_max = 0, 30
    # Footprint range
    footprints = [i["energy_quality"] * i["PUE"] for i in datacenters.values()]
    fp_min, fp_max = min(footprints), max(footprints)

    # Inverse linear mapping
    affinities = {}
    for name, dc in datacenters.items():
        if fp_min != fp_max:
            x = dc["energy_quality"] * dc["PUE"]
            affinities[name] = round(
                af_max + (af_min - af_max) * (x - fp_min) / (fp_max - fp_min)
            )
        else:  # avoid dividing by zero
            affinities[name] = 0

    # Add one affinity per datacenter
    for k, v in affinities.items():
        if v == 0:  # Nomad does not allows affinity weight equal to zero
            continue

        nomad_conf["Affinities"].append(
            {"LTarget": "${node.datacenter}", "Operand": "=", "RTarget": k, "Weight": v}
        )

    return nomad_conf
