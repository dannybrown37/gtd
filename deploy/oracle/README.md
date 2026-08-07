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

Once [auto-deploy](#auto-deploy-from-github-actions) is set up, pushing a
release to `main` updates the VM on its own. To update by hand:

```bash
uv tool install --force "gtd-tui[api]"
sudo systemctl restart gtd-api
```

## Auto-deploy from GitHub Actions

`publish.yml`'s `deploy` job updates this VM automatically after each PyPI
release. The VM has no public ingress, so the runner joins your tailnet as an
**ephemeral `tag:ci` node** and connects over **Tailscale SSH** — no SSH
private key is stored in GitHub, and the only secret is a Tailscale OAuth
client scoped to minting `tag:ci` auth keys.

Deploys are triggered by the `v*` tag that `commitizen` pushes, **not** by
every push to `main`. A `chore:`/`docs:`/`ci:`-only push produces no version
bump, so no tag, so no deploy — that's expected, not a failure. In particular
a `ci:` commit touching the deploy workflow itself will never deploy itself;
use the manual run below to test it.

### Testing a deploy without cutting a release

`deploy.yml` also has a `workflow_dispatch` trigger, so the whole path
(tailnet join → Tailscale SSH → `uv tool install` → `systemctl restart` →
`GET /`) can be exercised on demand:

- **Actions → Deploy to OCI → Run workflow**, or
- `gh workflow run deploy.yml` (optionally `-f version=0.7.0`).

Leaving `version` blank installs whatever PyPI currently calls latest, which
is what you want when you're testing the deploy mechanics rather than a
specific release. This is the fastest way to check ACL, hostname, or OAuth
changes — it needs no version bump and no tag.

### One-time setup

**1. Policy file** — edit your tailnet policy file at
<https://login.tailscale.com/admin/acls/file> (a web textarea in the admin
console — nothing in this repo, and nothing on the VM). Merge the blocks below
into the corresponding top-level keys you already have; don't replace the
document.

Do this first: `tag:ci` won't be selectable when creating the OAuth client
until it exists in `tagOwners`, and step 2's `--advertise-tags` will be
rejected until `tag:gtd-server` does too.

> **Take all four blocks, not just the CI ones.** The moment you write an
> explicit `grants`/`ssh` section, Tailscale's default-allow-all stops
> applying — so a policy containing *only* the `tag:ci` rules below locks
> **you** out of your own tailnet: your phone can no longer reach the VM on
> `:8000`, and you can no longer SSH to it. The `autogroup:member` rules are
> what keep your own devices working.

```jsonc
{
  // Both tags must be declared before anything can use them.
  "tagOwners": {
    "tag:ci":         ["autogroup:admin"],
    "tag:gtd-server": ["autogroup:admin"],
  },

  "grants": [
    // Your own devices keep full access to the tailnet, including the VM's
    // port 8000. Omit this and the webapp becomes unreachable from your
    // phone the instant the policy is saved.
    {
      "src": ["autogroup:member"],
      "dst": ["*"],
      "ip":  ["*"],
    },
    // Restrict what CI may reach. Without this, a tagged node can talk to
    // anything your other ACLs allow — the point is that a compromised
    // workflow gets one box on one port, not the tailnet.
    {
      "src": ["tag:ci"],
      "dst": ["tag:gtd-server"],
      "ip":  ["tcp:22"],
    },
  ],

  "ssh": [
    // Your own SSH access to the VM. A tagged node has **no user owner**, so
    // it is not covered by `autogroup:self` — the dst must name the tag
    // explicitly or you can't SSH to your own server anymore.
    {
      "action": "check",
      "src":    ["autogroup:member"],
      "dst":    ["tag:gtd-server"],
      "users":  ["autogroup:nonroot", "ubuntu"],
    },
    // Permit Tailscale SSH from CI to the deploy user. This is what removes
    // the need for an SSH private key in GitHub — auth is the node's tailnet
    // identity. `accept` (not `check`) is required: `check` prompts for
    // browser re-auth, which a CI runner cannot satisfy.
    {
      "action": "accept",
      "src":    ["tag:ci"],
      "dst":    ["tag:gtd-server"],
      "users":  ["ubuntu"],
    },
  ],
}
```

**2. Tag the VM.** If it joined via a plain `sudo tailscale up`, it's owned by
your user account and the ACLs above won't match it. Re-auth it as a tagged
node with Tailscale SSH enabled:

```bash
sudo tailscale up --advertise-tags=tag:gtd-server --ssh
```

Note that tagged nodes don't expire, but they also lose their user
association — that's intended.

**3. OAuth client** — in the admin console, create one with **Keys → Auth Keys
→ Write** (the only scope needed; leave everything under *General* unchecked)
and tag it `tag:ci`.

**4. GitHub configuration** — under Settings → Environments, create an
environment named `oci`, then add:

| Kind | Name | Value |
|------|------|-------|
| Secret | `TAILSCALE_OAUTH_CLIENT_ID` | OAuth client ID |
| Secret | `TAILSCALE_OAUTH_CLIENT_SECRET` | OAuth client secret |
| Variable | `GTD_DEPLOY_HOST` | The VM's MagicDNS **hostname** (e.g. `gtd4me`) or Tailscale IP |

> **`GTD_DEPLOY_HOST` is the machine's name, not its ACL tag.** These are two
> unrelated namespaces and it is easy to conflate them: `tag:gtd-server` is
> what the ACL rules above match on, while `GTD_DEPLOY_HOST` is what
> `tailscale ssh ubuntu@<host>` resolves — the hostname shown in the machines
> list / `tailscale status`, e.g. `gtd4me`. Putting the tag here fails to
> resolve at the "Check the VM is reachable" step.

Put the two secrets in the **environment**, not at repo level. Repo-level
secrets do work — the tailnet join will succeed either way — but they're
readable by every workflow in the repo, which defeats the point of having an
environment. An `environment` also lets you add required reviewers or
restrict which branches may deploy.

### What the job does

Installs the exact version from the pushed tag (retrying while PyPI's CDN
catches up), restarts `gtd-api`, then verifies the unit is active and reports
the expected version — dumping the last 50 journal lines into the Actions log
if it isn't. Deploy failures are visible in the Actions UI rather than
silently leaving a stale app running.

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
- `instance_image_id` is pinned to a specific image OCID rather than looked
  up dynamically — see the comment in `main.tf` for why (a "latest" lookup
  would non-deterministically want to replace the running instance).
