/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-devenv-${JOB_UUID}" {
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
      port "monitor" {
        to = 6006
      }
      port "ide" {
        to = 8888
      }
      port "custom" {
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

    service {
      name = "${JOB_UUID}-custom"
      port = "custom"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-custom.tls=true",
        "traefik.http.routers.${JOB_UUID}-custom.rule=Host(`custom-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.custom-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "storage_mount" {
      // Running task in charge of mounting storage

      lifecycle {
        hook    = "prestart"
        sidecar = true
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "registry.cloud.ai4eosc.eu/ai4os/docker-storage:latest"
        privileged = true
        volumes    = [
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

    task "dataset_download" {
      // Download a dataset to the Nextcloud-mounted storage

      lifecycle {
        hook    = "prestart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "registry.cloud.ai4eosc.eu/ai4os/docker-zenodo:latest"
        volumes    = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
      }

      env {
        DOI = "${DATASET_DOI}"
        FORCE_PULL = "${DATASET_FORCE_PULL}"
      }

      resources {
        cpu    = 50
        memory = 2000
      }

    }

    task "email-notification" {
      lifecycle {
        hook    = "prestart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image = "registry.cloud.ai4eosc.eu/ai4os/docker-mail:client"
      }

      env {
        NUM_DAYS="7"  # if the job takes more than this to deploy, then we notify the users
        DATE="${TODAY}"  # when the job was created by the user
        MAILING_TOKEN="${MAILING_TOKEN}"
        DEST="${OWNER_EMAIL}"
        SUBJECT="[AI4EOSC Support] Your job is ready! 🚀️"
        BODY="Dear ${OWNER_NAME}, \n\nyour deployment \"${TITLE}\", created on ${TODAY}, is now ready to use. \nYou can access it at the ${PROJECT_NAME} Dashboard. \nRemember to delete the deployment in case you no longer need it! \n\nRegards, \n\n[The AI4EOSC Support Team]"
      }

      resources {
        cores  = 1
      }

      restart {
        attempts = 0
        mode     = "fail"
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
        ports      = ["api", "monitor", "ide", "custom"]
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
        MLFLOW_TRACKING_URI         = "${MLFLOW_URI}"
        MLFLOW_TRACKING_USERNAME    = "${MLFLOW_USERNAME}"
        MLFLOW_TRACKING_PASSWORD    = "${MLFLOW_PASSWORD}"
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
