; Builds the one-click Windows installer.  Compile with:
;   ISCC.exe /DMyAppVersion=1.0.0 installer\Constelyric.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{D3A9F1C4-5B7E-4E2A-8C61-9F0B7A3E52D8}
AppName=Constelyric
AppVersion={#MyAppVersion}
AppPublisher=Constelyric contributors
DefaultDirName={autopf}\Constelyric
DefaultGroupName=Constelyric
; installs for the current user only, so no admin prompt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Constelyric-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Constelyric.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "startup"; Description: "Start Constelyric when I sign in to Windows"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Constelyric\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Constelyric"; Filename: "{app}\Constelyric.exe"
Name: "{autodesktop}\Constelyric"; Filename: "{app}\Constelyric.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Constelyric"; ValueData: """{app}\Constelyric.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\Constelyric.exe"; Description: "Launch Constelyric"; Flags: nowait postinstall skipifsilent
