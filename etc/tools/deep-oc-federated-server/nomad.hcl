/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "userjob-${JOB_UUID}" {
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

  # CPU-only jobs should deploy *preferably* on CPU clients (affinity) to avoid
  # overloading GPU clients with CPU-only jobs.
  affinity {
    attribute = "${node.unique.name}"
    operator  = "regexp"
    value     = "gpu"
    weight    = -50  # anti-affinity for GPU clients
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
        "traefik.http.routers.${JOB_UUID}-fedserver.rule=Host(`fedserver-${DOMAIN}`, `www.fedserver-${DOMAIN}`)",
        "traefik.http.services.${JOB_UUID}-fedserver.loadbalancer.server.scheme=h2c",  # grpc support
      ]
    }

    service {
      name = "${JOB_UUID}-monitor"
      port = "monitor"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-monitor.tls=true",
        "traefik.http.routers.${JOB_UUID}-monitor.rule=Host(`monitor-${DOMAIN}`, `www.monitor-${DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-ide"
      port = "ide"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ide.tls=true",
        "traefik.http.routers.${JOB_UUID}-ide.rule=Host(`ide-${DOMAIN}`, `www.ide-${DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "usertask" {
      driver = "docker"

      # Use default command defined in the Dockerfile
      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        ports      = ["fedserver", "monitor", "ide"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
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
