#ifndef BundlePath
  #error BundlePath must point to the signed Universal Knoa Host Bundle
#endif
#ifndef UpdaterPath
  #error UpdaterPath must point to knoa-update.exe
#endif
#ifndef TrustStorePath
  #error TrustStorePath must point to release-trust.json
#endif
#ifndef ProductVersion
  #define ProductVersion "0.0.0-dev"
#endif

[Setup]
AppId={{EB10999B-88F4-4AF3-B293-27432836BF89}
AppName=Knoa
AppVersion={#ProductVersion}
DefaultDirName={autopf}\Knoa
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
OutputBaseFilename=knoa-setup-{#ProductVersion}-windows
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Knoa Host
ChangesEnvironment=no

[Types]
Name: "node"; Description: "Knoa Node（推荐）"
Name: "hub"; Description: "Knoa Hub"
Name: "all"; Description: "Knoa Hub + Node"

[Components]
Name: "node"; Description: "Knoa Node"; Types: node all
Name: "hub"; Description: "Knoa Hub"; Types: hub all

[Files]
Source: "{#BundlePath}"; DestDir: "{tmp}\knoa-bootstrap"; DestName: "knoa-host.zip"; Flags: deleteafterinstall
Source: "{#UpdaterPath}"; DestDir: "{tmp}\knoa-bootstrap"; DestName: "knoa-update.exe"; Flags: deleteafterinstall
Source: "{#TrustStorePath}"; DestDir: "{tmp}\knoa-bootstrap"; DestName: "release-trust.json"; Flags: deleteafterinstall
Source: "Install-KnoaBundle.ps1"; DestDir: "{tmp}\knoa-bootstrap"; Flags: deleteafterinstall
Source: "Uninstall-KnoaHost.ps1"; DestDir: "{app}\install"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{tmp}\knoa-bootstrap\Install-KnoaBundle.ps1"" -Role node -BundlePath ""{tmp}\knoa-bootstrap\knoa-host.zip"" -TrustStorePath ""{tmp}\knoa-bootstrap\release-trust.json"" -UpdaterPath ""{tmp}\knoa-bootstrap\knoa-update.exe"""; StatusMsg: "正在安装 Knoa Node…"; Flags: runhidden waituntilterminated; Check: IsComponentSelected('node') and not IsComponentSelected('hub')
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{tmp}\knoa-bootstrap\Install-KnoaBundle.ps1"" -Role hub -BundlePath ""{tmp}\knoa-bootstrap\knoa-host.zip"" -TrustStorePath ""{tmp}\knoa-bootstrap\release-trust.json"" -UpdaterPath ""{tmp}\knoa-bootstrap\knoa-update.exe"""; StatusMsg: "正在安装 Knoa Hub…"; Flags: runhidden waituntilterminated; Check: IsComponentSelected('hub') and not IsComponentSelected('node')
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{tmp}\knoa-bootstrap\Install-KnoaBundle.ps1"" -Role all -BundlePath ""{tmp}\knoa-bootstrap\knoa-host.zip"" -TrustStorePath ""{tmp}\knoa-bootstrap\release-trust.json"" -UpdaterPath ""{tmp}\knoa-bootstrap\knoa-update.exe"""; StatusMsg: "正在安装 Knoa Hub 和 Node…"; Flags: runhidden waituntilterminated; Check: IsComponentSelected('hub') and IsComponentSelected('node')

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\install\Uninstall-KnoaHost.ps1"""; Flags: runhidden waituntilterminated

[Code]
function IsComponentSelected(Name: String): Boolean;
begin
  Result := WizardIsComponentSelected(Name);
end;

