# Running Brewfather Reports on a public web server

The built-in server (`app/server.py`) speaks plain HTTP. On the internet, run it behind a web server that
handles HTTPS, and let it listen only on the machine itself (127.0.0.1). Each installation serves one
Brewfather account.

## 1. Copy and start it

```
unzip brewfather-reports-1.0.0.zip -d /opt
cd /opt/brewfather-reports-1.0.0
python3 app/server.py --proxy --port 8765
```

`--proxy` tells it to trust the `X-Forwarded-For` and `X-Forwarded-Proto` headers from your web server,
so login throttling sees real visitor addresses and the sign-in cookie is marked Secure. Only use
`--proxy` when a proxy is in front of it.

The first start prints a **setup code**. Open `https://your-site/admin.html` straight away and finish
setup, because whoever enters the code first sets the admin password.

## 2. HTTPS in front

**Caddy** (gets certificates automatically), `/etc/caddy/Caddyfile`:

```
reports.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

**nginx**, inside a `server { listen 443 ssl; ... }` block that already has your certificate:

```
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

To serve it under a sub-path such as `/brewing/`, use `location /brewing/ { proxy_pass http://127.0.0.1:8765/; ... }`.
Every link in the pages is relative, except the Admin page's calls to `/api/...`, so a sub-path needs a second
`location /api/` block, or a sub-domain instead (simpler).

**IIS** (Windows Server): install URL Rewrite and Application Request Routing, then add a reverse-proxy rule
to `http://127.0.0.1:8765` and turn on HTTPS for the site.

## 3. Keep it running

**Linux (systemd)**, `/etc/systemd/system/brewfather-reports.service`:

```
[Unit]
Description=Brewfather Reports
After=network-online.target

[Service]
WorkingDirectory=/opt/brewfather-reports-1.0.0
ExecStart=/usr/bin/python3 app/server.py --proxy --port 8765
User=brewreports
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now brewfather-reports`. To see the setup code, run `journalctl -u brewfather-reports`.
Run it as its own user, and make sure only that user can read `.env` and `data/`.

**Windows:** create a Task Scheduler task "At startup" that runs `python app/server.py --proxy` in the folder, or use NSSM
to install it as a service.

## 4. Updating to a new version

1. Stop the server.
2. Unzip the new version over the old folder. The package doesn't contain `.env`, `data/`, `config/exclude.txt` or
   `config/families.txt`, so yours are kept. It does contain `config/hop_flavours_extra.json`, so if you have edited that file,
   back it up first.
3. Start the server again, then click **Rebuild pages from saved data** on the Admin page.

## Security notes

- Reports are private by default (Admin › Settings › "Reports visible without sign-in").
- The admin password is stored as a salted PBKDF2 hash in `data/admin.json`. To reset it, stop the server, delete
  that file and start again. You will get a new setup code, and the saved API key is kept.
- After 5 wrong passwords, sign-in from that address is blocked for 5 minutes.
- Sessions last 12 hours from the last visit and use an HttpOnly, SameSite=Strict cookie.
- Only the files in `output/` are served, and only `.html`, `.csv`, `.png` and `.svg` files.
