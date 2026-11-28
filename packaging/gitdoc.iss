; Per-user GitDoc installer. Built by packaging/windows.ps1.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\windows\GitDoc"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef ProductIcon
  #define ProductIcon AddBackslash(SourcePath) + "..\desktop\icons\gitdoc.ico"
#endif

[Setup]
AppId={{6A2E4C0B-3C0F-4F6E-9B57-7D1B6C1F5E21}
AppName=GitDoc
AppVersion={#AppVersion}
AppPublisher=GitDoc
DefaultDirName={localappdata}\Programs\GitDoc
DefaultGroupName=GitDoc
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=GitDoc-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#ProductIcon}
UninstallDisplayIcon={app}\gitdoc-desktop.exe
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade must not keep modules the new version dropped.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GitDoc"; Filename: "{app}\gitdoc-desktop.exe"
Name: "{autodesktop}\GitDoc"; Filename: "{app}\gitdoc-desktop.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\gitdoc"; ValueType: string; ValueName: ""; ValueData: "URL:GitDoc document link"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\gitdoc"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\gitdoc\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\gitdoc-desktop.exe,0"
Root: HKCU; Subkey: "Software\Classes\gitdoc\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\gitdoc-desktop.exe"" ""%1"""

[Run]
Filename: "{app}\gitdoc-desktop.exe"; Description: "{cm:LaunchProgram,GitDoc}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
