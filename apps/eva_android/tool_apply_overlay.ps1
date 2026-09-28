# Run once after `flutter create` (see README.md). Copies E.V.A.'s Android files over the generated ones.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item -Force "$here\android_overlay\app\src\main\AndroidManifest.xml" "$here\android\app\src\main\AndroidManifest.xml"
$kt = "$here\android\app\src\main\kotlin\nl\ionut\eva_android"
New-Item -ItemType Directory -Force $kt | Out-Null
Copy-Item -Force "$here\android_overlay\app\src\main\kotlin\nl\ionut\eva_android\MainActivity.kt" "$kt\MainActivity.kt"
Write-Host "E.V.A. Android files applied. Now: flutter pub get; flutter run"
