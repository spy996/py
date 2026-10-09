; ============================================================
; 科技项目管理系统 · Inno Setup 6 安装脚本
; 构建前置：先运行 build.bat 生成 dist\科技项目管理系统\ 暂存目录，
; 再由本脚本（ISCC.exe）编译成「科技项目管理系统安装包.exe」。
; 特性：LZMA2 压缩、简体中文、免管理员安装到 {localappdata}、桌面+开始菜单快捷方式、
;       卸载项、卸载时可选删除用户数据（默认保留）。
; ============================================================

#define MyAppName "科技项目管理系统"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "研发知识智能管理平台"
#define MyAppExeName "科技项目管理系统.exe"

[Setup]
AppId={{8A7B2C3D-4E5F-4A6B-9C8D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
; 允许用户在安装向导中修改安装目录
DisableDirPage=no
OutputDir=.
OutputBaseFilename=科技项目管理系统安装包
Compression=lzma2/ultra64
SolidCompression=yes
; 免管理员权限（安装到用户目录）；也兼容 32 位系统上的 64 位运行时探测
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 若存在 app.ico 则用作安装器图标，否则用默认图标
#ifexist "app.ico"
SetupIconFile=app.ico
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; 暂存目录（含 launcher.exe、_packaged、app\、runtime\、setup_wizard.py、.env.example）
Source: "dist\科技项目管理系统\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; 显式覆盖 _packaged 标记文件（防止空文件被跳过）
Source: "dist\科技项目管理系统\_packaged"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时可选删除用户数据目录（见 [Code] 里的确认对话框），默认保留
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Ret: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\RDPlatform\data');
    Ret := MsgBox('是否同时删除用户数据目录？' + #13#10 + DataDir + #13#10#13#10 +
                  '选择「是」将删除数据库、上传文件、备份与配置（不可恢复）；' + #13#10 +
                  '选择「否」将保留全部数据，便于以后重新安装继续使用。',
                  mbConfirmation, MB_YESNO);
    if Ret = IDYES then
    begin
      if DelTree(DataDir, True, True, True) then
        MsgBox('用户数据目录已删除。', mbInformation, MB_OK)
      else
        MsgBox('数据目录删除失败，请手动删除：' + DataDir, mbError, MB_OK);
    end;
  end;
end;
