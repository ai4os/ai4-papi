/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replaced by Nomad at launch time
* remaining values are the same for everybody

When replacing user values we use safe_substitute() so that we don't get an error for not
replacing Nomad values.
*/

job "tool-fedserver-llm-${JOB_UUID}" {
  namespace = "${NAMESPACE}"
  type      = "service"
  region    = "global"
  id        = "${JOB_UUID}"
  priority  = "${PRIORITY}"

  meta {
    owner       = "${OWNER}"
    owner_name  = "${OWNER_NAME}"
    owner_email = "${OWNER_EMAIL}"
    title       = "${TITLE}"
    description = "${DESCRIPTION}"
  }

  constraint {
    attribute = "${meta.status}"
    operator  = "regexp"
    value     = "ready"
  }

  constraint {
    attribute = "${meta.type}"
    operator  = "="
    value     = "compute"
  }

  constraint {
    attribute = "${meta.type}"
    operator  = "!="
    value     = "batch"
  }

  constraint {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "${NAMESPACE}"
  }

  affinity {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "ai4eosc"
    weight    = -100
  }

  affinity {
    attribute = "${meta.tags}"
    operator  = "regexp"
    value     = "cpu"
    weight    = 100
  }

  affinity {
    attribute = "${meta.tags}"
    operator  = "regexp"
    value     = "gpu"
    weight    = -100
  }

  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {
    disconnect {
      lost_after = "48h"
      replace = false
      reconcile = "keep_original"
    }

    network {
      port "fedserver" {
        to = 5000
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
        "traefik.http.services.${JOB_UUID}-fedserver.loadbalancer.server.scheme=h2c",
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

      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}"
        ports      = ["fedserver", "ide"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        storage_opt = {
          size = "${DISK}M"
        }
      }

      env {
        jupyterPASSWORD    = "${JUPYTER_PASSWORD}"
        CODE_CARBON        = "${CODE_CARBON}"
        NUM_ROUNDS         = "${NUM_ROUNDS}"
        MODEL_NAME         = "${MODEL_NAME}"
        MODEL_QUANTIZATION = "${MODEL_QUANTIZATION}"
        NUM_EPOCHS         = "${NUM_EPOCHS}"
        FRACTION_TRAIN     = "${FRACTION_TRAIN}"
        FRACTION_EVALUATE  = "${FRACTION_EVALUATE}"
      }

      resources {
        cores      = ${CPU_NUM}
        memory     = ${RAM}
        memory_max = ${RAM}
      }
    }
  }
}
