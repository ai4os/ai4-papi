/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "module-${JOB_UUID}" {
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
    job_type    = "module"
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
        "traefik.http.routers.${JOB_UUID}-api.rule=Host(`api-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.api-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
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

    task "storage_mount" {
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
        cpu    = 50        # minimum number of CPU MHz is 2
        memory = 2000
      }
    }

    task "main" {
      // Task configured by the user (deepaas, jupyter, vscode)

      driver = "docker"

      config {
        force_pull = true
        image      = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        command    = "deep-start"
        args       = ["--${SERVICE}"]
        ports      = ["api", "monitor", "ide"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        volumes    = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
        storage_opt = {
          size = "${DISK}M"
        }
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
        cores  = ${CPU_NUM}
        memory = ${RAM}
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

    task "storage_cleanup" {
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
