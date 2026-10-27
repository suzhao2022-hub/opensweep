; Inno Setup script for the OpenSweep installer.
; Build after PyInstaller has produced dist\OpenSweep:
;   iscc /DAppVersion=1.0.0 packaging\opensweep.iss

#ifndef AppVersion
  #define AppVersion "1.0.1"
#endif

[Setup]
AppId={{6F3C1C1E-7A0B-4C7E-9D63-0B5D0E5A9C21}
AppName=OpenSweep
AppVersion={#AppVersion}
AppVerName=OpenSweep {#AppVersion}
AppPublisher=OpenSweep contributors
AppPublisherURL=https://github.com/suzhao2022-hub/opensweep
AppSupportURL=https://github.com/suzhao2022-hub/opensweep/issues
DefaultDirName={autopf}\OpenSweep
DefaultGroupName=OpenSweep
DisableProgramGroupPage=yes
; Installs for the current user without a UAC prompt; "all users" can be chosen instead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=OpenSweep-{#AppVersion}-setup
SetupIconFile=opensweep.ico
UninstallDisplayIcon={app}\OpenSweep.exe
UninstallDisplayName=OpenSweep
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
; Close a running OpenSweep before upgrading.
CloseApplications=yes

[Languages]
; Chinese comes from Inno Setup's user-contributed translations (packaging\ChineseSimplified.isl).
Name: "zh"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
; Other app languages (Korean...) fall back to the installer's English texts.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\OpenSweep\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OpenSweep"; Filename: "{app}\OpenSweep.exe"
Name: "{autodesktop}\OpenSweep"; Filename: "{app}\OpenSweep.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OpenSweep.exe"; Description: "{cm:LaunchProgram,OpenSweep}"; Flags: nowait postinstall skipifsilent

[Registry]
; The startup guard's logon check is added by the app itself; remove it on uninstall so no broken
; startup entry is left behind.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "OpenSweep Guard"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"; ValueType: none; ValueName: "OpenSweep Guard"; Flags: uninsdeletevalue

[UninstallDelete]
; Settings and logs live in %APPDATA%\OpenSweep and are kept on purpose (they're tiny and keep your choices).
