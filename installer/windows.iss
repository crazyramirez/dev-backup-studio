; Instalador de Windows de DEV Backup Studio (Inno Setup 6).
; Se compila desde GitHub Actions:
;   iscc /DAppVersion=1.1.0 /DSourceDir="<carpeta de PyInstaller>" installer\windows.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\DEV Backup Studio"
#endif
#define AppName "DEV Backup Studio"
#define AppExe "DEV Backup Studio.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{8F2C6A1E-3B7D-4E59-9C1A-5D2E7B4F6A90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=crazyramirez
AppPublisherURL=https://github.com/crazyramirez/dev-backup-studio
AppSupportURL=https://github.com/crazyramirez/dev-backup-studio/issues
AppUpdatesURL=https://github.com/crazyramirez/dev-backup-studio/releases
VersionInfoVersion={#AppVersion}
; para el usuario actual (sin administrador); se puede elegir «para todos» en el asistente
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; la app crea este mutex: si está abierta (también en la bandeja), el asistente pide cerrarla
AppMutex=DevBackupStudioMutex
CloseApplications=no
OutputDir=..\dist
OutputBaseFilename=DEV-Backup-Studio-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; restos de la versión anterior de las bibliotecas
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; actualización lanzada desde la propia app: al terminar se vuelve a abrir sola
Filename: "{app}\{#AppExe}"; Parameters: "--updated"; Flags: nowait; Check: IsAutoUpdate

[Code]
function IsAutoUpdate: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/AUTOUPDATE') = 0 then
      Result := True;
end;

{ Al desinstalar, quita el arranque con Windows solo si apunta a esta instalación
  (no toca el de una copia que se ejecute desde el código). Los ajustes, índices y
  registros en %APPDATA% se conservan, igual que las copias del disco externo. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
begin
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKEY_CURRENT_USER, '{#RunKey}', '{#AppName}', Value) then
      if Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0 then
        RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', '{#AppName}');
end;
