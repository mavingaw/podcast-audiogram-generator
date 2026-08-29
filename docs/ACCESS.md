# Reaching Kinder from outside the house

Kinder answers on the LAN at `http://192.168.1.58:8099`. Everything below is
about letting somebody who is not in the house use it.

## The route that already exists

This server already runs a Cloudflare tunnel for `skdcorp.com`
(`cloudflared-skdcorp`), serving `audiobooks.`, `cloud.` and `duplicati.`. That
connector is on the Docker bridge network and **can already reach Kinder** —
verified from inside it:

    docker exec cloudflared-skdcorp ... http://192.168.1.58:8099/api/health
    {"ok":true,"app":"Kinder"}

So no container, port, or firewall change is needed. The tunnel is
*remotely managed* — it holds only a token, and its routing lives in
Cloudflare — so the one remaining step is adding a hostname there:

1. <https://one.dash.cloudflare.com> → **Networks → Tunnels**
2. Open the tunnel serving `skdcorp.com`, then **Public Hostnames → Add**
3. Subdomain `kinder`, domain `skdcorp.com`
4. Service: **HTTP**, URL `192.168.1.58:8099`
5. Save. `https://kinder.skdcorp.com` works within a minute.

That cannot be done from this machine: routing is held by Cloudflare, and no
API token is stored here. With one (`Zone:DNS:Edit` and
`Account:Cloudflare Tunnel:Edit`) it could be scripted end to end.

## The temporary address

A quick tunnel is running so the app is usable before that is done:

    docker compose -p src -f docker-compose.gpu.yml --profile quicktunnel up -d
    docker logs kinder-quicktunnel | grep trycloudflare

It needs no account, but **the address changes every restart**. Take it down
once the permanent hostname is live — two ways in is one more than is needed:

    docker compose -p src -f docker-compose.gpu.yml --profile quicktunnel stop

Note `down` with a profile stops the *whole stack*, app included. Use `stop`.

## Before it is reachable

Three things were wrong for a public address and were fixed first. Each was
verified against the live tunnel, and one was found by attacking it.

**Registration was open.** Anybody with the URL could create an account — proven
by doing it from the internet. It is now closed by default, and people join with
a shared code (`PAS_SIGNUP_CODE`) instead.

**The code has to be declared in compose, not just `.env`.** A variable in
`.env` feeds compose's own substitution; it is *not* passed to the container.
`PAS_SIGNUP_CODE` sat in `.env` for a whole deploy doing nothing while
registration stayed open. It is now in the `environment:` block.

**Login was unthrottled.** Six wrong passwords now returns `429` with a
`Retry-After`, backing off exponentially, keyed on `cf-connecting-ip` so it
throttles the caller rather than the tunnel.

**The session cookie is now `Secure`** when the request arrives over HTTPS, and
still not on plain HTTP, so LAN sign-in keeps working.

## Adding somebody

Share the address and the sign-up code from `.env`. They pick their own username
and password; new accounts are never administrators. Rotate the code by editing
`.env` and recreating the container. To stop new sign-ups entirely, clear
`PAS_SIGNUP_CODE`.
