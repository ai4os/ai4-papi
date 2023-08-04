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

      port "fedserver" {
        to = 5000
      }
      port "ide" {
        to = 8888
      }
    }

    service {
      name = "${JOB_UUID}-fedserver"
      port = "fedserver"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-fedserver.tls=true",
        "traefik.http.routers.${JOB_UUID}-fedserver.rule=Host(`fedserver-${DOMAIN}`, `www.fedserver-${DOMAIN}`)",
        "traefik.http.services.${JOB_UUID}-fedserver.loadbalancer.server.scheme=h2c",  # grpc support
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

    task "usertask" {
      driver = "docker"

      # Use default command defined in the Dockerfile
      config {
        image   = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        ports   = ["fedserver", "ide"]
      }

      env {
        jupyterPASSWORD       = "${JUPYTER_PASSWORD}"
        FEDERATED_ROUNDS      = "${FEDERATED_ROUNDS}"
        FEDERATED_METRIC      = "${FEDERATED_METRIC}"
        FEDERATED_MIN_CLIENTS = "${FEDERATED_MIN_CLIENTS}"
        FEDERATED_STRATEGY    = "${FEDERATED_STRATEGY}"
      }

      resources {
        # Minimum number of CPUs is 2
        cpu    = ${CPU_NUM}
        memory = ${RAM}
        // disk   = ${DISK}  # TODO: CHECK THIS
      }
    }
  }
}

