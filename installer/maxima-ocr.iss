; Maxima OCR Windows installer.
; Built by build.ps1 -- do not invoke ISCC directly without passing /DMyAppVersion=...
; Upgrades are detected via the AppId GUID below; do NOT regenerate it across releases.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "Maxima OCR"
#define MyAppPublisher "automato-ai"
#define MyAppURL "https://github.com/automato-ai/maxima-ocr"
#define MyAppExeName "modbus_server.exe"
#define ServiceName "MaximaOCR"

[Setup]
AppId={{B7A8E3C1-9F2D-4E47-8E5A-1C3D5F0A9B4E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName=C:\Maxima\OCR
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=MaximaOCR-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Files]
; Main service binary -- always replaced on upgrade.
Source: "..\dist\modbus_server.exe"; DestDir: "{app}"; Flags: ignoreversion

; Default config -- preserved on upgrade. Only copied if not already present.
Source: "..\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist

; Service wrapper -- always replaced (vendored copy is pinned via build.ps1).
Source: "..\build\tools\nssm\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

; Operator scripts and service register/unregister helpers.
Source: "scripts\start.bat";              DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\stop.bat";               DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\restart.bat";            DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\register-service.bat";   DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\unregister-service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\uninstall.bat";          DestDir: "{app}"; Flags: ignoreversion

; OCR models bundle -- always replaced on upgrade (active.txt + dated subfolder).
Source: "..\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs

; Compiled tools.
Source: "..\dist\tools\modbus_client.exe"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\dist\tools\replay.exe";        DestDir: "{app}\tools"; Flags: ignoreversion

[Run]
; Register and start the service after files are in place.
Filename: "{app}\register-service.bat"; \
    StatusMsg: "Registering {#ServiceName} service..."; \
    Flags: runhidden waituntilterminated

[UninstallRun]
; Stop and remove the service before uninstaller deletes files.
Filename: "{app}\unregister-service.bat"; \
    Flags: runhidden waituntilterminated; \
    RunOnceId: "UnregisterMaximaOCRService"

[UninstallDelete]
; Logs and capture/ are created at runtime, so Inno doesn't know about them.
; Sweep them on uninstall so the install dir can be removed cleanly.
Type: files;          Name: "{app}\*.log"
Type: files;          Name: "{app}\*.log.*"
Type: filesandordirs; Name: "{app}\capture"
Type: dirifempty;     Name: "{app}"

[Code]
// Stop the service before file overwrite. Errors are silently ignored:
// on first install the service does not exist yet.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  NssmPath: String;
begin
  Result := '';
  NssmPath := ExpandConstant('{app}\nssm.exe');

  // 'sc stop' is asynchronous; NSSM's stop is synchronous. Prefer NSSM if the
  // previous install left it behind, fall back to sc otherwise.
  if FileExists(NssmPath) then
    Exec(NssmPath, 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    Exec(ExpandConstant('{sys}\sc.exe'), 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // Give Windows time to release the modbus_server.exe file handle.
  Sleep(3000);
end;
