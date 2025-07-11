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
    #prevent_reschedule_on_lost = true

    disconnect {
      lost_after = "6h"
      replace = false
      reconcile = "keep_original"
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

      lifecycle {
        hook    = "prestart"
        sidecar = true
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "vllm/vllm-openai:latest"
        ports      = ["vllm"]
        args       = ${VLLM_ARGS}
      }

      env {
        HUGGING_FACE_HUB_TOKEN = "${HUGGINGFACE_TOKEN}"
        VLLM_API_KEY = "${API_TOKEN}"
      }

      resources {
        cores  = 4
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

    task "check_vllm_startup" {

      lifecycle {
        hook    = "prestart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "python:slim-bullseye"
        command    = "bash"
        args       = ["local/get_models.sh"]
      }

      env {
        VLLM_ENDPOINT = "https://vllm-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}/v1/models"
        TOKEN = "${API_TOKEN}"
      }

      template {
        data = <<-EOF
        #!/bin/bash

        pip install requests

        python -c '
        import requests
        import os
        import time

        VLLM_ENDPOINT = os.environ["VLLM_ENDPOINT"]
        TOKEN = os.environ["TOKEN"]

        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"
        }

        attempts = 0
        delay = 2

        with requests.Session() as session:
            session.headers.update(headers)

            while True:
                response = session.get(VLLM_ENDPOINT)
                if response.ok:
                    print(f"Success | Status code: {response.status_code}")
                    print(f"{response.text}")
                    exit(0)
                else:
                    attempts += 1
                    print(f"Attempt nº {attempts} | Status code: {response.status_code}")
                    time.sleep(delay)
        '
        EOF
        destination = "local/get_models.sh"
      }
    }

    task "open-webui" {

      driver = "docker"

      config {
        force_pull = true
        image      = "ghcr.io/open-webui/open-webui:main"
        ports      = ["ui"]
      }

      env {
        OPENAI_API_KEY      = "${API_TOKEN}"
        OPENAI_API_BASE_URL = "${API_ENDPOINT}"
        WEBUI_AUTH          = true
      }

      resources {  # UI needs a fair amount of resources because it's also doing RAG
        cores  = 4
        memory = 16000
     }
   }


    task "create-admin" {
      # Open WebUI does not allow to create admin from configuration, so we have to
      # to make an HTTP call to create it, in order not to leave the UI vulnerable

      lifecycle {
        hook    = "poststart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "python:slim-bullseye"
        command    = "bash"
        args       = ["local/create_admin.sh"]
      }

      env {
        OPEN_WEBUI_URL = "https://ui-${HOSTNAME}.${meta.domain}-${BASE_DOMAIN}"
        NAME           = "${OWNER_NAME}"
        EMAIL          = "${OPEN_WEBUI_USERNAME}"
        PASSWORD       = "${OPEN_WEBUI_PASSWORD}"
      }

      template {
        data = <<-EOF
        #!/bin/bash

        pip install requests

        python -c """
        import os
        import time

        import requests


        # Define the URL
        base_url = os.getenv('OPEN_WEBUI_URL')

        # Define the JSON data
        data = {
            'name': os.getenv('NAME'),
            'email': os.getenv('EMAIL'),
            'password': os.getenv('PASSWORD'),
            'profile_image_url': '/user.png'
        }

        # Make the POST request (we repeat it because Open WebUI can take some time to warm)
        while True:
            r = requests.post(f'{base_url}/api/v1/auths/signup', json=data)
            if not r.ok:
                print(f'Error: status code {r.status_code}')
                time.sleep(1)
            else:
                break

        print(f'Status Code: {r.status_code}')
        print(f'Response Content: {r.text}')
        """
        EOF
        destination = "local/create_admin.sh"
      }


    }

  }
}
