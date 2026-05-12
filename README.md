# maxima-ocr
A custom OCR python program for Maxima

## Quick Start

### Install on a factory PC
1. Download `MaximaOCR-Setup-<version>.exe` from
   https://github.com/automato-ai/maxima-ocr/releases.
2. Copy it to the factory PC (the install target is airgapped — the installer
   is fully self-contained).
3. Right-click → **Run as administrator**. Accept the default install location
   `C:\Maxima\OCR`.
4. The installer registers `MaximaOCR` as a Windows service (auto-start) and
   starts it. Verify with `sc query MaximaOCR` or by triggering an OCR call.

### Upgrade
Run a newer `MaximaOCR-Setup-<version>.exe` on the same PC. The installer:
- detects the prior install via its AppId,
- stops the `MaximaOCR` service,
- replaces `modbus_server.exe`, `models\`, `tools\`, `nssm.exe`, and the scripts,
- **preserves the existing `config.yaml`** (site-specific tuning survives),
- re-registers and restarts the service.

### Service control
The install directory ships with operator scripts. Run them as administrator
(right-click → Run as administrator, or from an elevated shell):


| Script                       | Effect                                    |
|------------------------------|-------------------------------------------|
| `start.bat`                  | `sc start MaximaOCR`                      |
| `stop.bat`                   | `sc stop MaximaOCR`                       |
| `restart.bat`                | stop, wait for STOPPED, start             |
| `register-service.bat`       | re-register service if it's broken/missing |
| `unregister-service.bat`     | stop + remove service (uninstaller uses this) |
| `uninstall.bat`              | run the full uninstaller (accepts `/VERYSILENT`, etc.) |

### Configuration
Edit `C:\Maxima\OCR\config.yaml` and run `restart.bat`. Edits survive upgrades.

### Service logs
NSSM redirects the service's stdout/stderr to `C:\Maxima\OCR\service-stdout.log`
and `service-stderr.log` (rotated at 1 MB). Application-level logs (DEBUG) are
in `modbus_server.log` per the `logging:` section of `config.yaml`.

## Building the installer

The build is self-bootstrapping — no system-installed Inno Setup or NSSM needed.

Prerequisites on the build machine:
- Python with the project venv set up (`pip install -r requirements.txt`).
- `gh` (GitHub CLI), authenticated to an account with read access to the
  private `dennispo/maxima-ocr-learning` repo. The build fetches the latest
  `bundle-YYYYMMDD` release from there and bakes it into the installer.
  In CI: `gh` is preinstalled on Windows runners; just set `GH_TOKEN` (or
  `GITHUB_TOKEN`) to a PAT with read access to that repo.
- Internet access on first build (downloads NSSM + Inno Setup into
  `build\tools\`, cached after; models bundle is re-checked every build but
  the download is skipped if the latest tag is already present locally).

Build:
```
compile.bat 0.4
```
The first arg is the version baked into the installer filename and Add/Remove
Programs entry. If omitted, `compile.bat` falls back to `git describe --tags`
so a tagged checkout still builds locally. In CI, pass the git tag explicitly:
```
compile.bat %GITHUB_REF_NAME%
```

`compile.bat` wraps `build.ps1`, which:
1. resolves the latest `bundle-*` release from the models repo and downloads
   it into `models/` if not already present (then prunes stale dated subfolders),
2. compiles `modbus_server.exe` and `tools\modbus_client.exe` with PyInstaller,
3. fetches and caches NSSM and Inno Setup if not already in `build\tools\`,
4. compiles `dist\MaximaOCR-Setup-<version>.exe` via ISCC.

For offline / iteration-on-installer builds:
```
compile.bat 0.4 -SkipPyInstaller -SkipModelFetch
```

### Integration via modbus TCP
This program is a modbus TCP server (modbus slave) and can be controlled via TCP modbus communication.
The default listening port is `502`, but can be changed in the `config.yaml` file.

The protocol on top of modbus id as follows:

1. Master triggers an operation by writing an opcode to the holding register at address `1`. The slave will handle
   this register as a button. Meaning, it will only be triggered by the change of the value from `0` to a non-zero
   opcode. Any consecutive writing of the same opcode will be ignored (assuming the button is still pressed). The
   status will reset, once the Master writes `0` to the register again. See the [opcodes](#opcodes) below.
2. The operation starts. It can take a few seconds. The execution status can be monitored on holding register `2`.
   The monitoring is optional. See the [status codes](#status-codes-for-monitoring) below.
3. Once the operation is complete, the value at register `1` will become `0`, indicating that no operation is 
   currently performed and the slave is ready for next operation.
4. The result of the last executed operation can be read from the holding registers starting at address `3`. The
   result string is encoded in `ascii` format, one char per register. The string is terminated by `0` (next register
   after the result string is promised to hold the value `0`).

#### Opcodes

| Value of holding register at address `1` | Operation                                                |
|------------------------------------------|----------------------------------------------------------|
| `0`                                      | Idle / release button                                    |
| `1`                                      | Capture video from all cameras to disk                   |
| `2`                                      | Run OCR; result string is the recognized cylinder digits |

For opcode `2` (OCR), the recognition outcome determines both the status register
and the result string:

| Outcome           | Status register | Result string         |
|-------------------|-----------------|-----------------------|
| Recognized        | `0` (Complete)  | recognized digits     |
| No cameras found  | `10` (Error)    | `cameras not found`   |
| Cameras not ready | `10` (Error)    | `cameras not ready`   |
| Unrecognized      | `10` (Error)    | `unrecognized`        |

#### OCR session recording
Every OCR trigger (opcode `2`) leaves a replay artifact in `capture.folder`
(default `./capture`):

| File                                  | Contents                              |
|---------------------------------------|---------------------------------------|
| `{YYYYMMDD-HHMMSS}-{cam}.mp4`         | one video per camera, same naming as opcode `1` |
| `{YYYYMMDD-HHMMSS}-meta.json`         | session header + per-frame model decisions + outcome |

The metadata JSON pairs 1:1 with the videos via the shared timestamp prefix.
Each frame entry carries, per camera, the readiness decision (or `{"disabled":
true}` when readiness gating is off), the bounding box, the OCR text +
per-digit detections, and the aggregator's running result (including the
individual digit candidates and a reference to the per-frame original and
preprocessed crop PNGs stored under `{prefix}-crops/`). The tick-level
`agg_status` and `fused` fields capture the cross-camera fusion verdict — the
same data the pipeline uses to decide whether to terminate.

Replay a session with `tools\replay.exe`:

```
C:\Maxima\OCR\tools\replay.exe C:\Maxima\OCR\capture\20260511-153012-meta.json
```

The tool shows each camera's video side-by-side with the bbox (cyan, with the
bottom edge highlighted in red), a readiness chip, and below each video a
zoomed view of the original crop with aggregator candidates marked and the
preprocessed crop with OCR digit boxes. Pass a folder instead of a meta.json
to replay the most recent session.

Recording is enabled by default. To disable on disk-constrained sites:
```yaml
ocr:
  capture:
    enabled: false
```

#### Status codes for monitoring
Optionally, the master can monitor the execution of the operation by reading the execution status from 
holding register at address `2` during the operation. The possible values are:

| Value of  holding register at address `2` | Execution status    |
|-------------------------------------------|---------------------|
| `0`                                       | Complete            |
| `1`                                       | Working             |
| `10`                                      | Complete with error |
