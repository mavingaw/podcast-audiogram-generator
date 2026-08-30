# Reaching Kinder from outside the house

Kinder answers on the LAN at `http://192.168.1.58:8099` and on the internet at
**`https://kinder.skdcorp.com`**, through the Cloudflare tunnel this server
already ran for `skdcorp.com` (`cloudflared-skdcorp`). The hostname is a
public-hostname entry on that tunnel pointing at `http://192.168.1.58:8099`;
nothing on the box was opened for it.

## Letting a friend in

Sign-ups are closed. Nobody who finds the address can make an account. The way
in is an **invite link**:

1. Sign in as an administrator and open the **Admin** strip on the home page.
2. Copy the **Invite link** (`https://kinder.skdcorp.com/?invite=…`).
3. Send it. It opens straight onto the sign-up form with the code filled in;
   they choose a username and password and are in.

The link is as good as the code in it. To revoke it, change `PAS_SIGNUP_CODE`
in the container's settings (Unraid → Docker → Kinder → edit) and restart;
every link sent before stops working. Accounts already created stay — remove
one from the same Admin strip, which deletes everything that person made.

"Anyone can create an account" in the Admin strip opens sign-ups to everyone
with the address. Leave it off; the invite link is the middle ground.

## What Cloudflare's free plan limits, and how Kinder gets around it

The tunnel refuses any single request over 100 MB. Kinder uploads in 32 MB
pieces and reassembles them, so an hour-long episode goes through; nothing
about the plan needs changing. The Studio player streams only the clip's own
audio (under a megabyte), never the episode.

## If the hostname ever needs recreating

<https://one.dash.cloudflare.com> → Networks → Tunnels → the `skdcorp.com`
tunnel → Public Hostnames → Add: subdomain `kinder`, service **HTTP**,
URL `192.168.1.58:8099`. It works within a minute. The tunnel is remotely
managed, so this cannot be scripted from the box without an API token
(`Zone:DNS:Edit` + `Account:Cloudflare Tunnel:Edit`).

## Why not a VPN

Tailscale would remove the proxy and its limits, but every friend would need
the client installed and signed in. For people who just want to make clips
from a link, the tunnel is the right trade.
