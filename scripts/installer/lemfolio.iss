; Built by scripts/build_installer.ps1, which fills dist\stage and passes AppVersion.
[Setup]
AppId={{6F3B7E52-9A41-4C0D-8E27-4D1C5A7B9F10}
AppName=Lemfolio
AppVersion={#AppVersion}
AppPublisher=h1n4k0
AppPublisherURL=https://github.com/h1n4k0/lemfolio
DefaultDirName={autopf}\Lemfolio
DefaultGroupName=Lemfolio
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\..\LICENSE.md
SetupIconFile=..\..\native\assets\brand\lemfolio.ico
UninstallDisplayIcon={app}\lemfolio-desktop.exe
OutputBaseFilename=Lemfolio-{#AppVersion}-Setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; an upgrade must not keep files the new version dropped; data\ is never touched
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "..\..\dist\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lemfolio"; Filename: "{app}\lemfolio-desktop.exe"
Name: "{group}\Build TeX image"; Filename: "{app}\build-tex-image.cmd"
Name: "{autodesktop}\Lemfolio"; Filename: "{app}\lemfolio-desktop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\lemfolio-desktop.exe"; Description: "{cm:LaunchProgram,Lemfolio}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; what the running service wrote beside the installed files; data\ stays
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\runtime"
