"""
Miscellaneous Nomad patches
#TODO: remove when new nomad-python release is launched.
"""

from typing import Union

import nomad


Nomad = nomad.Nomad()


def deregister_job(
    self,
    id_: str,
    eval_priority: Union[int, None] = None,
    global_: Union[bool, None] = None,
    namespace: Union[str, None] = None,
    purge: Union[bool, None] = None,
):
    """================================================================================
    This is a monkey-patch of the default function in the python-nomad module,
    that did not support `namespace` as a parameter of the function.

    Remove when PR is merged:
        https://github.com/jrxFive/python-nomad/pull/153

    ================================================================================

    Deregisters a job, and stops all allocations part of it.

    https://www.nomadproject.io/docs/http/job.html

    arguments:
        - id
        - eval_priority (int) optional.
        Override the priority of the evaluations produced as a result
        of this job deregistration. By default, this is set to the
        priority of the job.
        - global (bool) optional.
        Stop a multi-region job in all its regions. By default, job
        stop will stop only a single region at a time. Ignored for
        single-region jobs.
        - purge (bool) optional.
        Specifies that the job should be stopped and purged immediately.
        This means the job will not be queryable after being stopped.
        If not set, the job will be purged by the garbage collector.
        - namespace (str) optional.
        Specifies the target namespace. If ACL is enabled, this value
        must match a namespace that the token is allowed to access.
        This is specified as a query string parameter.

    returns: dict
    raises:
        - nomad.api.exceptions.BaseNomadException
        - nomad.api.exceptions.URLNotFoundNomadException
        - nomad.api.exceptions.InvalidParameters
    """
    params = {
        "eval_priority": eval_priority,
        "global": global_,
        "namespace": namespace,
        "purge": purge,
    }
    return self.request(id_, params=params, method="delete").json()


def get_allocations(
    self,
    id_: str,
    all_: Union[bool, None] = None,
    namespace: Union[str, None] = None,
):
    """Query the allocations belonging to a single job.

    https://www.nomadproject.io/docs/http/job.html

    arguments:
        - id_
        - all (bool optional)
        - namespace (str) optional.
        Specifies the target namespace. If ACL is enabled, this value
        must match a namespace that the token is allowed to access.
        This is specified as a query string parameter.
    returns: list
    raises:
        - nomad.api.exceptions.BaseNomadException
        - nomad.api.exceptions.URLNotFoundNomadException
    """
    params = {
        "all": all_,
        "namespace": namespace,
    }
    return self.request(id_, "allocations", params=params, method="get").json()


def get_evaluations(
    self,
    id_: str,
    namespace: Union[str, None] = None,
):
    """Query the evaluations belonging to a single job.

    https://www.nomadproject.io/docs/http/job.html

    arguments:
        - id_
        - namespace (str) optional.
        Specifies the target namespace. If ACL is enabled, this value
        must match a namespace that the token is allowed to access.
        This is specified as a query string parameter.
    returns: dict
    raises:
        - nomad.api.exceptions.BaseNomadException
        - nomad.api.exceptions.URLNotFoundNomadException
    """
    params = {
        "namespace": namespace,
    }
    return self.request(id_, "evaluations", params=params, method="get").json()
