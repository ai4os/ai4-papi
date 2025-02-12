/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-llm-${JOB_UUID}" {
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

  # Avoid rescheduling the job on **other** nodes during a network cut
  # Command not working due to https://github.com/hashicorp/nomad/issues/16515
  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {

    # Avoid rescheduling the job when the node fails:
    # * if the node is lost for good, you would need to manually redeploy,
    # * if the node is unavailable due to a network cut, you will recover the job (and
    #   your saved data) once the network comes back.
    prevent_reschedule_on_lost = true

    network {

        port "ui" {
          to = 8080
        }
        port "vllm" {
          to = 8000
        }
    }

    service {
        name = "${JOB_UUID}-ui"
        port = "ui"
      	tags = [
            "traefik.enable=true",
            "traefik.http.routers.${JOB_UUID}-ui.tls=true",
            "traefik.http.routers.${JOB_UUID}-ui.rule=Host(`ui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.ui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
          ]
    }

    service {
        name = "${JOB_UUID}-vllm"
        port = "vllm"
      	tags = [
            "traefik.enable=true",
            "traefik.http.routers.${JOB_UUID}-vllm.tls=true",
            "traefik.http.routers.${JOB_UUID}-vllm.rule=Host(`vllm-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.vllm-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
          ]
    }

    ephemeral_disk {
      size = 4096
    }

    task "vllm" {

      driver = "docker"

      config {
        image = "vllm/vllm-openai:latest"
        ports = ["vllm"]
        args  = ${VLLM_ARGS}
      }

      env {
        HUGGING_FACE_HUB_TOKEN = "${HUGGINGFACE_TOKEN}"
        VLLM_API_KEY = "${API_TOKEN}"
      }

      resources {
        cores  = 8
        memory = 16000

        device "gpu" {
          count = 1

          # Add a constraint for a particular GPU model
          constraint {
            attribute = "${device.model}"
            operator  = "="
            value     = "Tesla T4"
          }

        }
      }

    }

    task "open-webui" {

      driver = "docker"

      config {
        image = "ghcr.io/open-webui/open-webui:main"
        ports = ["ui"]
      }

      env {
        OPENAI_API_KEY      = "${API_TOKEN}"
        OPENAI_API_BASE_URL = "${API_ENDPOINT}"
        WEBUI_AUTH          = true
      }

      resources {  # UI needs a fair amount of resources because it's also doing RAG
        cores  = 4
        memory = 8000
     }
   }

  }
}
