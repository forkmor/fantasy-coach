# Deploy Fieldhouse to fantasy-coach.tech

## Hosting requirement

Fieldhouse requires an always-running FastAPI process, SQLite persistence, and an
in-process scheduler. Hostinger Business/Cloud web hosting and its `public_html`
file manager cannot run that workload continuously. Use a Hostinger VPS. Do not
upload this package into `public_html`.

The recommended VPS starting point is Ubuntu 24.04 with at least 2 GB RAM. Select
Hostinger's Docker template if it is available. The deployment runs exactly one
application worker because the scheduler and team mutation lock are process-local.

If the VPS already hosts nginx sites, use `compose.nginx.yaml` instead of the
default Caddy stack. This publishes Fieldhouse only on `127.0.0.1:8765` and leaves
the existing nginx services intact.

## 1. Point the domain at the VPS

In Hostinger DNS:

1. Replace the root `A` record for `fantasy-coach.tech` with the VPS IPv4 address.
2. Remove conflicting root `A` records.
3. Add an `AAAA` record only if the VPS has working public IPv6.
4. Do not proxy the domain through another service during initial certificate
   issuance.

DNS may take time to propagate. Confirm that `fantasy-coach.tech` resolves to the
VPS before starting Caddy.

## 2. Prepare the VPS

Connect with SSH as a non-root sudo user. Install Docker Engine and the Docker
Compose plugin from Docker's official Ubuntu repository if the Hostinger image
does not already include them. Allow inbound TCP ports 22, 80, and 443 and UDP
port 443 in the Hostinger firewall. Restrict SSH to your own IP when practical.

Copy and extract `fieldhouse-hostinger-vps.zip` into a private directory such as:

```sh
mkdir -p "$HOME/fieldhouse"
cd "$HOME/fieldhouse"
unzip /path/to/fieldhouse-hostinger-vps.zip
chmod +x deploy/*.sh
```

The source and `.env` must not be placed under a public web root.

## 3. Generate production secrets

Run:

```sh
python3 deploy/generate-config.py
```

Enter an email for Let's Encrypt notices and a unique league password of at least
14 characters. Share that password only with this league's managers. The script creates:

- A scrypt password verifier. The cleartext password is not stored.
- A random 256-bit AES-GCM key used to encrypt provider keys and ESPN cookies.

The generated `.env` is mode `0600`. Back it up in a password manager or encrypted
offline storage. Losing `HARNESS_VAULT_KEY` makes stored credentials unrecoverable.
Anyone who obtains both `.env` and the database can decrypt those credentials.

Never upload `.env`, an ESPN cookie, a provider API key, or a database backup to
`public_html`, source control, chat, or a public file-sharing service.

## 4. Start the service

From the extracted directory:

```sh
sh deploy/install.sh
```

This validates the Compose configuration, builds the frontend and backend images,
and starts Fieldhouse and Caddy with `restart: unless-stopped`. Caddy obtains and
renews the HTTPS certificate automatically.

Check status:

```sh
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 caddy
curl https://fantasy-coach.tech/api/health
```

The health response should report `"status":"ok"` and `"deployment":"public"`.

### Existing nginx server

On a VPS where nginx and Certbot already serve other domains:

```sh
docker compose -f compose.nginx.yaml up -d --build
sudo cp deploy/fieldhouse.nginx /etc/nginx/sites-available/fieldhouse
sudo ln -s /etc/nginx/sites-available/fieldhouse /etc/nginx/sites-enabled/fieldhouse
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d fantasy-coach.tech --redirect
```

This does not modify the other site's upstream application or files. Inspect the
generated nginx configuration and run `sudo nginx -t` before every reload.

## 5. Complete private setup

1. Open `https://fantasy-coach.tech`.
2. Sign in with the league password created in step 3.
3. Connect fresh `espn_s2` and `SWID` values from your own ESPN account. Fieldhouse
   verifies which team that account owns before opening a team workspace.
4. Use the Grok or Gemini connection link, create an API key, and save it privately.
   Browser-assisted ESPN sign-in is intentionally disabled on a headless VPS; do
   not enter an ESPN password into Fieldhouse.
5. Test both provider and ESPN connections.
6. Review and save Coach settings.
7. Keep execution in Dry run until proposals look correct.
8. Enable Deep player research if desired.
9. Enable a check-in schedule only after confirming its interval and policy.
10. Enable Live mode only for the server-listed verified capabilities.

The service and scheduler continue running after SSH disconnects and return after
a normal VPS reboot. Scheduled work cannot run during VPS downtime or an upstream
ESPN/provider outage.

## Updates and backups

Before updates:

```sh
COMPOSE_FILE=compose.nginx.yaml sh deploy/backup.sh
```

This creates consistent SQLite backups for every connected team under a timestamped
directory in `deploy/backups/`. Copy the whole directory off the VPS and separately
protect `.env`.

After replacing the source with a new package:

```sh
COMPOSE_FILE=compose.nginx.yaml sh deploy/update.sh
```

The named Docker volume preserves the active database across image rebuilds.

## Security and operational notes

- Only `https://fantasy-coach.tech` is accepted as a browser mutation origin.
- Authentication cookies are Secure, HttpOnly, SameSite=Strict, and expire after
  12 hours. Application restarts invalidate existing sessions.
- Caddy supplies HTTPS; the application adds HSTS, CSP, frame denial, no-sniff,
  and no-referrer headers.
- Provider keys and ESPN cookies are AES-GCM encrypted at rest.
- The application container runs as a non-root user with all Linux capabilities
  dropped and a read-only root filesystem.
- Only Caddy publishes ports. The FastAPI port is private to the Compose network.
- Mutation retries remain disabled after ambiguous ESPN outcomes.
- Review `docker compose logs` and the in-app activity history regularly.
- Install unattended security updates on the VPS and keep Docker current.

The shared league password gates the site, while verified ESPN cookies bind each
browser session to one team. Every team's credentials, settings, schedule, runs,
and actions are stored in a separate database. An authenticator-app second factor
or an identity-aware access proxy would provide stronger protection if the site
becomes a high-value target.
