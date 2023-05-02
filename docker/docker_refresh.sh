# Refresh Docker container running ai4-papi ([!] run with `sudo`)

cd /home/ubuntu/ai4-papi

echo "$(tput setaf 3)** Pull latest changes from Github **$(tput sgr0)"
git pull

echo "$(tput setaf 3)** Build new image **$(tput sgr0)"
docker build -t ai4-papi /home/ubuntu/ai4-papi/docker

echo "$(tput setaf 3)** Stop old container **$(tput sgr0)"
docker stop $(sudo docker ps | awk '/ai4-papi/ { print $1 }') || true

echo "$(tput setaf 3)** Start new container **$(tput sgr0)"
docker run -d -v /home/ubuntu/nomad-certs:/home/nomad-certs -v /home/ubuntu/letsencrypt:/home/letsencrypt -p 443:443 ai4-papi

cd /home/ubuntu
