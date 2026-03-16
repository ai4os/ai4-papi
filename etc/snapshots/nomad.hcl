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

size_limit=$((${SIZE_LIMIT_GB} * 1024 * 1024 * 1024))  # convert to bytes

if ! command -v jq &> /dev/null; then
  echo "Installing jq..."
  sudo apt update
  sudo apt install -y jq
fi

container_ids=$(sudo docker ps -q)

for container_id in $container_ids; do

  task_name=$(sudo docker exec "$container_id" printenv NOMAD_TASK_NAME)

  if [ "$task_name" == "main" ]; then

    job_id=$(sudo docker exec "$container_id" printenv NOMAD_JOB_ID)

    if [ "$job_id" == "${TARGET_JOB_ID}" ]; then

      container_size=$(sudo docker inspect --size --format='{{.SizeRootFs}}' "$container_id")
      echo "Target details:"
      echo "* Job UUID: $job_id"
      echo "* Task name: $task_name"
      echo "* Container ID: $container_id"
      echo "* Container size: $((container_size / (1024 * 1024 * 1024))) GB"

      if [ "$container_size" -gt "$size_limit" ]; then
        echo "Error: Snapshot size limit is ${SIZE_LIMIT_GB} GB."
        exit 1
      else
        exit 0
      fi

    fi
  fi
done

echo "There is no container with NOMAD_JOB_ID: ${TARGET_JOB_ID}"
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

container_ids=$(sudo docker ps -q)

for container_id in $container_ids; do

  task_name=$(sudo docker exec "$container_id" printenv NOMAD_TASK_NAME)

  if [ "$task_name" == "main" ]; then

    job_id=$(sudo docker exec "$container_id" printenv NOMAD_JOB_ID)

    if [ "$job_id" == "${TARGET_JOB_ID}" ]; then

      DOCKER_IMAGE_NAME="registry.cloud.ai4eosc.eu/user-snapshots/${FORMATTED_OWNER}:${TARGET_JOB_ID}_${TIMESTAMP}"

      echo "Creating a snapshot of docker container"
      sudo docker commit --change 'LABEL OWNER="${OWNER}" OWNER_NAME="${OWNER_NAME}" OWNER_EMAIL="${OWNER_EMAIL}" TITLE="${TITLE}" DESCRIPTION="${DESCRIPTION}" DATE="${SUBMIT_TIME}" VO="${VO}"' $container_id $DOCKER_IMAGE_NAME

      echo "Login on the registry"
      echo "${HARBOR_ROBOT_PASSWORD}" | sudo docker login registry.cloud.ai4eosc.eu --username "${HARBOR_ROBOT_USER}" --password-stdin

      echo "Uploading image to registry"
      sudo docker push "$DOCKER_IMAGE_NAME"
      push_exit_code=$?
      sudo docker image rm "$DOCKER_IMAGE_NAME"
      if [ "$push_exit_code" -ne 0 ]; then
        exit "$push_exit_code"
      fi

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