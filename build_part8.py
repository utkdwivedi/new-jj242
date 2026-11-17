# -*- coding: utf-8 -*-
"""Part 8: backup, DR, documentation."""
import build_part7  # noqa
from build_review import *  # noqa

# =====================================================================
H("8. Backup and disaster recovery", 1)

H("8.1 The 3-2-1 story covers the NAS, not the cluster", 2)
RICH("The README's backup section is genuine and well-chosen: TrueNAS ZFS mirror as "
     "primary, an external SSD as secondary, Backblaze B2 via TrueNAS Cloud Sync as "
     "offsite, with GitHub as a fourth copy of the configs. That is a real 3-2-1 strategy "
     "for *file data*.")

RICH("What it does not cover is everything that makes the cluster a cluster:")
table(
    ["Asset", "Where it lives now", "Covered by README's 3-2-1?", "Recoverable?"],
    [["Media library", "TrueNAS `/mnt/data/media`", "**yes**", "yes"],
     ["Jellyfin config", "`nfs-config` → TrueNAS `/mnt/data/config`", "**yes**", "yes"],
     ["Karakeep / Meilisearch data", "`nfs-config` → TrueNAS `/mnt/data/config`",
      "**yes**", "yes — until SQLite-on-NFS corrupts it"],
     ["SilverBullet notes", "`nfs-config` → TrueNAS `/mnt/data/config`", "**yes**",
      "yes (same caveat)"],
     ["Grafana dashboards", "PVC → NFS", "**yes**", "yes — but nothing provisions them, "
      "so a restore restores an empty Grafana"],
     ["**K3s cluster state (etcd)**", "3 server nodes' local disk", "**no**",
      "**no — this is the gap**"],
     ["**Kubernetes Secrets**", "etcd (encrypted at rest? unknown)", "**no**",
      "partially — the SOPS *source* is in git, but the age key is in Ansible Vault"],
     ["**SOPS age private key**", "Ansible Vault", "**no**",
      "only if `.vault_pass` survives"],
     ["**`.vault_pass`**", "your workstation, location unstated", "**no**",
      "**single point of failure for the entire cluster**"],
     ["Cloudflare tunnel config", "Cloudflare dashboard", "n/a",
      "yes — but not reproducible from the repo"]],
    widths=[1.5, 1.8, 1.4, 2.1], font=8.5)

RICH("**The chain that matters.** Read the last three rows together: to rebuild this "
     "cluster you need the SOPS age private key; the age private key is inside Ansible "
     "Vault; Ansible Vault is unlocked by `.vault_pass`; `.vault_pass` is nowhere in any "
     "backup strategy. **One unbacked-up file is the root of trust for every secret in "
     "the cluster.** That is the most important sentence in this section.")

H("8.2 Add K3s etcd snapshots", 2)
RICH("K3s has built-in etcd snapshotting and you are not using it. With 3 server nodes "
     "you would be running embedded etcd, so this is configuration, not new software:")
CODE('''
# inventory/group_vars/all/defaults.yaml
k3s_server_args: >-
  --write-kubeconfig-mode 644
  --etcd-snapshot-schedule-cron="0 */6 * * *"
  --etcd-snapshot-retention=10
  --etcd-snapshots-path=/var/lib/rancher/k3s/server/db/snapshots
{-# and for offsite, S3-compatible support is built in: #}
  --etcd-s3
  --etcd-s3-endpoint=s3.us-west-004.backblazeb2.com
  --etcd-s3-bucket=homelab-etcd
  --etcd-s3-access-key=${ETCD_S3_KEY}
  --etcd-s3-secret-key=${ETCD_S3_SECRET}
''')

RICH("Snapshots alone are not a backup; an untested snapshot is a hope. Add the "
     "verification step and put it on a schedule:")
CODE('''
# monthly, on staging — NOT on production
k3s etcd-snapshot list
k3s etcd-snapshot save --name manual-$(date +%F)
# restore test:
systemctl stop k3s
k3s server --cluster-reset --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/<name>
systemctl start k3s
kubectl get nodes && flux get kustomizations
''')

H("8.3 Back up the PVCs, not just the NAS", 2)
RICH("The NAS volume is backed up, but the *Kubernetes objects* that bind to it are not, "
     "and neither is the mapping between them. `Velero` (or `Kopia` for PVC-only) closes "
     "this by capturing cluster resources plus volume data into object storage:")
CODE('''
# Velero with the B2 S3-compatible endpoint, scheduled daily, 30-day retention.
velero schedule create daily --schedule="0 3 * * *" --ttl 720h \\
  --include-namespaces jellyfin,karakeep,silverbullet,monitoring

# Verify, and record the output in docs/disaster-recovery.md:
velero backup get
velero backup describe daily-<timestamp> --details
velero restore create --from-backup daily-<timestamp> --dry-run
''')

NOTE("**If Velero is more machinery than you want**, the acceptable minimum is: (a) K3s "
     "etcd snapshots to B2, (b) a documented `kubectl get -A -o yaml > cluster-state.yaml` "
     "dump into the repo's backup bucket, and (c) a `make backup-test` target that restores "
     "into a scratch namespace. Velero's advantage is point-in-time consistency between "
     "objects and volumes. Its cost is a CRD, a controller and an S3 credential. Pick "
     "deliberately and write down which you picked.")

H("8.4 Write the disaster-recovery runbook", 2)
RICH("The question the README does not answer is *\"the ThinkPad dies, now what?\"* Until "
     "it is written down, the answer is improvisation under stress. The runbook needs "
     "five things:")
B("**The rebuild order**, as exact commands: install Fedora → restore `~/.ssh` from "
  "backup → `source scripts/env.sh` → `make provision-host` → `make apply` → "
  "`make provision` → `flux get kustomizations`.")
B("**Where the age key lives** and how to retrieve it. Name the physical location and "
  "the password manager entry. This is the step that fails at 2 a.m.")
B("**What is restorable and what is not**, with expected times: NAS data (hours, "
  "limited by B2 egress), cluster state (minutes from an etcd snapshot), PVCs (hours via "
  "Velero), service config (seconds from git).")
B("**The order of dependency**: infrastructure before services before monitoring; the "
  "Flux `dependsOn` graph already encodes this — document it so you know what to expect.")
B("**A recovery-time objective and a recovery-point objective**, even rough ones: *\"I "
  "accept up to 6 hours of downtime and up to 6 hours of lost metrics.\"* Writing the "
  "number down is what turns a wish into a target, and it tells you whether your snapshot "
  "interval is right.")

H("8.5 An honest HA assessment", 2)
RICH("The earlier review flagged this and the manifests confirm the shape: all VMs run on "
     "one physical host. The inventory shows one `k3s_server` and three `k3s_agents` in "
     "production, so you have a **single control-plane node** — not a 3-server HA etcd "
     "quorum. That is a perfectly reasonable design; the problem is only if the README "
     "implies otherwise. It says *\"production-grade homelab cluster\"* and *\"Production "
     "Host IP\"*, which invites an assumption of redundancy that does not exist.")
table(
    ["Layer", "Redundancy", "Statement to put in the README"],
    [["Physical host", "**none** — one ThinkPad T14",
      "\"Single physical host. Host failure = full outage.\""],
     ["Control plane", "**none** — 1 server node",
      "\"One K3s server with embedded etcd. No quorum; no control-plane HA.\""],
     ["Workers", "3 agents on the same host",
      "\"Workers are redundant against *node* failure, not against *host* failure.\""],
     ["Storage", "ZFS mirror + offsite",
      "\"Data survives disk failure and site loss. The cluster does not.\""],
     ["Network", "**none** — single bridge `br0`, single NIC",
      "\"Single uplink over one bridge. No bonded NIC.\""],
     ["Power", "**unknown / none**",
      "\"No UPS. Power loss is an ungraceful shutdown.\""]],
    widths=[1.2, 1.6, 3.9], font=8.5)

doc.add_page_break()

# =====================================================================
H("9. Documentation, Makefile and script polish", 1)

H("9.1 Restructure the README into a front door plus `docs/`", 2)
RICH("The README is 12.3 KB and carries everything: architecture, prerequisites, "
     "structure diagram, four tooling tables, backup strategy, licence. It has grown into "
     "a document that is too long to skim and too shallow to operate from. Split it:")
table(
    ["File", "Contents", "Why it exists"],
    [["`README.md`", "Badges · one-paragraph what-this-is · architecture diagram · "
      "quick start (5 commands) · link table to `docs/`",
      "The front door. Under one screen. A reader decides in 30 seconds whether to "
      "continue."],
     ["`docs/architecture.md`", "The Mermaid diagram · node inventory · storage topology · "
      "network design · Cilium mode · what talks to what",
      "The thing you re-read when you change something."],
     ["`docs/bootstrap.md`", "Zero to cluster, in exact order, with the expected output "
      "of each step", "The answer to \"I wiped the host\"."],
     ["`docs/disaster-recovery.md`", "The §8.4 runbook", "The answer to \"the host died\"."],
     ["`docs/secrets.md`", "SOPS + age + Ansible Vault: how the three relate, where the "
      "keys live, how to rotate", "Currently tribal knowledge across three tools."],
     ["`docs/networking.md`", "Cloudflare Tunnel config, ingress model, TLS termination "
      "point, LB pool allocation, Tailscale scope", "Encodes the decisions that are "
      "currently only in the Cloudflare dashboard."],
     ["`docs/adding-a-service.md`", "The checklist: base manifests → overlay → namespace → "
      "NetworkPolicy → PVC class → SOPS secret → ingress → backup", "Makes the next "
      "service consistent with these ten."],
     ["`docs/adr/`", "One short Markdown file per decision", "See §9.3 — this is how the "
      "reasoning survives you."],
     ["`docs/roadmap.md`", "The known-limitations list from §9.5, as a plan",
      "Turns the gaps in this report into visible work."],
     ["Per-directory `README.md`", "Short: what belongs here, naming conventions, how to "
      "add to it", "Cheap, and it stops the `base/` duplication from recurring."]],
    widths=[1.5, 2.6, 2.6], font=8.5)

H("9.2 Complete the prerequisites table", 2)
RICH("The current table lists four IP addresses. It does not list a single tool. A reader "
     "cannot start. Replace it with the tools and their versions — which `.tool-versions` "
     "from §6.5 then enforces:")
table(
    ["Tool", "Version", "Used for", "Required by"],
    [["terraform", ">= 1.13", "VM provisioning", "`make init|plan|apply`"],
     ["ansible", ">= 11", "host and node configuration", "`make provision*`"],
     ["ansible-lint", ">= 25", "playbook checks", "CI"],
     ["kubectl", ">= 1.34", "cluster interaction", "everything"],
     ["kustomize", ">= 5.7", "building overlays", "CI, local verification"],
     ["flux", ">= 2.7", "bootstrap, reconcile, build", "`make bootstrap-flux`"],
     ["sops", ">= 3.10", "editing encrypted secrets", "secret changes"],
     ["age", ">= 1.2", "key generation", "one-time setup"],
     ["helm", ">= 3.19", "chart inspection", "debugging HelmReleases"],
     ["yq", ">= 4.48", "reading config/hosts.yaml", "`env.sh`, CI"],
     ["gitleaks", ">= 8.30", "secret scanning", "pre-commit, CI"],
     ["kubeconform", ">= 0.7", "schema validation", "CI"]],
    widths=[1.15, 1.0, 1.95, 2.7], font=8.5)

H("9.3 Write ADRs — the highest-value documentation for the smallest effort", 2)
RICH("The README states *what* the stack contains. It never states *why*. That matters "
     "because this review has raised several questions that only you can answer, and in a "
     "year you will not remember the answers either:")
B("Why hand-roll Prometheus instead of `kube-prometheus-stack`?")
B("Why NFS for the databases when the failure mode is known — or was it not known?")
B("Why `Audit` mode for Kyverno — a deliberate staging phase, or an unfinished policy?")
B("Why three agents plus one server rather than three servers?")
B("Why no NetworkPolicies — Cilium's policy engine is installed but unused.")
B("Why Tailscale is installed on the host (`roles/tailscale`) but no admin surface uses "
  "it?")
RICH("Each of those is a five-line file. The format is deliberately boring: "
     "**Context · Decision · Consequences**.")
CODE('''
# docs/adr/0007-nfs-considered-harmful-for-databases.md
## Status
Accepted — 2026-10-20

## Context
Karakeep (SQLite), Meilisearch (LMDB) and SilverBullet all stored state on the
`nfs-config` class, backed by TrueNAS over NFS. SQLite and LMDB both require
reliable POSIX advisory locking and local fsync semantics. NFS provides neither.

## Decision
Move all single-writer datastores to `local-path`. Keep NFS for the Jellyfin
media library and read-mostly config.

## Consequences
+ No corruption risk from lock semantics.
- Data is pinned to the node; a node loss requires a PVC restore.
- Mitigated by the daily Velero schedule (§8.3).
''')

H("9.4 Make the Makefile complete and safe", 2)
RICH("Three problems beyond the broken paths: the help output is incomplete, "
     "`auto-rebuild` is unguarded, and a fixed `sleep 15` assumes a boot time.")

table(
    ["Issue", "Current", "Fix"],
    [["`help` omits targets",
      "`provision-host`, `get-ips` and `auto-rebuild` all exist but are not listed. "
      "`help` is not even the default goal, so a bare `make` prints "
      "`make: *** No targets.  Stop.`",
      "Only list generated from `##` comments, and make `help` the default goal. Then it "
      "cannot drift again."],
     ["`auto-rebuild` runs `terraform destroy -auto-approve`",
      "No confirmation, and `ENV` is not checked. `make auto-rebuild ENV=production` "
      "destroys the production fleet without a prompt. In a repo where `ENV` defaults to "
      "`production`, one typo is an outage.",
      "Require `CONFIRM=yes`, and refuse outright when `ENV=production`."],
     ["`sleep 15`",
      "`rebuild` sleeps a fixed 15 s, then pings. Boot time varies with host load and "
      "whether cloud-init has work to do. Too short and `ping` fails spuriously; too long "
      "and every rebuild wastes time.",
      "Poll until the hosts answer, with a timeout."]],
    widths=[1.4, 2.6, 2.7], font=8.5)

CODE('''
.DEFAULT_GOAL := help
ENV ?= production

help: ## Show this help
\t@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \\
\t  | awk 'BEGIN{FS=":.*?## "}{printf "  \\033[36m%-18s\\033[0m %s\\n", $$1, $$2}'

validate: ## Fail if any referenced path is missing
\t@bash scripts/check-paths.sh

lint: ## Lint YAML, Ansible and the Makefile
\tyamllint -c .yamllint.yaml .
\tansible-lint inventory playbooks roles
\tpre-commit run --all-files

bootstrap-flux: ## Bootstrap Flux into the current cluster (idempotent)
\tflux bootstrap github --owner=utkdwivedi --repository=homelab \\
\t  --branch=main --path=clusters/$(ENV) --personal

kubeconfig: ## Fetch the kubeconfig from the server node
\t@ssh $(K3S_NODE) 'sudo cat /etc/rancher/k3s/k3s.yaml' \\
\t  | sed "s/127.0.0.1/$(HOST_IP)/" > $(ENV)_kubeconfig.yaml
\t@echo "wrote $(ENV)_kubeconfig.yaml (gitignored)"

wait-ready: ## Wait for all VMs to answer, up to 5 minutes
\t@for i in $$(seq 1 60); do \\
\t  ansible everything $(ANSIBLE_OPTS) -m ping -o >/dev/null 2>&1 && exit 0; \\
\t  sleep 5; \\
\tdone; echo "timed out waiting for hosts"; exit 1

rebuild: destroy apply wait-ready ## Destroy, rebuild and wait for the fleet
\t@$(MAKE) ping

auto-rebuild: ## DESTRUCTIVE: destroy and rebuild, then provision. CONFIRM=yes required
\t@test "$(CONFIRM)" = "yes" || { echo "refusing: pass CONFIRM=yes"; exit 1; }
\t@test "$(ENV)" != "production" || { echo "refusing: never auto-rebuild production"; exit 1; }
\tcd $(TERRAFORM_DIR) && terraform destroy -auto-approve
\tcd $(TERRAFORM_DIR) && terraform apply  -auto-approve
\t@$(MAKE) wait-ready ENV=$(ENV)
\tcd $(TERRAFORM_DIR) && terraform apply  -auto-approve   # pick up IPs after qemu-agent
\t@$(MAKE) provision ENV=$(ENV)
\t@$(MAKE) get-ips   ENV=$(ENV)
''')

H("9.5 Write down the limitations — it converts gaps into a plan", 2)
RICH("A README that says *\"production-grade\"* invites scrutiny it cannot survive. One "
     "that says *\"here is what is not done yet\"* reads as engineering maturity, because "
     "it demonstrates you know the shape of your own system. Suggested content, drawn "
     "directly from this review:")
B("No hardware HA — single host, single control-plane node, no UPS.")
B("No log aggregation or tracing.")
B("Restore has not been tested end-to-end (until §8.4 is done and dated).")
B("Kyverno policies are in Audit mode; nothing is enforced.")
B("No NetworkPolicies — pod-to-pod traffic is unrestricted inside the cluster.")
B("Staging and production are currently identical; promotion is manual.")
B("Cilium upgrade outstanding — pinned at an EOL release (§4.1).")
B("TLS termination is configured outside this repository (Cloudflare dashboard).")
B("Headless Chrome runs `--no-sandbox`; mitigated by a NetworkPolicy (§5.3).")
RICH("Then add the mirror image: **what is verified working**, with a date. \"Restored a "
     "Karakeep PVC from a Velero backup into staging on 2026-11-02\" is worth more than "
     "any amount of architecture prose, because it is the only statement that proves the "
     "system is real.")

H("9.6 Add screenshots", 2)
RICH("Nothing in a 12 KB README shows the system running. Three images change how the "
     "repository is read:")
B("`docs/images/flux-get-kustomizations.png` — five Kustomizations, all `Ready=True`, "
  "with real reconcile ages. This is the single most credible screenshot you can publish.")
B("`docs/images/grafana-dashboard.png` — the homelab dashboard with live data. It proves "
  "§5.4 and §7.2 are done.")
B("`docs/images/service-behind-tunnel.png` — a browser at a real "
  "`*.utkdwivedi.com` hostname with the padlock, proving the ingress path end to end.")

doc.add_page_break()
