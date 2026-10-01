/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-fedserver-simulation-${JOB_UUID}" {
  namespace = "${NAMESPACE}"
  type      = "service"
  region    = "global"
  id        = "${JOB_UUID}"
  priority  = "${PRIORITY}"

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
      port "ide" {
        to = 8888
      }
    }

    service {
      name = "${JOB_UUID}-ide"
      port = "ide"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ide.tls=true",
        "traefik.http.routers.${JOB_UUID}-ide.rule=Host(`ide-${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    restart {
      attempts = 0
      mode = "fail"
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
        image      = "ai4os/docker-storage:latest"
        privileged = true
        volumes    = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
      }

      resources {
        cpu    = 50        # minimum number of CPU MHz is 2
        memory = 2000
      }
    }

    task "dataset_download" {
      // Download the dataset to the Nextcloud-mounted storage.

      lifecycle {
        hook    = "prestart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "ai4os/docker-zenodo:latest"
        volumes    = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
      }

      resources {
        cpu    = 50
        memory = 2000
      }

    }

    task "main" {
      // The image runs the service and manages the simulated clients.

      driver = "docker"

      config {
        force_pull = true
        image      = "configured-by-papi"
        ports      = ["ide"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        volumes    = [
          "/nomad-storage/${JOB_UUID}:/storage:shared",
        ]
        storage_opt = {
          size = "${DISK}M"
        }
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
            value     = "configured-by-papi"
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
