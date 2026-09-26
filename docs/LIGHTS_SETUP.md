# Connecting E.V.A. to your LSC Smart Connect LED strips

About 20 minutes, once. After that she controls the strips directly on your Wi-Fi: no cloud per command.

## Why the Smart Life app
LSC Smart Connect runs on Tuya, but the LSC app keeps devices in a private part of Tuya's cloud that
developer tools can't reach. Tuya's own Smart Life app uses the open part. Same strips, same features.

## 1. Move the strips to Smart Life
1. Install **Smart Life** on your phone and create an account with your email (not a guest account).
2. In the LSC app, remove each strip.
3. Put a strip in pairing mode: usually hold its controller button about 5 seconds, or switch its power
   off and on 3 times, until it blinks quickly.
4. In Smart Life: **+ > Add device**, and add it. Give each strip a clear name, e.g. `Desk strip`, `Bed strip`.
   E.V.A. uses these names ("turn on the bed lights").

## 2. Free Tuya developer account
Tuya changes this portal often, so labels may differ slightly.
1. Sign up at https://iot.tuya.com (skip the account-type question).
2. **Cloud > Development > Create Cloud Project**: name `EVA`, industry Smart Home, development method
   Smart Home, data center **Central Europe**. Accept the default API services.
3. In the project: **Devices > Link App Account > Add App Account**. A QR code appears.
4. In Smart Life on your phone: **Me** tab, scan icon at the top right, scan the code. Your strips show up.
5. In the project **Overview**, note the **Access ID** and **Access Secret**.

## 3. Fetch the local keys
In PowerShell, from `D:\Project E.V.A\eva` with the venv active:

    pip install tinytuya
    mkdir data\lights
    cd data\lights
    python -m tinytuya wizard
    cd ..\..

Enter the Access ID, Access Secret, region `eu`, and answer **yes** when it offers to poll local devices.
That writes `devices.json` (with the secret keys) and `snapshot.json` (with IP addresses) into `data\lights`.
Both are git-ignored and never leave the PC.

## 4. Try it
Restart E.V.A., then: "Turn the lights purple", "Lights to 30 percent", "Warm white", "Turn off the bed lights",
"I'm home, I feel red", "Set the mood to relax", "Create a mood called study: cool white at 80 percent with Deep Focus".

## Good to know
- Tuya strips accept one connection at a time. If a command fails, close the Smart Life app and try again.
- If you ever re-pair a strip, its key changes: run the wizard again. The Tuya trial is only needed for that.
- A fixed IP for each strip (a DHCP reservation in your router) makes control faster and more reliable.
- The PC and the strips must be on the same network (not a separate guest Wi-Fi).
