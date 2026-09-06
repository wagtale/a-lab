# Akvorado Quick-Deploy: Commands Only

> For explanation of what each step does, see [`akvorado-lab-guide.md`](akvorado-lab-guide.md).

## 1. Install Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER   # log out and back in after this
sudo systemctl enable --now docker

docker run hello-world
```

## 2. Deploy the Stack

```bash
mkdir ~/akvorado
cd ~/akvorado

curl -sL https://github.com/akvorado/akvorado/releases/latest/download/docker-compose-quickstart.tar.gz | tar zxvf -

docker compose up -d
```

Open the UI at `http://<YOUR-VM-IP>:8081` once all containers are healthy.

## 3. Configure SNMP Enrichment (optional)

Add to `outlet.yaml`:

```yaml
metadata:
  providers:
    - type: snmp
      credentials:
        ::/0:
          communities: public
```

## 4. MikroTik / RouterOS v7: Fix Missing Sampling Rate

RouterOS v7 does not include the sampling rate in its NetFlow v9 export.
Add to `outlet.yaml`:

```yaml
core:
  default-sampling-rate: 1
```

Then restart the outlet:

```bash
docker compose restart akvorado-outlet
```

> Ensure the interface description on the router matches the regex in `outlet.yaml`
> (e.g. `transit : ISP`).

## 5. Teardown

```bash
docker compose down -v   # stops everything and removes data volumes
```