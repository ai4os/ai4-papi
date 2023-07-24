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

  group "usergroup" {

    network {

      port "deepaas" {
        to = 5000  # -1 will assign random port
      }
      port "monitor" {
        to = 6006
      }
      port "ide" {
        to = 8888
      }
    }

    service {
      name = "${JOB_UUID}-deepaas"
      port = "deepaas"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-deepaas.rule=Host(`deepaas.${DOMAIN}`, `www.deepaas.${DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-monitor"
      port = "monitor"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-monitor.rule=Host(`monitor.${DOMAIN}`, `www.monitor.${DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-ide"
      port = "ide"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ide.rule=Host(`ide.${DOMAIN}`, `www.ide.${DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "usertask" {
      driver = "docker"

      config {
        image   = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        command = "deep-start"
        args    = ["--${SERVICE}"]
        ports   = ["deepaas", "monitor", "ide"]
      }

      env {
        jupyterPASSWORD             = "${JUPYTER_PASSWORD}"
        RCLONE_CONFIG               = "${RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${RCLONE_CONFIG_RSHARE_PASS}"
      }

      resources {
        # Minimum number of CPUs is 2
        cpu    = ${CPU_NUM}
        memory = ${RAM}
        // disk   = ${DISK}  # TODO: CHECK THIS

        device "gpu" {
          count = ${GPU_NUM}

          # Add an affinity for a particular model
          affinity {
            attribute = "${device.model}"
            value     = "${GPU_MODELNAME}"
            weight    = 50
          }
        }
      }
    }
  }
}

