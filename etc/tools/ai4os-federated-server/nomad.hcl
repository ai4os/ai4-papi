/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-fl-${JOB_UUID}" {
  namespace = "${NAMESPACE}"
  type      = "service"
  region    = "global"
  id        = "${JOB_UUID}"
  priority  = "${PRIORITY}"

  meta {
    owner       = "${OWNER}"  # user-id from OIDC
    owner_name  = "${OWNER_NAME}"
    owner_email = "${OWNER_EMAIL}"
    title       = "${TITLE}"
    description = "${DESCRIPTION}"
  }

  # Only use nodes that have succesfully passed the ai4-nomad_tests (ie. meta.status=ready)
  constraint {
    attribute = "${meta.status}"
    operator  = "regexp"
    value     = "ready"
  }

  # Only launch in compute nodes (to avoid clashing with system jobs, eg. Traefik)
  constraint {
    attribute = "${meta.compute}"
    operator  = "="
    value     = "true"
  }

  # Only deploy in nodes serving that namespace (we use metadata instead of node-pools
  # because Nomad does not allow a node to belong to several node pools)
  constraint {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "${NAMESPACE}"
  }

  # Try to deploy iMagine jobs on nodes that are iMagine-exclusive
  # In this way, we leave AI4EOSC nodes for AI4EOSC users and for iMagine users only
  # when iMagine nodes are fully booked.
  affinity {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "ai4eosc"
    weight    = -100  # anti-affinity for ai4eosc clients
  }

  # CPU-only jobs should deploy *preferably* on CPU clients (affinity) to avoid
  # overloading GPU clients with CPU-only jobs.
  affinity {
    attribute = "${meta.tags}"
    operator  = "regexp"
    value     = "cpu"
    weight    = 100
  }

  # Avoid rescheduling the job on **other** nodes during a network cut
  # Command not working due to https://github.com/hashicorp/nomad/issues/16515
  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {

    # Recover the job in the **original** node when the network comes back
    # (after a network cut).
    # If network cut lasts more than 10 days (240 hrs), job is restarted anyways.
    # Do not increase too much this limit because we want to still be able to notice
    # when nodes are truly removed from the cluster (not just temporarily lost).
    max_client_disconnect = "240h"

    network {

      port "fedserver" {
        to = 5000
      }
      port "monitor" {
        to = 6006
      }
      port "ide" {
        to = 8888
      }
    }

    service {
      name = "${JOB_UUID}-fedserver"
      port = "fedserver"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-fedserver.tls=true",
        "traefik.http.routers.${JOB_UUID}-fedserver.rule=Host(`fedserver-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.fedserver-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
        "traefik.http.services.${JOB_UUID}-fedserver.loadbalancer.server.scheme=h2c",  # grpc support
      ]
    }

    service {
      name = "${JOB_UUID}-monitor"
      port = "monitor"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-monitor.tls=true",
        "traefik.http.routers.${JOB_UUID}-monitor.rule=Host(`monitor-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.monitor-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-ide"
      port = "ide"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ide.tls=true",
        "traefik.http.routers.${JOB_UUID}-ide.rule=Host(`ide-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.ide-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "main" {
      driver = "docker"

      # Use default command defined in the Dockerfile
      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        ports      = ["fedserver", "monitor", "ide"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        storage_opt = {
          size = "${DISK}M"
        }
      }

      env {
        VAULT_TOKEN                     = "${VAULT_TOKEN}"
        jupyterPASSWORD                 = "${JUPYTER_PASSWORD}"
        FEDERATED_ROUNDS                = "${FEDERATED_ROUNDS}"
        FEDERATED_METRIC                = "${FEDERATED_METRIC}"
        FEDERATED_MIN_FIT_CLIENTS       = "${FEDERATED_MIN_FIT_CLIENTS}"
        FEDERATED_MIN_AVAILABLE_CLIENTS = "${FEDERATED_MIN_AVAILABLE_CLIENTS}"
        FEDERATED_STRATEGY              = "${FEDERATED_STRATEGY}"
        MU_FEDPROX                      = "${MU_FEDPROX}"
        FEDAVGM_SERVER_FL               = "${FEDAVGM_SERVER_FL}"
        FEDAVGM_SERVER_MOMENTUM         = "${FEDAVGM_SERVER_MOMENTUM}"
      }

      resources {
        cores  = ${CPU_NUM}
        memory = ${RAM}
        memory_max = ${RAM}
      }
    }
  }
}
