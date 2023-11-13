# Run this Docker file mounting the nomad certs folder in `/home/nomad-certs`
# and lenstcrypt certs in `/home/letsencrypt`:
#
# docker build -t ai4-papi .
# docker run -ti -v /home/ubuntu/nomad-certs:/home/nomad-certs -v /home/ubuntu/letsencrypt:/home/letsencrypt -p 443:443 ai4-papi &
#
# See `docker_refresh.sh` for more information.

FROM ubuntu:22.04

ARG papi_branch=master

# Install Nomad
# Updated commands: https://developer.hashicorp.com/nomad/tutorials/get-started/gs-install
RUN apt-get update && \
    apt-get install -y \
    wget \
    gpg \
    coreutils \
    lsb-release \
    python3-pip \
    git \
    micro \
    && rm -rf /var/lib/apt/lists/*
RUN wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/hashicorp.list
RUN apt-get update && \
    apt-get install nomad && \
    rm -rf /var/lib/apt/lists/*

ENV NOMAD_ADDR=https://193.146.75.221:4646
ENV NOMAD_CACERT=/home/nomad-certs/nomad-ca.pem
ENV NOMAD_CLIENT_CERT=/home/nomad-certs/cli.pem
ENV NOMAD_CLIENT_KEY=/home/nomad-certs/cli-key.pem

# Allow the ai4os-proxy (172.16.44.228) to forward requests to the API
# Trying to restrict to internal network (172.16.0.0/16 or 172.*) didn't work
# ENV FORWARDED_ALLOW_IPS="*"
ENV FORWARDED_ALLOW_IPS=172.16.44.228

# Install API
#FIXME: installing directly from Github with pip (`pip install git+https://github.com/`)
# breaks config (does not copy `etc` folder)
# We use the ADD command to force a cache rebuilt when the repo is updated
# see: https://stackoverflow.com/a/65762156
WORKDIR /home
ADD "https://api.github.com/repos/AI4EOSC/ai4-papi/commits/${papi_branch}?per_page=1" latest_commit
RUN git clone -b ${papi_branch} https://github.com/AI4EOSC/ai4-papi
RUN pip3 install -e ai4-papi

# Run the API
EXPOSE 80
CMD ai4papi-run \
    --host 0.0.0.0 \
    --port 80
