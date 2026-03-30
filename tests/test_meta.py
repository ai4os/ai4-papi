"""
Test that PAPI is correctly configured
"""

# Check that the GPU models defined in the /var CSV file match those present in
# the cluster

from ai4papi.nomad.common import get_gpu_models
from ai4papi.utils import gpu_specs

cluster_models = get_gpu_models()
papi_models = gpu_specs().keys()
diff = set(cluster_models) - set(papi_models)
if diff:
    raise Exception(
        "The following GPU models present in the Nomad cluster have not been defined "
        f"in PAPI's gpu_models.csv:\n{diff}"
    )

print("🟢 Meta tests passed!")
