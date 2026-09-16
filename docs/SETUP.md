# SETUP — binarythrottle.co.uk

Log of how this static site went live. No secrets included; any token-shaped value is shown as `<PLACEHOLDER>`.

**Stack:** GitHub → Vercel → Cloudflare (DNS). Hermes orchestrates the local side.
**Repo:** `BinaryThrottle/binarythrottle-website`
**Domain:** `binarythrottle.co.uk` (apex) + `www.binarythrottle.co.uk`
**Live URL:** `https://www.binarythrottle.co.uk/`

Each step ends with a one-line **WHY** so future-me remembers the chain.

---

## 1. Prerequisites

Before touching anything:

- **Domain on Cloudflare.** `binarythrottle.co.uk` already pointing at the CF nameservers:
  - `tate.ns.cloudflare.com`
  - `zita.ns.cloudflare.com`
  - **WHY:** CF has to be the authoritative DNS to edit zone records through the API.
- **GitHub account** `BinaryThrottle` with `gh` CLI authenticated.
- **Vercel account** linked to the same GitHub org so the Vercel-for-GitHub integration can import the repo.
- **Hermes install on Linux** with the `cf` and `vercel` CLI shims in `PATH`.
  - **WHY:** the DNS-create step is scripted through `cf` against the CF API; doing it in the dashboard works too but isn't reproducible.
- **`~/.hermes/profiles/default/.env`** writable (this is where the CF token lands).

---

## 2. Repo creation

Empty repo, push content second (per user's preference — avoids the "Initial commit" race on first import).

```bash
# create empty public repo on GitHub
gh repo create BinaryThrottle/binarythrottle-website --public --confirm

# clone locally
gh repo clone BinaryThrottle/binarythrottle-website
cd binarythrottle-website
```

- **WHY empty first:** Vercel's "Import Git Repository" flow is cleanest when the repo has zero commits; you pick the framework on import and don't fight a pre-existing build artifact.

---

## 3. Initial site — commit `15eb07d`

Add three files at the repo root:

- `index.html` — the *"Short. Sharp. Ship."* landing page (dark-mode-first, single-file CSS, no JS, no build).
- `README.md` — short description + local preview (`python3 -m http.server 8000`).
- `.gitignore` — Node/Vercel/editor noise.

```bash
git add index.html README.md .gitignore
git commit -m "Initial site: index.html + README + .gitignore"
git push -u origin main   # → commit 15eb07d
```

- **WHY these three and nothing else:** the moment Vercel sees a commit on `main` it tries to build. Shipping only the static landing page (no `package.json`, no framework) makes the auto-detected framework "Other" succeed without configuration.

---

## 4. Vercel import — auto-deploy

In the Vercel dashboard:

1. **Add New… → Project** → Import `BinaryThrottle/binarythrottle-website`.
2. Framework Preset: **Other** (static).
3. Root directory: `./` (default).
4. Build command / Output directory: leave blank (no build step).
5. **Deploy**.

Vercel issues the default URL: **`https://binarythrottle-website.vercel.app/`** — HTTP 200 on first build, GitHub webhook installed automatically so every push redeploys.

- **WHY dashboard (not CLI):** a one-time import is the path of least surprise and the Vercel GitHub app handles the webhook. The CLI is fine later for env vars / alias management.

---

## 5. `vercel.json` — commit `63e2af3`

Adds:

- `cleanUrls: true` → drop `.html` extensions.
- `trailingSlash: false`.
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`).
- Apex→`www` redirect: `host == "binarythrottle.co.uk"` → `https://www.binarythrottle.co.uk` (permanent / 308-equivalent).

```jsonc
// vercel.json (committed)
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "cleanUrls": true,
  "trailingSlash": false,
  "headers": [ /* X-Content-Type-Options, X-Frame-Options, Referrer-Policy */ ],
  "redirects": [
    {
      "source": "/",
      "has": [{ "type": "host", "value": "binarythrottle.co.uk" }],
      "destination": "https://www.binarythrottle.co.uk",
      "permanent": true
    }
  ]
}
```

```bash
git add vercel.json
git commit -m "Add vercel.json: security headers + apex→www redirect"
git push origin main   # → commit 63e2af3
```

Vercel auto-rebuilds on push.

- **WHY this commit exists at all:** without the redirect rule, the apex and `www` would be two unrelated HTTPS origins with separate certs; redirecting to `www` keeps HSTS / cookies / canonical URL all in one place.

---

## 6. Cloudflare DNS — two records

Create a CF API token scoped to *this* zone only.

### 6a. Token (do this once)

1. <https://dash.cloudflare.com/profile/api-tokens> → **Create Token** → template **Edit zone DNS**.
2. Permissions: `Zone` → `DNS` → `Edit`.
3. Zone Resources: `Include` → `Specific zone` → `binarythrottle.co.uk`.
4. TTL: pick a sensible expiry.

Save the token into the local Hermes profile env file. **Do not paste it into chat, the shell history, or this repo.** Use a read-from-stdin sink so it never appears in `history`:

```bash
read -rs CF_TOKEN
printf 'CF_API_TOKEN="%s"\n' "$CF_TOKEN" >> ~/.hermes/profiles/default/.env
unset CF_TOKEN
```

- **WHY a per-zone scoped token:** if the token leaks it can only edit `binarythrottle.co.uk` — every other zone in the account stays untouched. Read from stdin so it never lands in `~/.bash_history`.

### 6b. Records (DNS-only, grey cloud)

Both records are **NOT** proxied through Cloudflare. The orange-cloud proxy would force all traffic through CF's edge and break Vercel's automatic Let's Encrypt DNS-01 issuance.

| Type | Name | Target | Proxied | TTL |
|------|------|--------|---------|-----|
| `CNAME` | `www` | `abe61092ef46a48d.vercel-dns-017.com` | `false` (DNS-only) | `1` (auto) |
| `A` | `@` | `76.76.21.21` | `false` (DNS-only) | `1` (auto) |

Create via the CF REST API (preferred — reproducible) or via the dashboard:

```bash
# CNAME for www — full DNS record value from Vercel
curl -X POST "https://api.cloudflare.com/client/v4/zones/<ZONE_ID>/dns_records" \
  -H "Authorization: Bearer <CF_API_TOKEN>" \
  -H "Content-Type: application/json" \
  --data '{
    "type": "CNAME",
    "name": "www",
    "content": "abe61092ef46a48d.vercel-dns-017.com",
    "proxied": false,
    "ttl": 1,
    "comment": "Vercel project-specific target (expanding IP range)"
  }'

# A for apex — legacy anycast, still documented as supported
curl -X POST "https://api.cloudflare.com/client/v4/zones/<ZONE_ID>/dns_records" \
  -H "Authorization: Bearer <CF_API_TOKEN>" \
  -H "Content-Type: application/json" \
  --data '{
    "type": "A",
    "name": "@",
    "content": "76.76.21.21",
    "proxied": false,
    "ttl": 1,
    "comment": "Vercel legacy anycast apex"
  }'
```

Notes baked into this setup:

- **`<ZONE_ID>`** is the Cloudflare zone UUID — visible in the dashboard URL on the zone overview page. Not a secret.
- **`<CF_API_TOKEN>`** is the Edit-zone-DNS scoped token from §6a.
- The CNAME target `abe61092ef46a48d.vercel-dns-017.com` is the **project-specific** Vercel value Vercel emailed about ("we're expanding our IP range — point your records at this instead"). It is documented as public per Vercel's DNS docs.
- The legacy targets `cname.vercel-dns.com` and `76.76.21.21` continue to work — both are documented as supported. The `A` apex record uses the legacy anycast IP because apex records can't be a CNAME (RFC 1034).
- **WHY both DNS-only (grey cloud):** the Vercel integration issues a Let's Encrypt cert via DNS-01 *by talking directly to CF on your behalf* — that path requires CF to actually answer DNS authoritatively for the zone. Proxying through CF's edge breaks the chain.

---

## 7. Vercel domain attach (dashboard, can't be scripted against this project's free tier easily)

In Vercel: **Project → Settings → Domains**:

1. **Add `www.binarythrottle.co.uk`.** Vercel shows the recommended record (the `abe61092…vercel-dns-017.com` CNAME). Confirm.
2. Wait for the green "Valid Configuration" badge.
3. **Add `binarythrottle.co.uk`** (apex). Same flow — Vercel shows the A-record target.
4. Wait for valid.

Vercel issues the **Let's Encrypt** cert automatically via DNS-01 (it creates a `_acme-challenge` TXT in CF on demand, then removes it). Cert valid 90 days, auto-renews.

- **WHY this step happens *after* the CF records exist:** if you add the domain in Vercel first it sits in "Invalid Configuration" until the records propagate. Adding records first means by the time you attach the domain, validation succeeds on the first check.

---

## 8. End state

```text
$ dig +short www.binarythrottle.co.uk          CNAME
abe61092ef46a48d.vercel-dns-017.com.

$ dig +short binarythrottle.co.uk              A
76.76.21.21

$ curl -sI https://www.binarythrottle.co.uk/
HTTP/2 200
server: Vercel
strict-transport-security: max-age=63072000

$ curl -sI https://binarythrottle.co.uk/
HTTP/2 308
location: https://www.binarythrottle.co.uk
```

- `https://www.binarythrottle.co.uk/` → **HTTP/2 200**, LE cert, HSTS on.
- `https://binarythrottle.co.uk/` → **HTTP/2 308** → `https://www.binarythrottle.co.uk`.

---

## 9. Housekeeping

Two per-host scripts live in `/home/jarvis/`, **not** in this repo:

- `cleanup_env.sh` — strips duplicate `TELEGRAM_BOT_TOKEN=` lines (14 were piled up) and the deprecated `MESSAGING_CWD` / `TERMINAL_CWD` keys from every `~/.hermes/profiles/*/.env`.
- `remove_telegram.sh` — companion: drops the Telegram bot config entirely if you're disabling that integration.

Run once after upgrading Hermes when the new env-loader complains about duplicates:

```bash
bash ~/cleanup_env.sh
```

- **WHY outside the repo:** they're host-specific maintenance, not part of the site's source. Putting them here would pollute the git history with one-machine cleanup noise.

---

## Re-running on a new domain

Quick-reference checklist (assumes the repo + Vercel project already exist):

1. **Cloudflare side**: domain already on CF nameservers; otherwise add it in the dashboard first.
2. **CF API token**: `dash.cloudflare.com/profile/api-tokens` → *Edit zone DNS* template → scope to the new zone only → stash in `~/.hermes/profiles/default/.env` with the read-stdin sink.
3. **DNS records** (DNS-only, grey cloud):
   - `CNAME www → <vercel-project-target>.vercel-dns-017.com`
   - `A @ → 76.76.21.21`
4. **Vercel domain attach**: Project → Settings → Domains → add `www` first, then apex. Wait for "Valid Configuration" between each.
5. **SSL**: nothing to do — Vercel issues LE via DNS-01 once the records propagate.
6. **Apex→www redirect**: if not already in `vercel.json`, add the `redirects[]` entry scoped to `has: host == "<apex>"`.
7. **Verify**: `dig +short <host> CNAME`, `dig +short <apex> A`, `curl -sI https://www.<domain>/`.

That's the whole chain. Total hands-on time once you have the token: ~3 minutes.