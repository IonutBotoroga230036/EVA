# Reaching E.V.A. from anywhere (v0.3 milestone 8)

Tailscale puts your phone and your PC on a private network (a "tailnet") that works from any Wi-Fi or mobile
data, with no open ports on your router and a real HTTPS certificate. E.V.A. can stay on `listen: local`: she
stays invisible on your home network and is reachable only through your tailnet.

## One-time setup
1. Install Tailscale on the PC (tailscale.com/download) and on the phone (Play Store). Sign in with the same
   account on both.
2. In the Tailscale admin console, DNS page: enable **MagicDNS** and **HTTPS Certificates**.
3. On the PC, in PowerShell (once; it survives restarts):
   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8001
   ```
4. Start E.V.A. The terminal now shows `Anywhere (Tailscale): https://<your-pc>.<tailnet>.ts.net`.
5. In the E.V.A. app (or the phone's browser): that address, plus the remote token from `data\remote_token.txt`.

## How it stays safe
- Tailscale forwards requests from 127.0.0.1 with forwarding headers; E.V.A. treats them as remote, so the
  **token is still required**, on top of Tailscale's own device authentication.
- Websites can't reach her (the origin check also works behind the proxy).
- Stop remote access any time: `tailscale serve reset`.

## Check it
- `tailscale serve status` shows `https://<your-pc>.<tailnet>.ts.net` proxying to `http://127.0.0.1:8001`.
- Status panel: `remote` shows Tailscale running and serving.
