# Based on: https://developer.hashicorp.com/nomad/tutorials/load-balancing/load-balancing-traefik

job "userjob" {
  namespace = "default"
  type      = "service"
  region    = "global"

  meta {
    owner       = ""  # user-id from OIDC
    title       = ""
    description = ""
  }

  # TODO: remove when ready
  # We are temporarily deploying only to the "host-ifca-4" client because Traefik,
  # which is deployed in that client, seems only able to redirect only to services
  # which are deployed in the same client (although it is able to _list_ services
  # from other clients).
  # And because this is a CPU client, we have disable temporarily the creation of
  # GPU deployments (see `deployments.create_deployment()` and `user_conf.yaml`)
  constraint {
    attribute = "${node.unique.name}"
    operator  = "="
    value     = "host-ifca-4"
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

