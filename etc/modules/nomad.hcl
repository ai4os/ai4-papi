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

  group "usergroup" {

    network {

      port "api" {
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
      name = "${JOB_UUID}-api"
      port = "api"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-api.tls=true",
        "traefik.http.routers.${JOB_UUID}-api.rule=Host(`api-${DOMAIN}`, `www.api-${DOMAIN}`)",
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

    task "storagetask" {
      // Running task in charge of mounting storage

      driver = "docker"

      config {
        image   = "ignacioheredia/ai4-docker-storage"
        privileged = true
        volumes = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
      }

      env {
        RCLONE_CONFIG               = "${RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${RCLONE_CONFIG_RSHARE_PASS}"
        REMOTE_PATH                 = "rshare:/"
        LOCAL_PATH                  = "/storage"
      }

      resources {
        cpu    = 2         # Minimum number of CPUs is 2
        memory = 2000
        // disk   = 1000   # TODO: CHECK THIS
      }
    }

    task "usertask" {
      // Task configured by the user (deepaas, jupyter, vscode)

      driver = "docker"

      config {
        image   = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        command = "deep-start"
        args    = ["--${SERVICE}"]
        ports   = ["api", "monitor", "ide"]
        volumes = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
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

    task "storagecleanup" {
      // Unmount empty storage folder and delete it from host

      lifecycle {
        hook = "poststop"
      }

      driver = "raw_exec"

      config {
        command = "/bin/bash"
        args = ["-c", "sudo umount /nomad-storage/${JOB_UUID} && sudo rmdir /nomad-storage/${JOB_UUID}" ]

      }
    }
  }
}

