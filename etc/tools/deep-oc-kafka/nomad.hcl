// failed to setup alloc: pre-run hook "group_services" 
// failed: unable to get address for service "job-kafka-test-kafka": 

job "userjob-${JOB_UUID}" {

  id = "${JOB_UUID}"
  priority  = "${PRIORITY}"
  namespace = "${NAMESPACE}"

  meta {
    owner       = "${OWNER}"  # user-id from OIDC
    owner_name  = "${OWNER_NAME}"
    owner_email = "${OWNER_EMAIL}"
    title       = "${TITLE}"
    description = "${DESCRIPTION}"
  }

  group "kafka" {
    count = 3

    ephemeral_disk {
      size = ${DISK}
    }

    network {

        port "kafka-broker" {
            to = 9092
        }

        port "kafka-controller" {
            to = 9093
        }
    }

    service {
        name = "${JOB_UUID}-broker"
        provider = "consul"
        address = "${attr.unique.network.ip-address}"
        port = "kafka-broker"
        tags = [
          "traefik.enable=true",
          "traefik.http.routers.demo-kafka-public.tls=true",
          "traefik.http.routers.demo-kafka-public.rule=Host(`demo-kafka.deployments.cloud.ai4eosc.eu`,`www.demo-kafka.deployments.cloud.ai4eosc.eu`)",
          "traefik.http.services.kafka-broker.loadbalancer.server.port=9092"
        ]

    //     check {
    //         type     = "tcp"
    //         port     = "kafka-broker"
    //         path     = "/health"
    //         interval = "10s"
    //         timeout  = "2s"
    //     }
    }

    service {
        name = "${JOB_UUID}-controller"
        provider = "consul"
        address = "${attr.unique.network.ip-address}"
        port = "kafka-controller"
        tags = [
          "traefik.enable=true",
          "traefik.http.routers.demo-kafka-public.tls=true",
          "traefik.http.routers.demo-kafka-public.rule=Host(`demo-kafka.deployments.cloud.ai4eosc.eu`,`www.demo-kafka.deployments.cloud.ai4eosc.eu`)",
          "traefik.http.services.kafka-broker.loadbalancer.server.port=9092"
        ]

        // check {
        //     type     = "tcp"
        //     port     = "kafka-controller"
        //     path     = "/health"
        //     interval = "10s"
        //     timeout  = "2s"
        // }
    }

    task "usertask" {

      driver = "docker"

      config {
        image      = "${DOCKER_IMAGE}:${DOCKER_TAG}"
        privileged = true
        ports = ["kafka-broker", "kafka-controller"]
        network_mode = "host"
      }
       template {
         data        = <<EOF
# Configuration for a single NGINX upstream service.
KAFKA_CFG_CONTROLLER_QUORUM_VOTERS={{- range $index, $services := service "kafka-controller" -}}
    {{- if eq $index 0 -}}
        {{$index}}@{{ .Address }}:9093
    {{- else -}}
        ,{{$index}}@{{ .Address }}:9093
    {{- end -}}
{{- end}}

KAFKA_CFG_NODE_ID={{- range $i, $e := service "kafka-broker" -}}
  {{- if and (eq .Address (env "attr.unique.network.ip-address")) (eq .Port (env "NOMAD_HOST_PORT_kafka_broker" | parseInt)) -}}
    {{$i}}
  {{- end -}}
{{- end}}

EOF
        destination = "${NOMAD_SECRETS_DIR}/vars.env"
        env         = true
        change_mode = "restart"
      }
      env {
        # KAFKA_CFG_NODE_ID                        = "${KAFKA_BROKER_ID}"
        KAFKA_CFG_PROCESS_ROLES                  = "controller,broker"
        ALLOW_PLAINTEXT_LISTENER                 = "yes"
        KAFKA_CFG_LISTENERS                      = "INTERNAL://0.0.0.0:19092,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093"
        KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP = "CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT"
        KAFKA_CFG_CONTROLLER_LISTENER_NAMES      = "CONTROLLER"
        #KAFKA_CFG_CONTROLLER_QUORUM_VOTERS       = "0@kafka0:9093"
        KAFKA_ADVERTISED_LISTENERS               = "INTERNAL://${NOMAD_IP_kafka-broker}:19092,EXTERNAL://${NOMAD_IP_kafka-broker}:9092"
        KAFKA_INTER_BROKER_LISTENER_NAME         = "INTERNAL"
        KAFKA_CFG_PORT                           = "9092"
        KAFKA_KRAFT_CLUSTER_ID                   = "QZ0WG-zFRYquI54uiCfiTg"
        KAFKA_CFG_NUM_PARTITIONS                 = "4"
        KAFKA_CFG_DEFAULT_REPLICATION_FACTOR     = "1"
        KAFKA_CFG_SOCKET_REQUEST_MAX_BYTES       = "1024000000"
        BITNAMI_DEBUG                            = "yes"
      }

      resources {
        cores  = ${CPU_NUM}
        memory = ${RAM}
        memory_max = ${RAM}
      }
    }
  }
}
