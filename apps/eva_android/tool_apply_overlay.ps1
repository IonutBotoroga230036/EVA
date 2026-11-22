# Run after `flutter create` or after an update (see README.md). Copies E.V.A.'s Android files over the generated ones.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = "$here\android_overlay\app\src\main"
$dst = "$here\android\app\src\main"
Copy-Item -Force "$src\AndroidManifest.xml" "$dst\AndroidManifest.xml"
$kt = "$dst\kotlin\nl\ionut\eva_android"
New-Item -ItemType Directory -Force $kt | Out-Null
Copy-Item -Force "$src\kotlin\nl\ionut\eva_android\*.kt" $kt          # MainActivity, WakeService, WakeEngine, OverlayActivity, DeviceActions, MessageListener
New-Item -ItemType Directory -Force "$dst\res\xml" | Out-Null
Copy-Item -Force "$src\res\xml\network_security_config.xml" "$dst\res\xml\network_security_config.xml"
New-Item -ItemType Directory -Force "$dst\res\values" | Out-Null
Copy-Item -Force "$src\res\values\eva_overlay.xml" "$dst\res\values\eva_overlay.xml"
Write-Host "E.V.A. Android files applied. Now: flutter pub get; flutter run"
