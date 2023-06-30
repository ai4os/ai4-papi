# Based on: https://developer.hashicorp.com/nomad/tutorials/load-balancing/load-balancing-traefik

job "userjob" {
  namespace = "default"
  type      = "service"
  region    = "global"

  # TODO: remove when ready
  # Issue: Traefik is not listing jobs that are deployed on a datacenter different than
  # the one Traefik is deployed in. So for the time being we have to force the
  # datacenter until this is resolved.
  # datacenters = ["AI4EOSC-IFCA"]

  meta {
    owner       = ""  # user-id from OIDC
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
      name = "some-unique-name-deepaas"
      port = "deepaas"
      tags = [
        "traefik.enable=true",
        # add: "traefik.http.routers.some-unique-name-deepaas.rule=Host(`deepaas.some-unique-name.main-domain`)",
      ]
    }

    service {
      name = "some-unique-name-monitor"
      port = "monitor"
      tags = [
        "traefik.enable=true",
        # add: "traefik.http.routers.some-unique-name-monitor.rule=Host(`monitor.some-unique-name.main-domain`)",
      ]
    }

    service {
      name = "some-unique-name-ide"
      port = "ide"
      tags = [
        "traefik.enable=true",
        # add: "traefik.http.routers.some-unique-name-ide.rule=Host(`ide.some-unique-name.main-domain`)",
      ]
    }

    task "usertask" {
      driver = "docker"

      config {
        image   = "deephdc/deep-oc-image-classification-tf:cpu"
        command = "deep-start"
        args    = ["--deepaas"]
        ports   = ["deepaas", "monitor", "ide"]
      }

      env {
        jupyterPASSWORD             = ""
        RCLONE_CONFIG               = ""
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = ""
        RCLONE_CONFIG_RSHARE_VENDOR = ""
        RCLONE_CONFIG_RSHARE_USER   = ""
        RCLONE_CONFIG_RSHARE_PASS   = ""
      }

      resources {
        # Minimum number of CPUs is 2
        cpu    = 2
        memory = 8000
        disk   = 20000

        device "gpu" {
          count = 0

          # Add an affinity for a particular model
          affinity {
            attribute = "${device.model}"
            value     = ""  # GPU model name
            weight    = 50
          }
        }
      }
    }
  }
}

