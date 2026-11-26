# Running Brewfather Reports on a public web server

The built-in server (`app/server.py`) speaks plain HTTP. On the internet, run it behind a web server that
handles HTTPS, and let it listen only on the machine itself (127.0.0.1). Each installation serves one
Brewfather account.

## 1. Copy and start it

```
unzip brewfather-reports-1.0.0.zip -d /opt
cd /opt/brewfather-reports-1.0.0
python3 app/server.py --proxy --port 8642
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
    reverse_proxy 127.0.0.1:8642
}
```

**nginx**, inside a `server { listen 443 ssl; ... }` block that already has your certificate:

```
location / {
    proxy_pass http://127.0.0.1:8642;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

To serve it under a sub-path such as `/brewing/`, use `location /brewing/ { proxy_pass http://127.0.0.1:8642/; ... }`.
Every link in the pages is relative, except the Admin page's calls to `/api/...`, so a sub-path needs a second
`location /api/` block, or a sub-domain instead (simpler).

**IIS** (Windows Server): install URL Rewrite and Application Request Routing, then add a reverse-proxy rule
to `http://127.0.0.1:8642` and turn on HTTPS for the site.

## 3. Keep it running

**Linux (systemd)**, `/etc/systemd/system/brewfather-reports.service`:

```
[Unit]
Description=Brewfather Reports
After=network-online.target

[Service]
WorkingDirectory=/opt/brewfather-reports-1.0.0
ExecStart=/usr/bin/python3 app/server.py --proxy --port 8642
User=brewreports
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now brewfather-reports`. To see the setup code, run `journalctl -u brewfather-reports`.
Run it as its own user, and make sure only that user can read `.env` and `data/`.

**Windows:** create a Task Scheduler task "At startup" that runs `python app/server.py --proxy` in the folder, or use NSSM
to install it as a service.

## On a Plesk server (Linux)

This runs happily beside other sites, such as WordPress, on a Plesk VPS. The app is one small Python process
(about 30–50 MB of memory) that listens only on the server itself. Plesk's nginx sends one subdomain to it,
and nothing else on the server changes. You need SSH access as root (or sudo).

The steps use `yourdomain.com` for the Plesk subscription, `brew.yourdomain.com` for the new subdomain and
`yoursysuser` for the subscription's system user (Plesk › the subscription › **Hosting & DNS › Hosting** shows it).
Replace them with your own.

### 1. Check Python

```
python3 --version
```

You need 3.9 or newer. Ubuntu 22.04/24.04 and Debian 12 have it. AlmaLinux/Rocky 8 ships 3.6: install a newer one
with `dnf install python3.11`, and use `/usr/bin/python3.11` wherever these steps say `/usr/bin/python3`.
Nothing else needs installing; the app uses only Python's standard library.

### 2. Create the subdomain

In Plesk, **Add Subdomain** › `brew` under `yourdomain.com`. Then open the subdomain's **SSL/TLS Certificates** and
get a free Let's Encrypt certificate, with "Redirect from HTTP to HTTPS" turned on.

Use a subdomain rather than a folder under an existing site: the Admin page talks to the server at `/api/...`
from the site's root.

### 3. Put the app next to the website folder, not inside it

Install it in the subscription's folder but **outside** any `httpdocs` or subdomain document root. Inside a
document root, Apache could serve your `.env` (API key) and `data/` directly. Next to it, they stay private, and
Plesk's backups of the subscription include them.

From GitHub:

```
cd /var/www/vhosts/yourdomain.com
git clone https://github.com/samthinkbox/Brewfather-Reports.git brew-reports
```

or upload the package zip (`python tools/build_package.py`) with Plesk's File Manager and extract it to
`/var/www/vhosts/yourdomain.com/brew-reports`.

Then give it to the subscription's user and keep the private folders private:

```
chown -R yoursysuser:psacln /var/www/vhosts/yourdomain.com/brew-reports
chmod 750 /var/www/vhosts/yourdomain.com/brew-reports
```

### 4. Run it as a service

Copy [brew-reports.service](brew-reports.service) from this folder to `/etc/systemd/system/brew-reports.service`,
change `yoursysuser`, the folder and (if needed) the Python path and port, then:

```
systemctl daemon-reload
systemctl enable --now brew-reports
systemctl status brew-reports
```

It starts at boot and restarts if it stops. It listens only on 127.0.0.1, so its port is never open to the internet
and the Plesk firewall needs no change. Pick another port than 8642 if something already uses it
(`ss -ltn | grep 8642` shows nothing when it is free).

### 5. Send the subdomain to it

In Plesk › `brew.yourdomain.com` › **Apache & nginx Settings**:

1. Untick **Proxy mode** (under nginx settings), so nginx answers this subdomain itself instead of passing it to
   Apache. This affects only this subdomain.
2. In **Additional nginx directives**, add:

   ```
   location / {
       proxy_pass http://127.0.0.1:8642;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $remote_addr;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 120s;
   }
   ```

3. Click **OK**. If Plesk reports `duplicate location "/"`, change the first line to `location ~ ^/ {` and save again.

Use the same port here as in the service file.

### 6. Finish setup straight away

```
journalctl -u brew-reports | grep "setup code"
```

Open `https://brew.yourdomain.com/admin.html`, enter the code, your Brewfather User ID and API key, and choose an
admin password. Do this as soon as the service is running: whoever enters the code first sets the password.

Then, in Admin › Settings, choose your tap water profile and, if you like, **Fetch from Brewfather automatically**,
which keeps running because the service is always on.

### Updating on Plesk

```
cd /var/www/vhosts/yourdomain.com/brew-reports
sudo -u yoursysuser git pull        # or extract the new zip over the folder
systemctl restart brew-reports
```

Then click **Rebuild pages from saved data** on the Admin page. Your `.env` and `data/` are never touched by an update.

### If something doesn't work

- **502 Bad Gateway:** the service isn't running or the ports differ. Check `systemctl status brew-reports` and
  `journalctl -u brew-reports -n 50`.
- **The page shows a WordPress or Plesk default page:** Proxy mode is still on, or the nginx directives weren't saved.
- **"Can't use port 8642":** another program has that port. Choose another in both the service file and the nginx directives.
- **Forgot the admin password:** `systemctl stop brew-reports`, delete `data/admin.json`, `systemctl start brew-reports`,
  and use the new setup code from the log. Your saved Brewfather key is kept.

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
