; Inno Setup script for the Heroez Ops Windows installer.
; Build (from the repo root, after PyInstaller and downloading the two tools):
;   ISCC.exe /DAppVersion=0.2.0 packaging\windows\installer.iss
; Expects: dist\HeroezOps\*, packaging\windows\bin\WinSW-x64.exe, packaging\windows\bin\cloudflared.exe

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Never change AppId: it is how a new installer recognises and upgrades the old one.
AppId={{6B1E3C52-8F0A-4F3E-9C55-3E1D2A7B9F10}
AppName=Heroez Ops
AppVersion={#AppVersion}
AppPublisher=Heroez
DefaultDirName={autopf}\Heroez Ops
DefaultGroupName=Heroez Ops
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=HeroezOps-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Heroez Ops
CloseApplications=no

[Tasks]
Name: "nosleep"; Description: "Jangan sleep / hibernate saat laptop dicolok ke listrik (disarankan: pengingat berhenti kalau komputer tidur)"

[Dirs]
Name: "{commonappdata}\HeroezOps"

[Files]
Source: "..\..\dist\HeroezOps\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "bin\WinSW-x64.exe"; DestDir: "{app}"; DestName: "HeroezOpsService.exe"; Flags: ignoreversion
Source: "HeroezOpsService.xml"; DestDir: "{app}"; Flags: ignoreversion
Source: "bin\cloudflared.exe"; DestDir: "{app}"; Flags: ignoreversion

[INI]
Filename: "{app}\Pengaturan Heroez Ops.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:8788/"

[Icons]
Name: "{group}\Heroez Ops - Pengaturan"; Filename: "{app}\Pengaturan Heroez Ops.url"
Name: "{group}\Heroez Ops - Folder log"; Filename: "{commonappdata}\HeroezOps\logs"
Name: "{autodesktop}\Heroez Ops - Pengaturan"; Filename: "{app}\Pengaturan Heroez Ops.url"

[Run]
; Data folder: only SYSTEM and Administrators may read it (chat history, encrypted secrets).
Filename: "{sys}\icacls.exe"; Parameters: """{commonappdata}\HeroezOps"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F"; Flags: runhidden waituntilterminated
Filename: "{sys}\powercfg.exe"; Parameters: "/change standby-timeout-ac 0"; Flags: runhidden waituntilterminated; Tasks: nosleep
Filename: "{sys}\powercfg.exe"; Parameters: "/change hibernate-timeout-ac 0"; Flags: runhidden waituntilterminated; Tasks: nosleep
Filename: "{app}\HeroezOpsService.exe"; Parameters: "install"; Flags: runhidden waituntilterminated
Filename: "{app}\HeroezOpsService.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Sleep 6; Start-Process 'http://127.0.0.1:8788/'"""; Description: "Buka halaman Pengaturan"; Flags: postinstall nowait runhidden skipifsilent

[UninstallRun]
Filename: "{app}\HeroezOpsService.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\HeroezOpsService.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]
// On upgrade: stop and remove the old service before files are replaced.
// Settings and data in ProgramData\HeroezOps are left untouched.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Svc: String;
  Rc: Integer;
begin
  Svc := ExpandConstant('{app}\HeroezOpsService.exe');
  if FileExists(Svc) then
  begin
    Exec(Svc, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Rc);
    Exec(Svc, 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
  Result := '';
end;
