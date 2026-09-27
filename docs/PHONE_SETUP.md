# Using E.V.A. on your phone (v0.2.5)

Browsers only allow the microphone on secure pages (HTTPS or localhost). Over plain
`http://192.168.x.x:8001` your phone can type to E.V.A. and hear her, but never talk to her.
So in network mode she also serves HTTPS on port 8443, with a certificate from her own small
certificate authority (CA). You install that CA on your phone once.

## One-time setup

1. **Network mode.** In `config/settings.local.yaml`:
   ```yaml
   server:
     listen: network
   ```
2. **Firewall** (admin PowerShell, once; your Wi-Fi must be a *Private* network in Windows):
   ```powershell
   New-NetFirewallRule -DisplayName "E.V.A." -Direction Inbound -Protocol TCP -LocalPort 8001,8443 -Profile Private -Action Allow
   ```
3. **Start E.V.A.** The terminal prints two links and the CA fingerprint.
4. **Install the CA on the phone** (Android):
   - Open `http://<pc-ip>:8001/eva-ca.crt` in Chrome on the phone. It downloads `eva-ca.crt`.
   - Settings > Security and privacy > More security settings > Encryption and credentials >
     Install a certificate > **CA certificate** > "Install anyway" > pick `eva-ca.crt`.
     (Menu names differ a little per phone; searching Settings for "CA certificate" finds it.)
5. **Pair.** Open the `https://<pc-ip>:8443/?token=...` link from the terminal. You get a padlock,
   the browser is paired for a year, and the mic works. Then "Add to Home screen".

## Why the CA is safe to install

A CA on your phone is normally trusted for every website. E.V.A.'s CA carries X.509 *name constraints*:
it can only vouch for private network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x, 127.x.x.x) and
`localhost` / `*.local`. Even if its key were stolen from your PC, it could not impersonate google.com or
your bank. The key never leaves `data/tls/` on your PC (git-ignored).

## If something is off

- **"Your connection is not private":** the CA isn't installed, or was installed as a *VPN and app*
  certificate instead of a **CA certificate**. Reinstall it as a CA certificate.
- **The link stopped working:** your PC got a new LAN address. E.V.A. renews her certificate automatically
  at the next start; open the new link. Reserving the PC's address in your router avoids this.
- **Quick test without installing anything:** tap "Advanced > Proceed" on the warning page. The mic works
  for that session, but the home-screen app won't install and Chrome keeps warning.
- **Start over:** delete `data/tls/` and restart, then remove the old CA on the phone (Settings >
  Encryption and credentials > Trusted credentials > User) and install the new one.
