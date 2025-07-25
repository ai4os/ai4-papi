/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-ai4life-${JOB_UUID}" {
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

  # Only use nodes that have successfully passed the ai4-nomad_tests (ie. meta.status=ready)
  constraint {
    attribute = "${meta.status}"
    operator  = "regexp"
    value     = "ready"
  }

  # Only launch in compute nodes (to avoid clashing with system jobs, eg. Traefik)
  constraint {
    attribute = "${meta.type}"
    operator  = "="
    value     = "compute"
  }

  # Avoid deploying in nodes that are reserved to batch
  constraint {
    attribute = "${meta.type}"
    operator  = "!="
    value     = "batch"
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

  # Avoid rescheduling the job if the job fails the first time
  # This is done to avoid confusing users with cyclic job statuses
  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {

    # Avoid rescheduling the job when the node fails:
    # * if the node is lost for good, you would need to manually redeploy,
    # * if the node is unavailable due to a network cut, you will recover the job (and
    #   your saved data) once the network comes back.
    # We don't want to increase the "lost_after" too much because otherwise we will fail
    # to identify clients that are failing due to network from clients that are truly down

    disconnect {
      lost_after = "48h"
      replace = false
      reconcile = "keep_original"  # in our case, this is redundant
    }

    network {

      port "api" {
        to = 5000  # -1 will assign random port
      }
      port "ui" {
        to = 80
      }
    }

    service {
      name = "${JOB_UUID}-api"
      port = "api"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-api.tls=true",
        "traefik.http.routers.${JOB_UUID}-api.rule=Host(`api-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.api-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-ui"
      port = "ui"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ui.tls=true",
        "traefik.http.routers.${JOB_UUID}-ui.rule=Host(`ui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.ui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "main" {

      driver = "docker"

      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        command    = "deep-start"
        args       = ["--deepaas"]
        ports      = ["api", "ui"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        storage_opt = {
          size = "${DISK}M"
        }

      }

      env {
        MODEL_NAME = "${AI4LIFE_MODEL}"
      }

      resources {
        cores      = ${CPU_NUM}
        memory     = ${RAM}
        memory_max = ${RAM}

        device "gpu" {
          count = ${GPU_NUM}

          # Add a constraint for a particular GPU model
          constraint {
            attribute = "${device.model}"
            operator  = "="
            value     = "${GPU_MODELNAME}"
          }

        }
      }
    }

  }
}
