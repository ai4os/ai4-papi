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

Do not use: image = "${NOMAD_META_cvat_server_image}" for the "main" task (i.e. cvat_server),
otherwise the Dashboard will show that (unreplaced) variable in the Docker image field. To avoid disruption,
exact values for all the image specifications is used instead of Nomad metadata.

[1]: https://github.com/ai4os/ai4os-cvat/blob/v2.7.3-AI4OS/nomad/ai4-cvat.jobspec.nomad.hcl
[2]: https://stackoverflow.com/a/56957750/18471590

*/


job "tool-cvat-${JOB_UUID}" {
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

    # CVAT-specific metadata
    force_pull_img_cvat_server         = true
    force_pull_img_cvat_ui             = true
    restore_from                       = "${RESTORE_FROM}"
    backup_name                        = "${BACKUP_NAME}"
    cvat_allow_static_cache            = "no"
    cvat_su_username                   = "${CVAT_USERNAME}"
    cvat_su_password                   = "${CVAT_PASSWORD}"

    smokescreen_opts                   = ""

    clickhouse_db                      = "cvat"
    clickhouse_user                    = "user"
    clickhouse_password                = "user"

    RCLONE_CONFIG                      = "${RCLONE_CONFIG}"
    RCLONE_CONFIG_RSHARE_TYPE          = "webdav"
    RCLONE_CONFIG_RSHARE_URL           = "${RCLONE_CONFIG_RSHARE_URL}"
    RCLONE_CONFIG_RSHARE_VENDOR        = "${RCLONE_CONFIG_RSHARE_VENDOR}"
    RCLONE_CONFIG_RSHARE_USER          = "${RCLONE_CONFIG_RSHARE_USER}"
    RCLONE_CONFIG_RSHARE_PASS          = "${RCLONE_CONFIG_RSHARE_PASS}"

    # remote path common for CVAT instances, without trailing /
    RCLONE_REMOTE_PATH                 = "/ai4os-storage/tools/cvat"
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

  # Avoid rescheduling the job if the job fails the first time
  # This is done to avoid confusing users with cyclic job statuses
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

    ephemeral_disk {
      size = 32768
    }

    network {
      port "ui" {
        to = 80
      }
      port "server" {
        to = 8080
      }
      port "utils" {
        to = 8080
      }
      port "worker_import" {
        to = 8080
      }
      port "worker_export" {
        to = 8080
      }
      port "worker_annotation" {
        to = 8080
      }
      port "worker_webhooks" {
        to = 8080
      }
      port "worker_quality_reports" {
        to = 8080
      }
      port "worker_analytics_reports" {
        to = 8080
      }
      port "worker_chunks" {
        to = 8080
      }
      port "opa" {
        to = 8181
      }
      port "grafana" {
        to = 3000
      }
      port "db" {
        to = 5432
      }
      port "redis_inmem" {
        to = 6379
      }
      port "redis_ondisk" {
        to = 6666
      }
      port "clickhouse_http" {
        to = 8123
      }
      port "vector" {
        to = 80
      }
    }

    service {
      name = "${JOB_UUID}-ui"
      port = "ui"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-ui.tls=true",
        "traefik.http.routers.${JOB_UUID}-ui.entrypoints=websecure",
        "traefik.http.routers.${JOB_UUID}-ui.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`)"
      ]
    }

    service {
      name = "${JOB_UUID}-server"
      port = "server"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-server.tls=true",
        "traefik.http.routers.${JOB_UUID}-server.entrypoints=websecure",
        "traefik.http.routers.${JOB_UUID}-server.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`) && PathPrefix(`/api/`, `/static/`, `/admin`, `/documentation/`, `/django-rq`)"
      ]
    }

    service {
      name = "${JOB_UUID}-grafana"
      port = "grafana"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${JOB_UUID}-grafana.tls=true",
        "traefik.http.routers.${JOB_UUID}-grafana.entrypoints=websecure",
        "traefik.http.routers.${JOB_UUID}-grafana.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`) && PathPrefix(`/analytics`)",
        "traefik.http.middlewares.${JOB_UUID}-grafana-analytics-auth.forwardauth.address=http://${NOMAD_HOST_ADDR_server}/analytics",
        "traefik.http.middlewares.${JOB_UUID}-grafana-analytics-auth.forwardauth.authRequestHeaders=Cookie,Authorization",
        "traefik.http.middlewares.${JOB_UUID}-grafana-analytics-strip-prefix.stripprefix.prefixes=/analytics",
        "traefik.http.routers.${JOB_UUID}-grafana.middlewares=${JOB_UUID}-grafana-analytics-auth@consulcatalog,${JOB_UUID}-grafana-analytics-strip-prefix@consulcatalog",
        "traefik.services.${JOB_UUID}-grafana.loadbalancer.servers.url=${NOMAD_HOST_ADDR_grafana}",
        "traefik.services.${JOB_UUID}-grafana.loadbalancer.passHostHeader=false"
      ]
    }

    task "share" {
      lifecycle {
        hook = "prestart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 500
        memory = 4096
      }
      env {
        RCLONE_CONFIG               = "${NOMAD_META_RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${NOMAD_META_RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${NOMAD_META_RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}"
        REMOTE_PATH                 = "rshare:${NOMAD_META_RCLONE_REMOTE_PATH}"
        LOCAL_PATH                  = "/mnt"
      }
      config {
        force_pull = true
        image      = "registry.services.ai4os.eu/ai4os/docker-storage:latest"
        privileged = true
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/share:/mnt/share:rshared"
        ]
        mount {
          type = "bind"
          target = "/srv/.rclone/rclone.conf"
          source = "local/rclone.conf"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/entrypoint.sh"
          source = "local/entrypoint.sh"
          readonly = false
        }
        entrypoint = [
          "/bin/bash",
          "-c",
          "chmod +x /entrypoint.sh; /entrypoint.sh"
        ]
      }
      template {
        data = <<-EOF
        [ai4eosc-share]
        type = webdav
        url = https://share.services.ai4os.eu/remote.php/dav
        vendor = nextcloud
        user = ${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}
        pass = ${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}
        EOF
        destination = "local/rclone.conf"
      }
      template {
        data = <<-EOF
        #!/usr/bin/env bash
        export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $$RCLONE_CONFIG_RSHARE_PASS)
        rm -rf $LOCAL_PATH/share
        mkdir -p $LOCAL_PATH/share
        rclone mkdir $REMOTE_PATH/share
        chown 1000:1000 $LOCAL_PATH/share
        chmod 750 $LOCAL_PATH/share
        rclone --log-level INFO mount $REMOTE_PATH/share $LOCAL_PATH/share \
          --uid 1000 \
          --gid 1000 \
          --dir-perms 0750 \
          --allow-non-empty \
          --allow-other \
          --vfs-cache-mode full
        EOF
        destination = "local/entrypoint.sh"
      }
    }

    task "synclocal" {
      lifecycle {
        hook = "prestart"
        sidecar = "false"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 500
        memory = 4096
      }
      env {
        RCLONE_CONFIG               = "${NOMAD_META_RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${NOMAD_META_RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${NOMAD_META_RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}"
        REMOTE_PATH                 = "rshare:${NOMAD_META_RCLONE_REMOTE_PATH}/backups"
        LOCAL_PATH                  = "/alloc/data"
        RESTORE_FROM                = "${NOMAD_META_restore_from}"
      }
      config {
        force_pull = true
        image   = "registry.services.ai4os.eu/ai4os/docker-storage:latest"
        mount {
          type = "bind"
          target = "/srv/.rclone/rclone.conf"
          source = "local/rclone.conf"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/sync_local.sh"
          source = "local/sync_local.sh"
          readonly = false
        }
        entrypoint = [
          "/bin/bash",
          "-c",
          "chmod +x /sync_local.sh; /sync_local.sh"
        ]
      }
      template {
        data = <<-EOF
        [ai4eosc-share]
        type = webdav
        url = https://share.services.ai4os.eu/remote.php/dav
        vendor = nextcloud
        user = ${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}
        pass = ${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}
        EOF
        destination = "local/rclone.conf"
      }
      template {
        data = <<-EOF
        #!/usr/bin/env bash
        tarbals='cache_db data db events inmem_db keys logs'
        export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $$RCLONE_CONFIG_RSHARE_PASS)
        for tarbal in $tarbals; do
          rm -rf $LOCAL_PATH/$tarbal
          mkdir -p $LOCAL_PATH/$tarbal
          if [[ $tarbal == "data" || $tarbal == "keys" || $tarbal == "logs"  ]]; then
            chown -R 1000 $LOCAL_PATH/$tarbal
            chgrp -R 1000 $LOCAL_PATH/$tarbal
            chmod -R 750 $LOCAL_PATH/$tarbal
          fi
        done
        if [ -z "$${RESTORE_FROM}" ]; then
          echo "CVAT backup not specified, a clean start will be performed"
        elif [[ $(rclone lsd $REMOTE_PATH/$$RESTORE_FROM; echo $?) == 0 ]]; then
          echo "found a CVAT backup '$$RESTORE_FROM', syncing ..."
          rm -rf $LOCAL_PATH/$$RESTORE_FROM
          mkdir -p $LOCAL_PATH/$$RESTORE_FROM
          rclone sync $REMOTE_PATH/$$RESTORE_FROM $LOCAL_PATH/$$RESTORE_FROM --progress
          for tarbal in $tarbals; do
            if [ -f $LOCAL_PATH/$$RESTORE_FROM/$tarbal.tar.gz ]; then
              echo -n "extracting $tarbal.tar.gz ... "
              cd $LOCAL_PATH/$tarbal && tar -xf $LOCAL_PATH/$$RESTORE_FROM/$tarbal.tar.gz --strip 1
              if [[ $? == 0 ]]; then echo "OK"; else echo "ERROR"; fi
            else
              echo "file not found: $LOCAL_PATH/$$RESTORE_FROM/$tarbal.tar.gz"
            fi
          done
        else
          echo "CVAT backup '$$RESTORE_FROM' not found, a clean start will be performed"
        fi
        EOF
        destination = "local/sync_local.sh"
      }
    }

    task "syncremote" {
      lifecycle {
        hook = "poststop"
        sidecar = "false"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 500
        memory = 4096
      }
      env {
        RCLONE_CONFIG               = "${NOMAD_META_RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${NOMAD_META_RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${NOMAD_META_RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}"
        REMOTE_PATH                 = "rshare:${NOMAD_META_RCLONE_REMOTE_PATH}/backups"
        LOCAL_PATH                  = "/alloc/data"
        BACKUP_NAME                 = "${NOMAD_META_backup_name}"
      }
      config {
        force_pull = true
        image   = "registry.services.ai4os.eu/ai4os/docker-storage:latest"
        mount {
          type = "bind"
          target = "/srv/.rclone/rclone.conf"
          source = "local/rclone.conf"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/sync_remote.sh"
          source = "local/sync_remote.sh"
          readonly = false
        }
        entrypoint = [
          "/bin/bash",
          "-c",
          "chmod +x /sync_remote.sh; /sync_remote.sh"
        ]
      }
      template {
        data = <<-EOF
        [ai4eosc-share]
        type = webdav
        url = https://share.services.ai4os.eu/remote.php/dav
        vendor = nextcloud
        user = ${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}
        pass = ${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}
        EOF
        destination = "local/rclone.conf"
      }
      template {
        data = <<-EOF
        #!/usr/bin/env bash
        TS=$(date +"%Y-%m-%d-%H-%M-%S-%N")
        BACKUP_NAME="$${BACKUP_NAME}_$${TS}"
        tarbals='cache_db data db events inmem_db keys logs'
        export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $$RCLONE_CONFIG_RSHARE_PASS)
        echo "creating a CVAT backup $$BACKUP_NAME ..."
        if [[ -d $LOCAL_PATH/$$BACKUP_NAME ]]; then
          echo "ERROR: local backup folder $LOCAL_PATH/$$BACKUP_NAME already exists"
          exit 1
        fi
        rm -rf $LOCAL_PATH/$$BACKUP_NAME
        mkdir -p $LOCAL_PATH/$$BACKUP_NAME
        cd $LOCAL_PATH
        for tarbal in $tarbals; do
          echo -n "creating $tarbal.tar.gz ..."
          tar -czf $LOCAL_PATH/$$BACKUP_NAME/$tarbal.tar.gz $tarbal
          if [ -f $LOCAL_PATH/$$BACKUP_NAME/$tarbal.tar.gz ]; then echo "OK"; else echo "ERROR"; fi
        done
        if [[ $(rclone lsd $REMOTE_PATH/$$BACKUP_NAME; echo $?) == 0 ]]; then
          echo "ERROR: remote backup folder $REMOTE_PATH/$$BACKUP_NAME already exists"
          exit 1
        fi
        rclone mkdir $REMOTE_PATH/$$BACKUP_NAME
        rclone sync $LOCAL_PATH/$$BACKUP_NAME $REMOTE_PATH/$$BACKUP_NAME --progress
        EOF
        destination = "local/sync_remote.sh"
      }
    }

    task "clickhouse" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 1000
        memory = 4096
      }
      env {
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
      }
      config {
        image = "clickhouse/clickhouse-server:23.11-alpine"
        ports = ["clickhouse_http"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/events:/var/lib/clickhouse"
        ]
        mount {
          type = "bind"
          target = "/docker-entrypoint-initdb.d/init.sh"
          source = "local/docker-entrypoint-initdb.d/init.sh"
          readonly = false
        }
      }
      template {
        data = <<-EOF
          #!/bin/bash
          CLICKHOUSE_DB="$$$${CLICKHOUSE_DB:-cvat}";
          clickhouse-client --query "CREATE DATABASE IF NOT EXISTS $$$${CLICKHOUSE_DB};"
          echo "
          CREATE TABLE IF NOT EXISTS $$$${CLICKHOUSE_DB}.events
          (
            \`scope\` String NOT NULL,
            \`obj_name\` String NULL,
            \`obj_id\` UInt64 NULL,
            \`obj_val\` String NULL,
            \`source\` String NOT NULL,
            \`timestamp\` DateTime64(3, 'Etc/UTC') NOT NULL,
            \`count\` UInt16 NULL,
            \`duration\` UInt32 DEFAULT toUInt32(0),
            \`project_id\` UInt64 NULL,
            \`task_id\` UInt64 NULL,
            \`job_id\` UInt64 NULL,
            \`user_id\` UInt64 NULL,
            \`user_name\` String NULL,
            \`user_email\` String NULL,
            \`org_id\` UInt64 NULL,
            \`org_slug\` String NULL,
            \`payload\` String NULL
          )
          ENGINE = MergeTree
          PARTITION BY toYYYYMM(timestamp)
          ORDER BY (timestamp)
          SETTINGS index_granularity = 8192
          ;" | clickhouse-client
        EOF
        destination = "local/docker-entrypoint-initdb.d/init.sh"
      }
    }

    task "grafana" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 300
        memory = 2048
      }
      env {
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
        GF_PATHS_PROVISIONING = "/etc/grafana/provisioning"
        GF_AUTH_BASIC_ENABLED = false
        GF_AUTH_ANONYMOUS_ENABLED = true
        GF_AUTH_ANONYMOUS_ORG_ROLE = "Admin"
        GF_AUTH_DISABLE_LOGIN_FORM = true
        GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS = "grafana-clickhouse-datasource"
        GF_SERVER_ROOT_URL = "http://${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}/analytics"
        GF_INSTALL_PLUGINS = "https://github.com/grafana/clickhouse-datasource/releases/download/v4.0.8/grafana-clickhouse-datasource-4.0.8.linux_amd64.zip;grafana-clickhouse-datasource"
        GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH = "/var/lib/grafana/dashboards/all_events.json"
      }
      config {
        image = "grafana/grafana-oss:10.1.2"
        ports = ["grafana"]
        mount {
          type = "bind"
          target = "/var/lib/grafana/dashboards/all_events.json"
          source = "local/var/lib/grafana/dashboards/all_events.json"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/var/lib/grafana/dashboards/management.json"
          source = "local/var/lib/grafana/dashboards/management.json"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/var/lib/grafana/dashboards/monitoring.json"
          source = "local/var/lib/grafana/dashboards/monitoring.json"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/etc/grafana/provisioning/dashboards/dashboard.yaml"
          source = "local/etc/grafana/provisioning/dashboards/dashboard.yaml"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/etc/grafana/provisioning/datasources/ds.yaml"
          source = "local/etc/grafana/provisioning/datasources/ds.yaml"
          readonly = false
        }
        command = "exec"
        args = [
          "/run.sh"
        ]
      }
      artifact {
        source = "https://github.com/ai4os/ai4os-cvat/raw/v2.28.0-AI4OS/components/analytics/grafana/dashboards/all_events.json"
        destination = "local/var/lib/grafana/dashboards/"
      }
      artifact {
        source = "https://github.com/ai4os/ai4os-cvat/raw/v2.28.0-AI4OS/components/analytics/grafana/dashboards/management.json"
        destination = "local/var/lib/grafana/dashboards/"
      }
      artifact {
        source = "https://github.com/ai4os/ai4os-cvat/raw/v2.28.0-AI4OS/components/analytics/grafana/dashboards/monitoring.json"
        destination = "local/var/lib/grafana/dashboards/"
      }
      template {
        data = <<-EOF
        apiVersion: 1
        providers:
          - name: cvat-logs
            type: file
            updateIntervalSeconds: 30
            options:
              path: /var/lib/grafana/dashboards
              foldersFromFilesStructure: true
        EOF
        destination = "local/etc/grafana/provisioning/dashboards/dashboard.yaml"
      }
      template {
        data = <<-EOF
        apiVersion: 1
        datasources:
          - name: 'ClickHouse'
            type: grafana-clickhouse-datasource
            isDefault: true
            jsonData:
              defaultDatabase: $$$${CLICKHOUSE_DB}
              port: $$$${CLICKHOUSE_PORT}
              server: $$$${CLICKHOUSE_HOST}
              username: $$$${CLICKHOUSE_USER}
              tlsSkipVerify: false
              protocol: http
            secureJsonData:
              password: $$$${CLICKHOUSE_PASSWORD}
            editable: true
        EOF
        destination = "local/etc/grafana/provisioning/datasources/ds.yaml"
      }
    }

    task "db" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        POSTGRES_USER = "root"
        POSTGRES_DB = "cvat"
        POSTGRES_HOST_AUTH_METHOD = "trust"
        PGDATA = "/var/lib/postgresql/data/pgdata"
      }
      config {
        image = "postgres:16.4-alpine"
        privileged = true
        force_pull = "false"
        ports = ["db"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/db:/var/lib/postgresql/data"
        ]
      }
    }

    task "redis_inmem" {
      driver = "docker"
      kill_timeout = "120s" # double the time of the periodic dump (60s, see --save argument below)
      resources { # https://redis.io/docs/latest/operate/rs/installing-upgrading/install/plan-deployment/hardware-requirements/
        cpu = 100
        memory = 2048
      }
      config {
        image = "redis:7.2.3-alpine"
        ports = ["redis_inmem"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/inmem_db:/data"
        ]
        command = "redis-server"
        args = [
          "--save", "60", "100",
          "--appendonly", "yes",
        ]
      }
    }

    task "redis_ondisk" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 5120
      }
      config {
        image = "apache/kvrocks:2.7.0"
        ports = ["redis_ondisk"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/cache_db:/var/lib/kvrocks/data"
        ]
        args = [
          "--dir", "/var/lib/kvrocks/data"
        ]
      }
    }

    task "vector" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 2048
      }
      env {
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
      }
      config {
        image = "timberio/vector:0.26.0-alpine"
        ports = ["vector"]
        mount {
          type = "bind"
          target = "/etc/vector/vector.toml"
          source = "local/etc/vector/vector.toml"
          readonly = false
        }
      }
      artifact {
        source = "https://github.com/ai4os/ai4os-cvat/raw/v2.28.0-AI4OS/components/analytics/vector/vector.toml"
        destination = "local/etc/vector/"
      }
    }

    task "main" {
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 300
        memory = 4096
      }
      env {
        ALLOWED_HOSTS = "*"
        ADAPTIVE_AUTO_ANNOTATION = "false"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_ANALYTICS = "1"
        CVAT_BASE_URL = ""
        CVAT_HOST = "${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}"
        CVAT_LOG_IMPORT_ERRORS = "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_LEVEL = "INFO"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        DJANGO_MODWSGI_EXTRA_ARGS = ""
        DJANGO_SUPERUSER_PASSWORD = "${NOMAD_META_cvat_su_password}"
        DJANGO_SUPERUSER_USERNAME = "${NOMAD_META_cvat_su_username}"
        IAM_OPA_ADDR = "${NOMAD_HOST_ADDR_opa}"
        IAM_OPA_HOST = "${NOMAD_HOST_IP_opa}"
        IAM_OPA_PORT = "${NOMAD_HOST_PORT_opa}"
        IAM_OPA_BUNDLE = "1"
        NUMPROCS = "2"
        ONE_RUNNING_JOB_IN_QUEUE_PER_USER = "false"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["server"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/share:/home/django/share"
        ]
        command = "init"
        args = [
          "ensuresuperuser",
          "run",
          "server"
        ]
      }
    }

    task "utils" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_INMEM_PASSWORD = ""
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["utils"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/share:/home/django/share",
        ]
        command = "run"
        args = [
          "utils"
        ]
      }
    }

    task "worker_import" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cores = 1
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_import"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/share:/home/django/share",
        ]
        command = "run"
        args = [
          "worker.import"
        ]
      }
    }

    task "worker_export" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cores = 1
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_export"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/logs:/home/django/logs",
        ]
        command = "run"
        args = [
          "worker.export"
        ]
      }
    }

    task "worker_annotation" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_annotation"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/share:/home/django/share",
        ]
        command = "run"
        args = [
          "worker.annotation"
        ]
      }
    }

    task "worker_webhooks" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_webhooks"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/logs:/home/django/logs",
        ]
        command = "run"
        args = [
          "worker.webhooks"
        ]
      }
    }

    task "worker_quality_reports" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_quality_reports"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/logs:/home/django/logs",
        ]
        command = "run"
        args = [
          "worker.quality_reports"
        ]
      }
    }

    task "worker_analytics_reports" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cpu = 100
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CLICKHOUSE_DB = "${NOMAD_META_clickhouse_db}"
        CLICKHOUSE_USER = "${NOMAD_META_clickhouse_user}"
        CLICKHOUSE_PASSWORD = "${NOMAD_META_clickhouse_password}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_analytics_reports"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/logs:/home/django/logs",
        ]
        command = "run"
        args = [
          "worker.analytics_reports"
        ]
      }
    }

    # https://github.com/cvat-ai/cvat/issues/8983 - this container needs access to the shared volume
    task "worker_chunks" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      resources {
        cores = 1
        memory = 4096
      }
      env {
        CVAT_ALLOW_STATIC_CACHE = "${NOMAD_META_cvat_allow_static_cache}"
        CVAT_LOG_IMPORT_ERRORS =  "true"
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CVAT_REDIS_INMEM_HOST = "${NOMAD_HOST_IP_redis_inmem}"
        CVAT_REDIS_INMEM_PORT = "${NOMAD_HOST_PORT_redis_inmem}"
        CVAT_REDIS_ONDISK_HOST = "${NOMAD_HOST_IP_redis_ondisk}"
        CVAT_REDIS_ONDISK_PORT = "${NOMAD_HOST_PORT_redis_ondisk}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-server"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker_chunks"]
        volumes = [
          "..${NOMAD_ALLOC_DIR}/data/data:/home/django/data",
          "..${NOMAD_ALLOC_DIR}/data/keys:/home/django/keys",
          "..${NOMAD_ALLOC_DIR}/data/logs:/home/django/logs",
          "..${NOMAD_ALLOC_DIR}/data/share:/home/django/share"
        ]
        command = "run"
        args = [
          "worker.chunks"
        ]
      }
    }

    task "opa" {
      driver = "docker"
      kill_timeout = "30s"
      config {
        image = "openpolicyagent/opa:0.63.0"
        ports = ["opa"]
        command = "run"
        args = [
          "--server",
          "--log-level=debug",
          "--set=services.cvat.url=http://${NOMAD_HOST_ADDR_server}",
          "--set=bundles.cvat.service=cvat",
          "--set=bundles.cvat.resource=/api/auth/rules",
          "--set=bundles.cvat.polling.min_delay_seconds=5",
          "--set=bundles.cvat.polling.max_delay_seconds=15"
        ]
      }
    }

    task "ui" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      kill_timeout = "30s"
      config {
        image = "ai4oshub/ai4os-cvat:v2.28.0-ai4os-ui"
        force_pull = "${NOMAD_META_force_pull_img_cvat_ui}"
        ports = ["ui"]
      }
    }
  }
}
