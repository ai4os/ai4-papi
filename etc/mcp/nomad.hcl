/*
Self-deployed MCP package.

PAPI fills the uppercase placeholders before submitting the job. Nomad resolves
${meta.domain} after choosing a node, which lets Traefik build the final hostname.
The concrete package command and registry metadata are added to the parsed job by
MCPNomadClient so registry-provided strings are never interpolated into HCL.
*/

job "${JOB_ID}" {
  id         = "${JOB_ID}"
  namespace  = "${NAMESPACE}"
  type       = "service"
  region     = "global"
  priority   = "${PRIORITY}"

  meta {
    owner           = "pending"
    title           = "MCP server"
    description     = ""
    deployment_type = "self_deployed"
    base_domain     = "${BASE_DOMAIN}"
  }

  constraint {
    attribute = "${meta.status}"
    operator  = "regexp"
    value     = "ready"
  }

  constraint {
    attribute = "${meta.type}"
    operator  = "="
    value     = "compute"
  }

  constraint {
    attribute = "${meta.namespace}"
    operator  = "regexp"
    value     = "${NAMESPACE}"
  }

  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {
    disconnect {
      lost_after = "48h"
      replace = false
      reconcile = "keep_original"
    }

    network {
      port "mcp" {
        to = ${MCP_PORT}
      }
    }

    service {
      name = "${JOB_ID}"
      port = "mcp"

      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_ID}.tls=true",
        "traefik.http.routers.${JOB_ID}.rule=Host(`${JOB_ID}.${meta.domain}-${BASE_DOMAIN}`)",
        "traefik.http.routers.${JOB_ID}.middlewares=${JOB_ID}-auth",
        "traefik.http.middlewares.${JOB_ID}-auth.basicauth.users=papi:${BASIC_AUTH_HASH}",
        "traefik.http.middlewares.${JOB_ID}-auth.basicauth.removeheader=true",
      ]

      check {
        name     = "alive"
        type     = "tcp"
        interval = "10s"
        timeout  = "2s"
      }
    }

    ephemeral_disk {
      size = ${DISK_MB}
    }

    task "main" {
      driver = "docker"
      user   = "node"

      config {
        force_pull = true
        image      = "${NODE_IMAGE}"
        ports      = ["mcp"]
        command    = "npx"
        args       = ["placeholder"]
      }

      env {
        HOST = "0.0.0.0"
        PORT = "${MCP_PORT}"
      }

      resources {
        cores  = ${CPU_CORES}
        memory = ${MEMORY_MB}
      }
    }

    # The API only submits the job. This lifecycle task waits for the local MCP,
    # registers it in central LiteLLM and grants it through the owner's one group.
    task "register_mcp_in_litellm" {
      lifecycle {
        hook    = "poststart"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "python:3.12-slim"
        command    = "python"
        args       = ["local/register.py"]
      }

      env {
        LOCAL_MCP_URL  = "http://$${NOMAD_ADDR_mcp}${ENDPOINT_PATH}"
        PUBLIC_MCP_URL = "https://${JOB_ID}.$${meta.domain}-${BASE_DOMAIN}${ENDPOINT_PATH}"
      }

      template {
        data        = "placeholder"
        destination = "local/register.py"
        change_mode = "restart"
      }

      restart {
        attempts = 3
        interval = "10m"
        delay    = "10s"
        mode     = "fail"
      }

      resources {
        cpu    = 100
        memory = 128
      }
    }

    # Deregistration belongs to the deployment as well. Stopping the job removes
    # its server from the stable user group and then removes it from LiteLLM.
    task "deregister_mcp_from_litellm" {
      lifecycle {
        hook    = "poststop"
        sidecar = false
      }

      driver = "docker"

      config {
        force_pull = true
        image      = "python:3.12-slim"
        command    = "python"
        args       = ["local/deregister.py"]
      }

      template {
        data        = "placeholder"
        destination = "local/deregister.py"
        change_mode = "restart"
      }

      restart {
        attempts = 3
        interval = "10m"
        delay    = "10s"
        mode     = "fail"
      }

      resources {
        cpu    = 100
        memory = 128
      }
    }
  }
}
