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
  type      = "service"
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
      // Task configured by the user

      # TODO: kill after 10 mins and do *not* restart

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

      resources {
        cores  = 1
        memory = 1000  # 1GB
        memory_max = 1000  # 1GB
      }
    }

  }
}
