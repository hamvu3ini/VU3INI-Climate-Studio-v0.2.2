; ============================================================================
;  VU3INI Climate Studio - Inno Setup script
;  Built by packaging\build_installer.bat (do not compile by hand unless the
;  build\stage folder already exists).
;
;  Standard build : everything except the AI libraries; the installer offers to
;                   download PyTorch (GPU or CPU build) and TerraTorch.
;  Full build     : AI libraries included; works with no internet at install
;                   time (several GB, split into setup.exe + .bin parts).
; ============================================================================

#define MyAppName "VU3INI Climate Studio"
#ifndef MyVersion
  #define MyVersion "0.2.0"
#endif
#ifndef Full
  #define Full "0"
#endif
#ifndef RootDir
  #define RootDir SourcePath + "\.."
#endif
#if Full == "1"
  #define OutName "VU3INI_Climate_Studio_Setup_v" + MyVersion + "_Full"
#else
  #define OutName "VU3INI_Climate_Studio_Setup_v" + MyVersion
#endif

[Setup]
AppId={{9C365F6F-3FD0-40AD-A4C6-A2F9CFA584A6}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppVerName={#MyAppName} {#MyVersion}
AppPublisher=Sasanka Boruah (VU3INI)
AppContact=hamvu3ini@gmail.com
AppCopyright=© 2026 Sasanka Boruah (VU3INI)
VersionInfoVersion={#MyVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Installs for the current user without admin rights; the user may choose "all users" (needs admin).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile={#RootDir}\assets\app.ico
UninstallDisplayIcon={app}\assets\app.ico
UninstallDisplayName={#MyAppName}
LicenseFile={#RootDir}\packaging\NOTICE.txt
OutputDir={#RootDir}\dist
OutputBaseFilename={#OutName}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
; The running app holds this mutex, so setup and uninstall ask the user to close it first.
AppMutex=VU3INI_Climate_Studio_Mutex
#if Full == "1"
DiskSpanning=yes
DiskSliceSize=max
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
#if Full != "1"
Name: "installai"; Description: "Download and install the AI components now (PyTorch for this PC's NVIDIA GPU or CPU, and TerraTorch). Needs internet, about 3 GB. You can also do this later from the Start menu."; GroupDescription: "AI mapping (Prithvi):"
#endif

[Files]
Source: "{#RootDir}\build\stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Flags: runminimized; Comment: "Climate analysis with Google Earth Engine and your weather station"
Name: "{group}\{#MyAppName} (debug)"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\launcher.py"" --debug"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Comment: "Starts with a visible window showing all messages"
Name: "{group}\Install or repair AI components"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\packaging\install_ai.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Flags: runminimized; Tasks: desktopicon

[Run]
#if Full != "1"
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\packaging\install_ai.py"" --from-installer"; WorkingDir: "{app}"; StatusMsg: "Installing AI components (this can take several minutes)..."; Tasks: installai; Flags: waituntilterminated
#endif
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent runminimized

[UninstallDelete]
; Packages added after installation (AI components) and Python caches are not tracked by setup.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\VU3INI Climate Studio');
    if DirExists(DataDir) then
      if MsgBox('Also delete your settings, station data, saved results, reports and downloaded AI models?' + #13#10 + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
