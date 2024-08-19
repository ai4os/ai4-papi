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
- ${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom} --> registry.services.ai4os.eu/ai4os/ai4-cvat-server:v2.7.3-AI4OS

[1]: https://github.com/ai4os/ai4-cvat/blob/v2.7.3-AI4OS/nomad/ai4-cvat.jobspec.nomad.hcl
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
    force_pull_img_cvat_server         = false
    force_pull_img_cvat_ui             = false
    cvat_version                       = "v2.7.3"
    cvat_version_custom                = "-AI4OS"
    cvat_hostname                      = "${meta.domain}-${BASE_DOMAIN}"
    job_uuid                           = "${JOB_UUID}"

    grafana_clickhouse_plugin_version  = "3.3.0"
    smokescreen_opts                   = ""
    clickhouse_image                   = "clickhouse/clickhouse-server:22.3-alpine"
    db_image                           = "postgres:16-alpine"
    grafana_image                      = "grafana/grafana-oss:9.3.6"
    redis_image                        = "eqalpha/keydb:x86_64_v6.3.2"
    ui_image                           = "registry.services.ai4os.eu/ai4os/ai4-cvat-ui"
    opa_image                          = "openpolicyagent/opa:0.45.0-rootless"
    vector_image                       = "timberio/vector:0.26.0-alpine"
    server_image                       = "registry.services.ai4os.eu/ai4os/ai4-cvat-server"
    su_username                        = "admin"
    su_password                        = "${CVAT_PASSWORD}"
    su_email                           = "${CVAT_USERNAME}"

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

  # Avoid rescheduling the job on **other** nodes during a network cut
  # Command not working due to https://github.com/hashicorp/nomad/issues/16515
  reschedule {
    attempts  = 0
    unlimited = false
  }

  group "usergroup" {

    ephemeral_disk {
      size = 1024
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
      port "worker-import" {
        to = 8080
      }
      port "worker-export" {
        to = 8080
      }
      port "worker-annotation" {
        to = 8080
      }
      port "worker-webhooks" {
        to = 8080
      }
      port "worker-quality-reports" {
        to = 8080
      }
      port "worker-analytics-reports" {
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
      port "redis" {
        to = 6379
      }
      port "clickhouse_native" {
        to = 9000
      }
      port "clickhouse_http" {
        to = 8123
      }
      port "clickhouse_inter_server" {
        to = 9009
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
        "traefik.http.routers.${NOMAD_META_job_uuid}-ui.tls=true",
        "traefik.http.routers.${NOMAD_META_job_uuid}-ui.entrypoints=websecure",
        "traefik.http.routers.${NOMAD_META_job_uuid}-ui.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`)"
      ]
    }

    service {
      name = "${JOB_UUID}-server"
      port = "server"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${NOMAD_META_job_uuid}-server.tls=true",
        "traefik.http.routers.${NOMAD_META_job_uuid}-server.entrypoints=websecure",
        "traefik.http.routers.${NOMAD_META_job_uuid}-server.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`) && PathPrefix(`/api/`, `/static/`, `/admin`, `/documentation/`, `/django-rq`)"
      ]
    }

    service {
      name = "${JOB_UUID}-grafana"
      port = "grafana"
      tags = [
        "traefik.enable=true",
        "traefik.http.routers.${NOMAD_META_job_uuid}-grafana.tls=true",
        "traefik.http.routers.${NOMAD_META_job_uuid}-grafana.entrypoints=websecure",
        "traefik.http.routers.${NOMAD_META_job_uuid}-grafana.rule=Host(`${JOB_UUID}.${meta.domain}-${BASE_DOMAIN}`) && PathPrefix(`/analytics`)",
        "traefik.http.middlewares.${NOMAD_META_job_uuid}-grafana-analytics-auth.forwardauth.address=http://${NOMAD_HOST_ADDR_server}/analytics",
        "traefik.http.middlewares.${NOMAD_META_job_uuid}-grafana-analytics-auth.forwardauth.authRequestHeaders=Cookie,Authorization",
        "traefik.http.middlewares.${NOMAD_META_job_uuid}-grafana-analytics-strip-prefix.stripprefix.prefixes=/analytics",
        "traefik.http.routers.${NOMAD_META_job_uuid}-grafana.middlewares=${NOMAD_META_job_uuid}-grafana-analytics-auth@consulcatalog,${NOMAD_META_job_uuid}-grafana-analytics-strip-prefix@consulcatalog",
        "traefik.services.${NOMAD_META_job_uuid}-grafana.loadbalancer.servers.url=${NOMAD_HOST_ADDR_grafana}",
        "traefik.services.${NOMAD_META_job_uuid}-grafana.loadbalancer.passHostHeader=false"
      ]
    }

    task "storagetask" {
      lifecycle {
        hook = "prestart"
        sidecar = "true"
      }
      driver = "docker"
      env {
        RCLONE_CONFIG               = "${NOMAD_META_RCLONE_CONFIG}"
        RCLONE_CONFIG_RSHARE_TYPE   = "webdav"
        RCLONE_CONFIG_RSHARE_URL    = "${NOMAD_META_RCLONE_CONFIG_RSHARE_URL}"
        RCLONE_CONFIG_RSHARE_VENDOR = "${NOMAD_META_RCLONE_CONFIG_RSHARE_VENDOR}"
        RCLONE_CONFIG_RSHARE_USER   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_USER}"
        RCLONE_CONFIG_RSHARE_PASS   = "${NOMAD_META_RCLONE_CONFIG_RSHARE_PASS}"
        REMOTE_PATH                 = "rshare:${NOMAD_META_RCLONE_REMOTE_PATH}/${NOMAD_META_job_uuid}"
        LOCAL_PATH                  = "/storage"
      }
      config {
        image   = "ignacioheredia/ai4-docker-storage"
        privileged = true
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/storage/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/db:/storage/db:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/storage/share:shared",
        ]
        mount {
          type = "bind"
          target = "/srv/.rclone/rclone.conf"
          source = "local/rclone.conf"
          readonly = false
        }
        mount {
          type = "bind"
          target = "/mount_storage.sh"
          source = "local/mount_storage.sh"
          readonly = false
        }
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
        export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $RCLONE_CONFIG_RSHARE_PASS)
        rclone mkdir $REMOTE_PATH/data
        rclone mkdir $REMOTE_PATH/db
        rclone mkdir $REMOTE_PATH/share
        rclone mount $REMOTE_PATH/data $LOCAL_PATH/data --uid 1000 --gid 1000 --dir-perms 0750 --allow-non-empty --allow-other --vfs-cache-mode full &
        rclone mount $REMOTE_PATH/db $LOCAL_PATH/db --uid 70 --gid 70 --dir-perms 0700 --allow-non-empty --allow-other --vfs-cache-mode full &
        rclone mount $REMOTE_PATH/share $LOCAL_PATH/share --uid 1000 --gid 1000 --dir-perms 0750 --allow-non-empty --allow-other --vfs-cache-mode full
        EOF
        destination = "local/mount_storage.sh"
      }
      resources {
        cpu    = 50        # minimum number of CPU MHz is 2
        memory = 2000
      }
    }

    task "storagecleanup" {
      lifecycle {
        hook = "poststop"
      }
      driver = "raw_exec"
      config {
        command = "/bin/bash"
        args = [
          "-c",
          "sudo umount /nomad-storage/${NOMAD_META_job_uuid}/data && sudo rmdir /nomad-storage/${NOMAD_META_job_uuid}/data && sudo umount /nomad-storage/${NOMAD_META_job_uuid}/db && sudo rmdir /nomad-storage/${NOMAD_META_job_uuid}/db && sudo umount /nomad-storage/${NOMAD_META_job_uuid}/share && sudo rmdir /nomad-storage/${NOMAD_META_job_uuid}/share"
        ]
      }
    }

    task "clickhouse" {
      driver = "docker"
      resources {
        memory = 2048
      }
      env {
        CLICKHOUSE_DB = "cvat"
        CLICKHOUSE_USER = "user"
        CLICKHOUSE_PASSWORD = "user"
      }
      config {
        image = "${NOMAD_META_clickhouse_image}"
        ports = ["clickhouse_native", "clickhouse_http", "clickhouse_inter_server"]
        mount {
          type = "volume"
          target = "/var/lib/clickhouse"
          source = "${NOMAD_META_job_uuid}-events-db"
          readonly = false
        }
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
      env {
        GF_PATHS_PROVISIONING = "/etc/grafana/provisioning"
        GF_AUTH_BASIC_ENABLED = false
        GF_AUTH_ANONYMOUS_ENABLED = true
        GF_AUTH_ANONYMOUS_ORG_ROLE = "Admin"
        GF_AUTH_DISABLE_LOGIN_FORM = true
        GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS = "grafana-clickhouse-datasource"
        GF_SERVER_ROOT_URL = "http://${NOMAD_META_job_uuid}.${NOMAD_META_cvat_hostname}/analytics"
        GF_INSTALL_PLUGINS = "https://github.com/grafana/clickhouse-datasource/releases/download/v${NOMAD_META_grafana_clickhouse_plugin_version}/grafana-clickhouse-datasource-${NOMAD_META_grafana_clickhouse_plugin_version}.linux_amd64.zip;grafana-clickhouse-datasource"
        GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH = "/var/lib/grafana/dashboards/all_events.json"
      }
      config {
        image = "${NOMAD_META_grafana_image}"
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
        source = "https://github.com/ai4os/ai4-cvat/raw/${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}/components/analytics/grafana/dashboards/all_events.json"
        destination = "local/var/lib/grafana/dashboards/"
      }
      artifact {
        source = "https://github.com/ai4os/ai4-cvat/raw/${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}/components/analytics/grafana/dashboards/management.json"
        destination = "local/var/lib/grafana/dashboards/"
      }
      artifact {
        source = "https://github.com/ai4os/ai4-cvat/raw/${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}/components/analytics/grafana/dashboards/monitoring.json"
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
          - name: ClickHouse
            type: grafana-clickhouse-datasource
            isDefault: true
            jsonData:
              defaultDatabase: cvat
              port: ${NOMAD_HOST_PORT_clickhouse_native}
              server: ${NOMAD_HOST_IP_clickhouse_native}
              username: user
              tlsSkipVerify: false
            secureJsonData:
              password: user
            editable: true
        EOF
        destination = "local/etc/grafana/provisioning/datasources/ds.yaml"
      }
    }

    task "db" {
      driver = "docker"
      env {
        POSTGRES_USER = "root"
        POSTGRES_DB = "cvat"
        POSTGRES_HOST_AUTH_METHOD = "trust"
        PGDATA = "/home/postgresql/pgdata"
      }
      config {
        image = "${NOMAD_META_db_image}"
        force_pull = "false"
        ports = ["db"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/db:/home/postgresql:shared",
        ]
      }
    }

    task "redis" {
      driver = "docker"
      resources {
        cores = 1
        memory = 5120
      }
      config {
        image = "${NOMAD_META_redis_image}"
        ports = ["redis"]
        mount {
          type = "volume"
          target = "/data"
          source = "${NOMAD_META_job_uuid}-redis"
          readonly = false
        }
        command = "keydb-server"
        args = [
          "/etc/keydb/keydb.conf",
          "--storage-provider", "flash", "/data/flash",
          "--maxmemory", "5G",
          "--maxmemory-policy", "allkeys-lfu"
        ]
      }
    }

    task "vector" {
      driver = "docker"
      resources {
        memory = 1024
      }
      env {
        CLICKHOUSE_DB = "cvat"
        CLICKHOUSE_USER = "user"
        CLICKHOUSE_PASSWORD = "user"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
      }
      config {
        image = "${NOMAD_META_vector_image}"
        ports = ["vector"]
        mount {
          type = "bind"
          target = "/etc/vector/vector.toml"
          source = "local/etc/vector/vector.toml"
          readonly = false
        }
      }
      artifact {
        source = "https://github.com/ai4os/ai4-cvat/raw/${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}/components/analytics/vector/vector.toml"
        destination = "local/etc/vector/"
      }
    }

    task "main" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      resources {
        cores = 1
        memory = 4096
      }
      env {
        DJANGO_MODWSGI_EXTRA_ARGS = ""
        ALLOWED_HOSTS = "*"
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        ADAPTIVE_AUTO_ANNOTATION = "false"
        IAM_OPA_ADDR = "${NOMAD_HOST_ADDR_opa}"
        IAM_OPA_HOST = "${NOMAD_HOST_IP_opa}"
        IAM_OPA_PORT = "${NOMAD_HOST_PORT_opa}"
        IAM_OPA_BUNDLE = "1"
        NUMPROCS = "2"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        DJANGO_SUPERUSER_USERNAME = "${NOMAD_META_su_username}"
        DJANGO_SUPERUSER_PASSWORD = "${NOMAD_META_su_password}"
        DJANGO_SUPERUSER_EMAIL = "${NOMAD_META_su_email}"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        CVAT_ANALYTICS = "1"
        CVAT_BASE_URL = ""
        CVAT_HOST = "${NOMAD_META_job_uuid}.${NOMAD_META_cvat_hostname}"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "registry.services.ai4os.eu/ai4os/ai4-cvat-server:v2.7.3-AI4OS"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["server"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/home/django/share:shared",
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
      resources {
        cores = 1
        memory = 1024
      }
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        CLICKHOUSE_HOST = "${NOMAD_HOST_IP_clickhouse_http}"
        CLICKHOUSE_PORT = "${NOMAD_HOST_PORT_clickhouse_http}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["utils"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/home/django/share:shared",
        ]
        command = "run"
        args = [
          "utils"
        ]
      }
    }

    task "worker-import" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      resources {
        cores = 1
        memory = 1024
      }
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-import"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/home/django/share:shared",
        ]
        command = "run"
        args = [
          "worker.import"
        ]
      }
    }

    task "worker-export" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      resources {
        cores = 1
        memory = 1024
      }
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-export"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/home/django/share:shared",
        ]
        command = "run"
        args = [
          "worker.export"
        ]
      }
    }

    task "worker-annotation" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      resources {
        cores = 1
        memory = 1024
      }
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-annotation"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
          "/nomad-storage/${NOMAD_META_job_uuid}/share:/home/django/share:shared",
        ]
        command = "run"
        args = [
          "worker.annotation"
        ]
      }
    }

    task "worker-webhooks" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
        SMOKESCREEN_OPTS = "${NOMAD_META_smokescreen_opts}"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-webhooks"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
        ]
        command = "run"
        args = [
          "worker.webhooks"
        ]
      }
    }

    task "worker-quality-reports" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "1"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-quality-reports"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
        ]
        command = "run"
        args = [
          "worker.quality_reports"
        ]
      }
    }

    task "worker-analytics-reports" {
      lifecycle {
        hook = "poststart"
        sidecar = "true"
      }
      driver = "docker"
      resources {
        cores = 1
        memory = 1024
      }
      env {
        CVAT_REDIS_HOST = "${NOMAD_HOST_IP_redis}"
        CVAT_REDIS_PORT = "${NOMAD_HOST_PORT_redis}"
        CVAT_REDIS_PASSWORD = ""
        CVAT_POSTGRES_HOST = "${NOMAD_HOST_IP_db}"
        CVAT_POSTGRES_PORT = "${NOMAD_HOST_PORT_db}"
        DJANGO_LOG_SERVER_HOST = "${NOMAD_HOST_IP_vector}"
        DJANGO_LOG_SERVER_PORT = "${NOMAD_HOST_PORT_vector}"
        NUMPROCS = "2"
      }
      config {
        image = "${NOMAD_META_server_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_server}"
        ports = ["worker-analytics-reports"]
        volumes = [
          "/nomad-storage/${NOMAD_META_job_uuid}/data:/home/django/data:shared",
        ]
        command = "run"
        args = [
          "worker.analytics_reports"
        ]
      }
    }

    task "opa" {
      driver = "docker"
      config {
        image = "${NOMAD_META_opa_image}"
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
      driver = "docker"
      config {
        image = "${NOMAD_META_ui_image}:${NOMAD_META_cvat_version}${NOMAD_META_cvat_version_custom}"
        force_pull = "${NOMAD_META_force_pull_img_cvat_ui}"
        ports = ["ui"]
      }
    }

  }

}
