# docker build -t ai4-papi -f docker/Dockerfile .

FROM ubuntu:22.04

# Install Nomad
# Updated commands: https://developer.hashicorp.com/nomad/tutorials/get-started/gs-install
# curl, unzip: install for the rclone command
RUN apt-get update && \
    apt-get install -y \
    wget \
    curl \
    unzip \
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
    apt-get install -y nomad && \
    rm -rf /var/lib/apt/lists/*

ENV NOMAD_ADDR=https://193.146.75.205:4646
ENV NOMAD_CACERT=/home/nomad-certs/nomad-ca.pem
ENV NOMAD_CLIENT_CERT=/home/nomad-certs/cli.pem
ENV NOMAD_CLIENT_KEY=/home/nomad-certs/cli-key.pem

# Allow the ai4os-proxy (172.16.44.228) to forward requests to the API
# Trying to restrict to internal network (172.16.0.0/16 or 172.*) didn't work
# ENV FORWARDED_ALLOW_IPS="*"
ENV FORWARDED_ALLOW_IPS=172.16.44.228

# Install latest rclone (for checking backups in CVAT; "/storage" route)
RUN curl https://rclone.org/install.sh | bash

# Install API
WORKDIR /home/ai4-papi
COPY . .
RUN pip3 install -e .

# Run the API
EXPOSE 80
CMD ai4papi-run \
    --host 0.0.0.0 \
    --port 80
