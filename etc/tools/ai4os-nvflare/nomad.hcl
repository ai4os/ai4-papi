/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "tool-nvflare-${JOB_UUID}" {
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

      port "dashboard" {
        to = 80
      }
      port "server-fl" {
        to = 8002
      }
      port "server-admin" {
        to = 8003
      }
      port "server-jupyter" {
        to = 8888
      }
    }

    service {
      name = "${JOB_UUID}-dashboard"
      port = "dashboard"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-dashboard.tls=true",
        "traefik.http.routers.${JOB_UUID}-dashboard.entrypoints=websecure",
        "traefik.http.routers.${JOB_UUID}-dashboard.rule=Host(`${JOB_UUID}-dashboard.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-server-fl"
      port = "server-fl"
      tags = [
        "traefik.enable=true",
        "traefik.tcp.routers.${JOB_UUID}-server-fl.tls=true",
        "traefik.tcp.routers.${JOB_UUID}-server-fl.tls.passthrough=true",  #TODO: check Stefan
        "traefik.tcp.routers.${JOB_UUID}-server-fl.entrypoints=nvflare_fl", #TODO: check Stefan
        "traefik.tcp.routers.${JOB_UUID}-server-fl.rule=HostSNI(`${JOB_UUID}-server.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-server-admin"
      port = "server-admin"
      tags = [
        "traefik.enable=true",
        "traefik.tcp.routers.${JOB_UUID}-server-admin.tls=true",
        "traefik.tcp.routers.${JOB_UUID}-server-admin.tls.passthrough=true",
        "traefik.tcp.routers.${JOB_UUID}-server-admin.entrypoints=nvflare_admin",
        "traefik.tcp.routers.${JOB_UUID}-server-admin.rule=HostSNI(`${JOB_UUID}-server.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    service {
      name = "${JOB_UUID}-server-jupyter"
      port = "server-jupyter"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-server-jupyter.tls=true",
        "traefik.http.routers.${JOB_UUID}-server-jupyter.entrypoints=websecure",
        "traefik.http.routers.${JOB_UUID}-server-jupyter.rule=Host(`${JOB_UUID}-server.${meta.domain}-${BASE_DOMAIN}`)",
      ]
    }

    ephemeral_disk {
      size = ${DISK}
    }

    task "dashboard" {

      driver = "docker"

      config {
        image      = "registry.services.ai4os.eu/ai4os/ai4os-nvflare-dashboard:${NVFL_VERSION}"
        force_pull = true
        ports      = ["dashboard"]
      }

      env {
        NVFL_CREDENTIAL="${NVFL_USERNAME}:${NVFL_PASSWORD}"
        NVFL_SERVER1="${NVFL_SERVER1}"
        NVFL_HA_MODE="False"
        NVFL_OVERSEER=""
        NVFL_SERVER2=""
        NVFL_PROJECT_SHORT_NAME="${NVFL_SHORTNAME}"
        NVFL_PROJECT_TITLE="${TITLE}"
        NVFL_PROJECT_DESCRIPTION="${DESCRIPTION}"
        NVFL_PROJECT_APP_LOCATION="${NVFL_APP_LOCATION}"
        NVFL_PROJECT_STARTING_DATE="${NVFL_STARTING_DATE}"
        NVFL_PROJECT_END_DATE="${NVFL_END_DATE}"
        NVFL_PROJECT_PUBLIC=${NVFL_PUBLIC_PROJECT}
        NVFL_PROJECT_FROZEN=true
        VARIABLE_NAME="app"
      }
    }

    task "main" {

      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }

      template {
        data = <<-EOF
        #!/bin/bash
        PIN='123456'
        sleep_time=10
        retries=10
        fl_server_dir=''
        while [[ $retries > 0 ]]; do
          # 1) login to the dashboard
          resp=$( \
            curl \
              -X POST \
              -L \
              -H 'Content-type: application/json' \
              -d '{"email":"'${NVFL_USERNAME}'", "password": "'${NVFL_PASSWORD}'"}' \
              https://${JOB_UUID}-dashboard.$${NOMAD_META_meta_domain}-${BASE_DOMAIN}/api/v1/login \
          )
          status=$(jq -r ".status" <<<"$resp")
          if [ "$status" != "ok" ]; then
            echo -e "resp: $resp"
            retries=$((retries-1))
            echo "retrying in ${sleep_time}s ..."
            sleep ${sleep_time}
            continue
          fi
          access_token=$(jq -r ".access_token" <<<"$resp")
          # 2) download server startup kit (primary)
          resp=$(\
            curl \
              -X POST \
              -L \
              -O \
              -J \
              -H 'Authorization: Bearer '$access_token \
              -H 'Content-type: application/json' \
              -d '{"pin":"'$PIN'"}' \
              https://${JOB_UUID}-dashboard.$${NOMAD_META_meta_domain}-${BASE_DOMAIN}/api/v1/servers/1/blob \
          )
          filename=$(echo -n "$resp" | sed -En 's/^.+?filename\s+\x27([^\x27]+)\x27.*$/\1/p')
          if [ ! -f $filename ]; then
            echo "file not found: $filename"
            retries=$((retries-1))
            echo "retrying in ${sleep_time}s ..."
            sleep ${sleep_time}
            continue
          fi
          # 3) unzip server startup kit
          echo "filename: $filename"
          unzip -P $PIN $filename
          fl_server_dir=$(echo -n "$filename" | sed -En 's/^(.+)\.zip$/\1/p')
          if [ ! -d $fl_server_dir ]; then
            echo "directory not found: $fl_servet_dir"
            echo "retrying in ${sleep_time}s ..."
            sleep ${sleep_time}
            continue
          fi
          retries=0
        done
        if [ -d $fl_server_dir ]; then
          # 4) start the FL server
          cd $fl_server_dir/startup
          ./start.sh
        else
          echo "failed to start the FL server"
        fi
        EOF
        destination = "local/init_fl_server.sh"
        perms = "750"
      }

      template {
        data = <<-EOF
        #!/bin/bash
        ./init_fl_server.sh
        jupyter-lab \
          --ServerApp.password=`python3 -c "from jupyter_server.auth import passwd; print(passwd('${NVFL_PASSWORD}'))"` \
          --port=8888 \
          --ip=0.0.0.0 \
          --notebook-dir=/tf \
          --no-browser \
          --allow-root
        EOF
        destination = "local/entrypoint.sh"
        perms = "750"
      }

      driver = "docker"

      config {
        image      = "registry.services.ai4os.eu/ai4os/ai4os-nvflare-server:${NVFL_VERSION}"
        command    = "/bin/bash"
        args       = [ "-c", "/workspace/entrypoint.sh"]
        force_pull = true
        ports      = ["server-fl", "server-admin", "server-jupyter"]
        shm_size   = ${SHARED_MEMORY}
        memory_hard_limit = ${RAM}
        storage_opt = {
          size = "${DISK}M"
        }

        mount {
          type = "bind"
          target = "/workspace/init_fl_server.sh"
          source = "local/init_fl_server.sh"
          readonly = true
        }
        mount {
          type = "bind"
          target = "/workspace/entrypoint.sh"
          source = "local/entrypoint.sh"
          readonly = true
        }
      }

      resources {
        cores      = ${CPU_NUM}
        memory     = ${RAM}
        memory_max = ${RAM}
      }

    }
  }
}
