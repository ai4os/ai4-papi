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

  # Try-me jobs have no owner
  meta {
    owner       = ""
    owner_name  = ""
    owner_email = ""
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

  # Do not try to restart a try-me job if it raised an error (eg. module incompatible with Gradio UI)
  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {

    network {

      port "ui" {
        to = 8888  # -1 will assign random port
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

    ephemeral_disk {
      size = 300  # MB
    }

    task "usertask" {
      # Task configured by the user

      driver = "docker"

      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:latest"
        command    = "sh"
        args       = ["-c", "curl https://raw.githubusercontent.com/ai4os/deepaas_ui/nomad/nomad.sh | bash"]
        ports      = ["ui"]
        shm_size   = 500000000  # 500MB
        memory_hard_limit = 1000  # 1GB
      }

      env {
        DURATION = "10m"  # try-me job killed after 10 mins (with exit_code=0)
        UI_PORT  = 8888
      }

      resources {
        cores      = 1
        memory     = 1000  # 1GB
        memory_max = 1000  # 1GB
      }

      # Do not try to restart a try-me job if it raised an error (eg. module incompatible with Gradio UI)
      restart {
        attempts = 0
        mode     = "fail"
      }

    }
  }
}
