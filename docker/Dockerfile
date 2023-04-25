# Run this Docker file mounting the nomad certs folder in `/home/nomad-certs`
# and lenstcrypt certs in `/home/letsencrypt`
#
# docker build --no-cache -t ai4-papi .
# docker run -ti -v /home/ubuntu/nomad-certs:/home/nomad-certs -v /home/ubuntu/letsencrypt:/home/letsencrypt -p 443:443 ai4-papi

FROM ubuntu:22.04

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
    && rm -rf /var/lib/apt/lists/*
RUN wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/hashicorp.list
RUN apt-get update && \
    apt-get install nomad && \
    rm -rf /var/lib/apt/lists/*

ENV NOMAD_ADDR=https://193.146.75.209:4646
ENV NOMAD_CACERT=/home/nomad-certs/nomad-ca.pem
ENV NOMAD_CLIENT_CERT=/home/nomad-certs/cli.pem
ENV NOMAD_CLIENT_KEY=/home/nomad-certs/cli-key.pem

# Install API
#FIXME: installing directly from Github with pip (`pip install git+https://github.com/`)
# breaks config (does not copy `etc` folder)
WORKDIR /home
RUN git clone -b master https://github.com/AI4EOSC/ai4-papi
RUN pip3 install -e ai4-papi
 
# Run the API
EXPOSE 443
CMD ai4papi-run \
    --host 0.0.0.0 \
    --port 443 \
    --ssl-keyfile /home/letsencrypt/live/api.dev.ai4eosc.eu/privkey.pem \
    --ssl-certfile /home/letsencrypt/live/api.dev.ai4eosc.eu/cert.pem
