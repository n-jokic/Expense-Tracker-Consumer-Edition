#define AppName "Expense Tracker"
#define AppVersion "1.0.0"
#define AppPublisher "Expense Tracker"

[Setup]
AppId={{E0A6F4D9-1A79-47AF-9D2F-CFAAF5155663}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ExpenseTracker
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=ExpenseTracker-Setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\ExpenseTracker.exe

[Files]
Source: "dist\ExpenseTracker\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Expense Tracker"; Filename: "{app}\ExpenseTracker.exe"
Name: "{autodesktop}\Expense Tracker"; Filename: "{app}\ExpenseTracker.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
; User data lives in %LOCALAPPDATA%\ExpenseTracker and is intentionally retained.
