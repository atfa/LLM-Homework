; xAgent per-user Windows installer. Compile from Agent/xAgent using ISCC.
#define MyAppName "xAgent"
#define MyAppVersion "0.1.0"
[Setup]
AppId={{A15D7342-32D8-4B13-BEB3-7395F4D49E23}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\xAgent
DefaultGroupName=xAgent
UninstallDisplayIcon={app}\xAgent.exe
OutputDir=dist-installer
OutputBaseFilename=xAgent-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=packaging\xagent.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\xAgent\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\xAgent"; Filename: "{app}\xAgent.exe"; WorkingDir: "{userdocs}\xAgent\workspace"
Name: "{autodesktop}\xAgent"; Filename: "{app}\xAgent.exe"; WorkingDir: "{userdocs}\xAgent\workspace"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："

[Dirs]
Name: "{userdocs}\xAgent\workspace"

[Run]
Filename: "{app}\xAgent.exe"; Description: "启动 xAgent"; Flags: nowait postinstall skipifsilent; WorkingDir: "{userdocs}\xAgent\workspace"
