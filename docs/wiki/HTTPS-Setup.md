# HTTPS Setup

BLE Pairing requires your Home Assistant to be accessible over **HTTPS**. This is a browser security requirement — Web Bluetooth only works on secure connections.

> **Not sure if you have HTTPS?** Open Home Assistant in your browser and look at the address bar. If it starts with `https://` you are already set and can skip this page.

---

## Option 1 — Nabu Casa (easiest)

[Nabu Casa](https://www.nabucasa.com/) is the official Home Assistant Cloud service. It automatically gives your HA instance a public HTTPS address.

1. Go to **Settings → Home Assistant Cloud**
2. Sign up or log in
3. Your instance gets a URL like `https://abc123.ui.nabu.casa`
4. Open the pairing page at that URL

**Cost:** ~$65/year. Also supports remote access and voice assistants.

---

## Option 2 — Let's Encrypt (free, built into HA)

Home Assistant can automatically obtain a free TLS certificate from Let's Encrypt.

**Requirements:**
- A domain name pointing to your home IP (e.g. via DuckDNS or No-IP)
- Port 80 or 443 open in your router

### With DuckDNS (recommended)

1. Sign up at [duckdns.org](https://www.duckdns.org/) — free
2. Create a subdomain, e.g. `myhome.duckdns.org`
3. Install the **DuckDNS** add-on in HA
4. Configure it with your token and subdomain
5. Enable **Let's Encrypt** in the DuckDNS add-on config
6. Restart HA — it will automatically get a certificate
7. Access HA at `https://myhome.duckdns.org:8123`

### With the HA Certificate Manager

If you already have a domain:

1. Go to **Settings → Add-ons → Add-on Store**
2. Install **Let's Encrypt**
3. Configure your domain and email
4. Restart HA

---

## Option 3 — Reverse proxy (nginx / Caddy)

If you run a reverse proxy in front of HA, configure it to terminate TLS:

### Caddy (automatic HTTPS)

```
your-domain.com {
    reverse_proxy homeassistant:8123
}
```

Caddy automatically obtains and renews a Let's Encrypt certificate.

### nginx

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://homeassistant:8123;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## Verify it works

1. Open `https://your-ha-domain` in Chrome or Edge
2. The padlock icon should appear in the address bar
3. Go to **Settings → Devices & Services → Add integration → Tuya Cloudless**
4. Choose **BLE Pairing** — the pairing page should open without HTTPS errors

---

## Troubleshooting

**"Web Bluetooth is not available"** — You are on HTTP (not HTTPS). The URL in your browser must start with `https://`.

**Certificate error / "Not secure"** — Your certificate is expired or self-signed. Let's Encrypt certificates expire after 90 days; the DuckDNS add-on renews them automatically.

**Port 443 blocked** — Check your router's port forwarding settings. Port 443 must be forwarded to your HA host.

---

## Related

- [How It Works](How-It-Works) — why HTTPS is required for BLE
- [Setup](Setup) — BLE pairing walkthrough
- [Troubleshooting](Troubleshooting) — other common issues
