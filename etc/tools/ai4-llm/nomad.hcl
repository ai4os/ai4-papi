/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

/*
Main changes with respect to the reference job located in [1].

- added preliminary constraints and affinites
- adapted meta field
- group renamed to 'user_group'
- $$ replaced with $$$$ to avoid escaping in Python Template [2]
- replace ${BASE} with ${JOB_UUID}
- renamed task "server" to "main" (to share same info retrieving pattern)

I also had to replace the following meta fields, otherwise when retrieving the
job info the ${env_var} where not being replaced. I'm having to do something similar
with ${meta.domain} but I don't want to extend it to env_vars just to support CVAT.

To avoid too much disruption, I'm only changing this inside the service field
- ${NOMAD_META_job_uuid} --> ${JOB_UUID}
- ${NOMAD_META_cvat_hostname} --> ${meta.domain}-${BASE_DOMAIN}

To avoid too much disruption, I'm only changing this in the "main" task (parameter `image`)
- ${NOMAD_META_server_image} --> registry.services.ai4os.eu/ai4os/ai4-cvat-server:v2.7.3-AI4OS

[1]: https://github.com/ai4os/ai4os-cvat/blob/v2.7.3-AI4OS/nomad/ai4-cvat.jobspec.nomad.hcl
[2]: https://stackoverflow.com/a/56957750/18471590

Note:
In several part of the job we use the old name of the repo (ai4os/ai4-cvat) which
should redirect fine to the new repo name (ai4os/ai4os-cvat)
But it is important nevertheless to keep it in mind, just in case.

*/


job "vllm-${JOB_UUID}" {
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

  # Only use nodes that have succesfully passed the ai4-nomad_tests (ie. meta.status=ready)
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
    weight    = -50  # anti-affinity for ai4eosc clients
  }

  # CPU-only jobs should deploy *preferably* on CPU clients (affinity) to avoid
  # overloading GPU clients with CPU-only jobs.
  affinity {
    attribute = "${meta.tags}"
    operator  = "regexp"
    value     = "cpu"
    weight    = 50
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

    ephemeral_disk {
      size = 4096
    }

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
            "traefik.http.routers.${JOB_UUID}-ui.rule=Host(`openwebui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`, `www.openwebui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}`)",
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
    
    task "open-webui" {

      driver = "docker"

      config {
        image   = "ghcr.io/open-webui/open-webui:main"
        ports   = ["ui"]
  			# args    = ["--restart", "always"] 
        volumes    = ["open-webui:/app/backend/data"]
      }

      env {
        OPENAI_API_KEY  = "EMPTY"
        OPENAI_API_BASE_URL = "https://vllm-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}/v1"
        WEBUI_AUTH  = false
      } 

      resources {

        memory = 8000

     }
   }

    task "vllm" {

      driver = "docker"

      config {
        image   = "vllm/vllm-openai:latest"
        ports   = ["vllm"]
        args    = ["${VLLM_ARGS}"] # For V100 GPUs
        volumes    = ["~/.cache/huggingface:/root/.cache/huggingface"]
      }

      env {
        HUGGING_FACE_HUB_TOKEN  = "${HUGGINGFACE_TOKEN}"
      } 

      resources {

        memory = 16000


        device "gpu" {
          count = 1

          # Add a constraint for a particular GPU model
          constraint {
            attribute = "${device.model}"
            operator  = "="
            value     = "Tesla V100-PCIE-32GB"
          }

        }
     }

   }
  }
}
