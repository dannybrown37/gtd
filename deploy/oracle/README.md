# Deploying gtd to Oracle Cloud (Always Free) + Tailscale

Runs `gtd api` (which also serves the `webapp/` PWA — see the root `CLAUDE.md`) on an
Oracle Cloud Always Free VM, reachable privately over your [Tailscale](https://tailscale.com/)
tailnet rather than the open internet.

## 1. Provision the infrastructure

Requires an [OCI account](https://www.oracle.com/cloud/free/) and an
[API signing key](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm)
(different from your SSH key — this one authenticates Terraform to the OCI API).

```bash
cd deploy/oracle
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your tenancy/user OCIDs, API key fingerprint, region, etc.

terraform init
terraform apply
```

This creates a VCN, public subnet, internet gateway, and an Always Free
`VM.Standard.E2.1.Micro` instance running Ubuntu 24.04, with your SSH public
key installed for the `ubuntu` user. `terraform apply` prints the instance's
public IP when done — you'll only need it for this initial setup; Tailscale
takes over after.

## 2. Bootstrap the VM

SSH in using the public IP Terraform printed:

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<public-ip>
```

Create `~/.env` on the VM with your Notion credentials and a chosen API key:

```bash
cp env.example .env   # copy the contents over manually, e.g. via `nano ~/.env`, then fill in real values
chmod 600 ~/.env
```

Then run the bootstrap script, which installs Tailscale, `uv`, `gtd-tui[api]`,
and sets up `gtd-api` as a systemd service:

```bash
curl -fsSL https://raw.githubusercontent.com/dannybrown37/gtd/main/deploy/oracle/bootstrap.sh | bash
```

Follow the printed Tailscale login URL to join the VM to your tailnet.

## 3. Connect your other devices

Install Tailscale on your phone/laptop and sign into the same tailnet. Once
connected, the VM is reachable at its Tailscale IP (`tailscale ip -4` on the
VM) — e.g. `http://100.x.x.x:8000/` opens the webapp, no public exposure
needed.

## 4. Verify

```bash
ssh ubuntu@<public-ip> 'sudo systemctl status gtd-api'
```

Then load `http://<tailscale-ip>:8000/` from a device on your tailnet — it
should serve the webapp (you'll be prompted once per browser/device for the
`GTD_API_KEY` bearer token you set in `~/.env`, stored thereafter in
`localStorage`).

## Updating

```bash
uv tool install --force "gtd-tui[api]"
sudo systemctl restart gtd-api
```

## Notes

- Uses an **ephemeral** public IP (free) since Tailscale is the actual access
  path — no need to pay for a reserved one.
- The security list opens TCP 22 (SSH) and UDP 41641 (Tailscale direct
  connections) to the internet; port 8000 (the API/webapp) is **not** opened
  publicly — it's only reachable over the tailnet or via the VM's private
  Oracle-internal IP.
- See the root `CLAUDE.md`'s "Webapp" section for the packaging gotcha that
  can make a published release 404 on webapp routes despite the code being
  correct — always confirm a new release actually bundles `webapp/` before
  redeploying.
