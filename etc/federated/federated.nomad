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

      port "fedserver" {
        to = 5000
      }
      port "ide" {
        to = 8888
      }
    }

    service {
      name = "some-unique-name-fedserver"
      port = "fedserver"
      tags = [
        "traefik.enable=true",
        # add: "traefik.http.routers.some-unique-name-fedserver.rule=Host(`fedserver.some-unique-name.main-domain`)",
        # add: "traefik.http.services.some-unique-name-fedserver.loadbalancer.server.scheme=h2c",  # grpc support
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

      # Use default command defined in the Dockerfile
      config {
        image   = "deephdc/deep-oc-federated-server:latest"
        ports   = ["fedserver", "ide"]
      }

      env {
        jupyterPASSWORD       = ""
        FEDERATED_ROUNDS      = 2
        FEDERATED_METRIC      = "accuracy"
        FEDERATED_MIN_CLIENTS = 2
        FEDERATED_STRATEGY    = "Federated Averaging"
      }

      resources {
        # Minimum number of CPUs is 2
        cpu    = 2
        memory = 4000
        disk   = 1000
      }
    }
  }
}

