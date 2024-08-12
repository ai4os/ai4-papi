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
  namespace = "ai4eosc"     # try-me jobs are always deployed in ai4eosc
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

  # CPU-only jobs should deploy *preferably* on CPU clients (affinity) to avoid
  # overloading GPU clients with CPU-only jobs.
  affinity {
    attribute = "${node.unique.name}"
    operator  = "regexp"
    value     = "gpu"
    weight    = -50  # anti-affinity for GPU clients
  }
  #TODO: *force* CPU for try-me deployments.
  # Wait until we move to federated cluster because this will be easier to implement.

  group "usergroup" {

    # Do not try to restart a try-me job if it raised an error (eg. module incompatible with Gradio UI)
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
        "traefik.http.routers.${JOB_UUID}-ui.rule=Host(`ui-${DOMAIN}`, `www.ui-${DOMAIN}`)",
      ]
    }
    #TODO: adapt for federated cluster

    ephemeral_disk {
      size = 300  # MB
    }

    task "usertask" {
      # Task configured by the user

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
        shm_size   = 500000000  # 500MB
        memory_hard_limit = 1000  # 1GB
      }

      resources {
        cores      = 1
        memory     = 1000  # 1GB
        memory_max = 1000  # 1GB
      }

    }

    task "ui" {
      # DEEPaaS UI

      driver = "docker"

      config {
        force_pull = true
        image      = "registry.services.ai4os.eu/ai4os/deepaas_ui"
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
