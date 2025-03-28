/*
Convention:
-----------
* ${UPPERCASE} are replaced by the user
* ${lowercase} are replace by Nomad at launchtime
* remaining is default, same for everybody

When replacing user values we use safe_substitute() so that ge don't get an error for not
replacing Nomad values
*/

job "snapshot-${JOB_UUID}" {
  namespace = "${NAMESPACE}"
  type      = "batch"         # snapshot jobs should not be redeployed when exit_code=0
  region    = "global"
  id        = "${JOB_UUID}"
  priority  = "50"            # snapshot jobs have medium priority

  meta {
    owner       = "${OWNER}"  # user-id from OIDC
    owner_name  = "${OWNER_NAME}"
    owner_email = "${OWNER_EMAIL}"
    title       = ""
    description = ""

    snapshot_id = "${TARGET_JOB_ID}_${TIMESTAMP}"  # Harbor Docker image label
    submit_time = "${SUBMIT_TIME}"
  }

  # Force snapshot job to land in the same node as target job
  constraint {
    attribute = "${node.unique.id}"
    operator  = "regexp"
    value     = "${TARGET_NODE_ID}"
  }

  group "usergroup" {

    task "check-container-size" {

      lifecycle {
        hook = "prestart"
      }

      driver = "raw_exec"

      config {
        command = "/bin/bash"
        args = ["-c", <<EOF
#!/bin/bash

if ! command -v jq &> /dev/null; then
  echo "Instalando jq..."
  sudo apt update
  sudo apt install -y jq
fi

input_job_id=${TARGET_JOB_ID}

container_ids=$(sudo docker ps -q)

size_limit=$((10 * 1024 * 1024 * 1024))  # 10 GB en bytes

for container_id in $container_ids; do

        task_name=$(sudo docker exec "$container_id" printenv NOMAD_TASK_NAME)


        if [ "$task_name" == "main" ]; then

                  job_id=$(sudo docker exec "$container_id" printenv NOMAD_JOB_ID)

                  if [ "$job_id" == "$input_job_id" ]; then

                          echo "$container_id"

                          container_size=$(sudo docker inspect --size --format='{{.SizeRootFs}}' "$container_id")

                          echo "$container_size"

                          if [ "$container_size" -gt "$size_limit" ]; then
                                  echo "Container $container_id with NOMAD_JOB_ID $job_id size is $((container_size / (1024 * 1024 * 1024))) GB, which is more than 10 GB."
                                  exit 1
                          else
                                  echo "Container $container_id with NOMAD_JOB_ID $job_id size is $((container_size / (1024 * 1024 * 1024))) GB, which is less than 10 GB."
                                  exit 0
                          fi

                  fi

        fi
done

echo "There is no container with NOMAD_JOB_ID: $input_job_id"
exit 1
EOF
        ]
      }

      restart {
        attempts = 0
        mode     = "fail"
      }

    }

    task "upload-image-registry" {

      driver = "raw_exec"

      config {
        command = "/bin/bash"
        args = ["-c", <<EOF
#!/bin/bash


input_job_id=${TARGET_JOB_ID}

container_ids=$(sudo docker ps -q)

for container_id in $container_ids; do

        task_name=$(sudo docker exec "$container_id" printenv NOMAD_TASK_NAME)

        if [ "$task_name" == "main" ]; then

                job_id=$(sudo docker exec "$container_id" printenv NOMAD_JOB_ID)

                if [ "$job_id" == "$input_job_id" ]; then

                        echo "Container ID: $container_id"
                        echo "Creating a snapshot of docker container"

                        sudo docker commit --change 'LABEL OWNER="${OWNER}" OWNER_NAME="${OWNER_NAME}" OWNER_EMAIL="${OWNER_EMAIL}" TITLE="${TITLE}" DESCRIPTION="${DESCRIPTION}" DATE="${SUBMIT_TIME}" VO="${VO}"' $container_id ${FORMATTED_OWNER}

                        echo "Login on the registry"

                        username='${HARBOR_ROBOT_USER}'
                        password='${HARBOR_ROBOT_PASSWORD}'

                        echo "$password" | sudo docker login https://registry.services.ai4os.eu --username "$username" --password-stdin

                        echo "Uploading image to registry"

                        sudo docker tag ${FORMATTED_OWNER} registry.services.ai4os.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}

                        sudo docker tag ${FORMATTED_OWNER} registry.services.ai4os.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}

                        if ! sudo docker push registry.services.ai4os.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}; then
                            sudo docker image rm registry.services.ai4os.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}
                            sudo docker image rm ${FORMATTED_OWNER}:latest
                            exit 1
                        fi

                        sudo docker image rm registry.services.ai4os.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}
                        sudo docker image rm ${FORMATTED_OWNER}:latest

                fi
        fi
done
exit 0


EOF
        ]
      }
    }

    restart {
      attempts = 0
      mode     = "fail"
    }

  }

}