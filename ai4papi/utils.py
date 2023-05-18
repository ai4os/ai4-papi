"""
Miscellaneous utils
"""


import nomad


def deregister_job(self, id, namespace=None, purge=None):
    """ ================================================================================
        This is a monkey-patch of the default function in the python-nomad module,
        that did not support `namespace` as a parameter of the function.

        Remove when PR is merged:

        ================================================================================

        Deregisters a job, and stops all allocations part of it.

        https://www.nomadproject.io/docs/http/job.html

        arguments:
            - id
            - purge (bool), optionally specifies whether the job should be
            stopped and purged immediately (`purge=True`) or deferred to the
            Nomad garbage collector (`purge=False`).

        returns: dict
        raises:
            - nomad.api.exceptions.BaseNomadException
            - nomad.api.exceptions.URLNotFoundNomadException
            - nomad.api.exceptions.InvalidParameters
    """
    print('*' * 36, f'namespace {namespace}')  #FIXME: remove
    params = {}
    if purge is not None:
        if not isinstance(purge, bool):
            raise nomad.api.exceptions.InvalidParameters("purge is invalid "
                    "(expected type %s but got %s)"%(type(bool()), type(purge)))
        params["purge"] = purge
    if namespace:
        params["namespace"] = namespace
    return self.request(id, params=params, method="delete").json()