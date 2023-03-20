job "userjob" {
  # todo: remove? do we need to hardcode the datacenter? Can it be found directly at runtime?  
  datacenters = ["dc1_dw_ifca"]
  type = "service"
  region = "global"

  meta {
    owner = "janedoe"
    description = ""
  }

  group "usergroup" {

    network {
        mode = "bridge"

        port "deepaas" {
            static = 5000
            to = 5000  # -1 will assign random port
        }
        port "monitor" {
            static = 6006
            to = 6006
        }
        port "lab" {
            static = 8888
            to = 8888
        }
    }

    service {
        # name = "userjob-service"
        port = "deepaas"

        connect {
            sidecar_service {}
        }
    }

    task "usertask" {
      driver = "docker"

      config {
        image = "deephdc/deep-oc-image-classification-tf:cpu"
        command = "deep-start"
        args = ["--deepaas"]
      }

      env {
        jupyterPASSWORD = ""
        RCLONE_CONFIG = ""
        RCLONE_CONFIG_RSHARE_TYPE = "webdav"
        RCLONE_CONFIG_RSHARE_URL = ""
        RCLONE_CONFIG_RSHARE_VENDOR = ""
        RCLONE_CONFIG_RSHARE_USER = ""
        RCLONE_CONFIG_RSHARE_PASS = ""
      }

      resources {
        # Minimum number of CPUs is 2
        cpu = 2
        memory = 8000
        disk = 20000
        device "nvidia/gpu" {
          count = 0

          # Add an affinity for a particular model
          affinity {
            attribute = "${device.model}"
            value = "Tesla K80"
            weight = 50
          }
        }
      }
    }
  }
}

