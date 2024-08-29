/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "try-${JOB_UUID}" {
  namespace = "${NAMESPACE}"
  type      = "batch"       # try-me jobs should not be redeployed when exit_code=0
  region    = "global"
  id        = "${JOB_UUID}"
  priority  = "0"           # try-me jobs have low priority

  meta {
    owner       = "${OWNER}"  # user-id from OIDC
    owner_name  = "${OWNER_NAME}"
    owner_email = "${OWNER_EMAIL}"
    title       = ""
    description = ""
  }

  # Only use nodes that have succesfully passed the ai4-nomad_tests (ie. meta.status=ready)
  constraint {
    attribute = "${meta.status}"
    operator  = "regexp"
    value     = "ready"
  }

  # Only deploy in nodes serving that namespace (we use metadata instead of node-pools
  # because Nomad does not allow a node to belong to several node pools)
  constraint {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "${NAMESPACE}"
  }

  # Force that try-me jobs land in "tryme" nodes (that are the ones that have the docker
  # images pre-fetched for fast deployment)
  constraint {
    attribute = "${meta.tags}"
    operator  = "regexp"
    value     = "tryme"
  }

  group "usergroup" {

    # Do not try to restart a try-me job if it raised an error (eg. module incompatible
    # with Gradio UI)
    reschedule {
      attempts  = 0
      unlimited = false
    }

    network {

      port "ui" {
        to = 80  # -1 will assign random port
      }
      port "api" {
        to = 5000  # -1 will assign random port
      }
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
      size = 300  # MB
    }

    task "main" { # DEEPaaS API

      # Run as a prestart task to make sure deepaas has already launched when launching the deepaas UI
      lifecycle {
        hook    = "prestart"
        sidecar = true
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:latest"
        command    = "deep-start"
        args       = ["--deepaas"]
        ports      = ["api"]
        shm_size   = 1000000000  # 1GB
        memory_hard_limit = 2000  # 2GB
      }

      # (!) Keep in mind that if a module works locally but isn't working in Nomad,
      # the reason is likely that these resources are too low and the module freezes
      resources {
        cores      = 1
        memory     = 2000  # 2GB
        memory_max = 2000  # 2GB
      }

      # Do not try to restart a try-me job if it failis to launch deepaas
      # This is usually due to the fact that the Docker image took too long to download
      # and failed with error: `Failed to pull `ai4oshub/...`: context deadline` exceeded
      # Restarting in the same node won't fix the connectivity issues
      restart {
        attempts = 0
        mode     = "fail"
      }

    }

    task "ui" { # DEEPaaS UI (Gradio)

      driver = "docker"

      config {
        force_pull = true
        image      = "registry.services.ai4os.eu/ai4os/deepaas_ui:latest"
        ports      = ["ui"]
        shm_size   = 250000000   # 250MB
        memory_hard_limit = 500  # MB
      }

      env {
        DURATION = "10m"  # kill job after 10 mins
        UI_PORT  = 80
      }

      resources {
        cpu        = 500  # MHz
        memory     = 500  # MB
        memory_max = 500  # MB
      }

      # Do not try to restart a try-me job if it raises error (module incompatible with Gradio UI)
      restart {
        attempts = 0
        mode     = "fail"
      }

    }

  }
}
