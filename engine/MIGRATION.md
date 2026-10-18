# 换电脑、备份和后续更新

本项目包括本机研究程序、当前GitHub公开网站适配，以及保留的历史Sites原型。用户已同意把空仓库改名为 `Dopagip00/gup` 并公开研究数据，目标网址 `https://dopagip00.github.io/gup/`。普通GitHub Pages不再提供此前仅本人登录的访问承诺。是否已上传、部署、启用08:00/09:00任务，应查看实际GitHub状态；生成一个ZIP不会发布网站、启用任务或转移登录。

当前业务是手动交易：北京时间08:00更新资讯，09:00给出买入区间、止盈/止损参考及前日建议的D2卖出观察价。GitHub Actions实际启用并验收后独立运行，不依赖电脑常开；计划可能排队、延迟，不能保证精确09:00。09:30后补跑不新生成当天买入建议。本机目录保留研究数据、历史报告和既有记录，方便恢复和继续开发。

## 完整项目包与单机备份的区别

要搬走整个项目，使用完整迁移包根目录的 `打包整个项目.cmd`，不要只取下述单机备份。完整包包含 `ashare-platform`（本机程序、一致SQLite快照、历史日报、官方便携Python）、`ashare-github`（当前发布源码和公开研究seed）、`ashare-cloud`（历史原型与原hosting绑定），以及总清单校验和后续打包工具。

解压完整包到新空目录，双击 `验证完整包.cmd`，通过后双击 `启动本机.cmd`。本机数据与研究无需重新抓取或从头开发。GitHub生成的 `engine` 为本机源码副本，完整包省略这一重复目录；`state/.state`、`public`、Actions artifact和Git仓库凭据也不随包搬走。需要运行或提交GitHub适配前，在完整包根目录执行：

```powershell
.\ashare-platform\runtime\python.exe .\ashare-github\prepare_source.py --platform .\ashare-platform
```

此操作重建engine并从白名单市场表和研究键更新 `seed/research.json.gz`，不将本机完整数据库或个人配置复制到公开仓库。公开seed保留研究缓存和建议日报，云端后续每日数据由工作流采集并经研究artifact连续恢复。迁移包不会自动包含打包之后才在GitHub产生的日报，需从原仓库/研究artifact独立获取最新状态。

后续修改仍以 `ashare-platform` 为研究引擎源码，测试后重新运行上述准备命令，再通过本人GitHub授权向同一 `Dopagip00/gup` 仓库提交push。网站静态导出不等于Python服务端，不能仅复制本机HTML或更改engine副本而遗漏原源码。新电脑通过GitHub自己的登录授权，不把令牌放入源码或迁移包。已正常运行的GitHub网站和任务无需因换电脑重建。

下面的 `backup.cmd`、`export-update` 和 `apply-update` 只处理 `ashare-platform` 单机项目。根目录完整打包脚本可用 `--platform`、`--github`、`--cloud` 指定三个目录，默认是脚本同级的对应目录。

## 1. 带走完整项目

在项目目录运行 `backup.cmd`。也可以指定存放位置：

```powershell
.\backup.ps1 -Output "D:\Transfer\huyan-full.zip"
```

等价命令（Python 3.10+，不需要第三方包）：

```powershell
python portability.py backup --output "D:\Transfer\huyan-full.zip"
python portability.py verify --package "D:\Transfer\huyan-full.zip"
```

完整备份包含项目源码、界面文件、配置、`data` 中的数据库、报告、日志，以及 `migration/session-handoff.md`。SQLite 使用官方 backup API 取得一致快照，包含已经提交到 WAL 的数据，不直接复制正在变化的数据库文件。其它文件按读取时刻保存；为获得同一批报告，建议先暂停本地更新再备份。

备份排除虚拟环境、缓存、历史备份、浏览器个人资料、Cookies、密钥及按名称识别的凭据文件；包内官方 `runtime` 便携运行环境保留。发现带密码/API密钥字段的JSON会拒绝打包；数据库KV中的登录和本机任务状态会移除或清理。项目本来就不应保存账户密码；不要把密码写进代码、日志或普通文本后期待归档工具识别所有形式的秘密。

`manifest.json` 记录每个文件的 SHA256。校验能发现损坏和内容不一致，不能证明发件人身份；只接受可信来源的更新包。输出文件存在时不会覆盖。

## 2. 新电脑恢复

1. 本次完整迁移包已含 runtime/ 官方 Windows 64位 Python 3.12.10，无需安装，解压后即可启动。其他系统或不带 runtime 的包需 Python 3.10+；没有必装的第三方 Python 依赖。启动脚本优先使用项目 runtime，再查找 Codex、.venv 和系统 Python。
2. 把完整 ZIP 解压到一个独立目录，例如 `D:\Projects\huyan-ashare`。将原始 ZIP 也保留一份，在解压后的项目目录执行上面的 `verify` 命令。
3. 若使用工具恢复，可先从可信备份提取 `portability.py` 到临时目录，然后运行：

```powershell
python portability.py restore --package "D:\Transfer\huyan-full.zip" --destination "D:\Projects\huyan-ashare"
```

恢复目标必须是新建或空目录。工具会先校验全部路径和哈希，禁止把完整备份覆盖到已有使用记录的项目。

4. 在 Codex 中打开恢复后的项目文件夹，让新任务先读 `PROJECT_STATE.md`、`AGENT_OPERATIONS.md`、本文件及 `migration/session-handoff.md`，即可基于已有代码继续修改。缺少的说明文件以项目实际内容为准。
5. 本地备用界面用 `start.cmd` 启动。它只提供本机地址，不等于互联网部署；手机看公开GitHub网站使用核验过的Pages网址，无需网站个人登录。

程序使用项目相对目录。若设置了 `ASHARE_DATA_DIR` 且指向项目外，备份会暂停，要求先把业务数据迁回项目 `data` 并取消该变量，防止漏掉外置数据库。

## 3. 以后只更新代码，保留数据

在修改代码的电脑上生成更新包：

```powershell
.\backup.ps1 -CodeOnly -Output "D:\Transfer\huyan-code-update.zip"
# 或
python portability.py export-update --output "D:\Transfer\huyan-code-update.zip"
```

更新包只包含根目录程序/脚本/说明/version.json，以及 `static`、`tests`、`docs`、`scripts` 中白名单类型的文件。不会携带 `data`，也不会覆盖接收电脑的数据、持仓、交易记录和本地业务配置。其它独立云端源码目录应通过其部署流程发布，不能假定本地代码包会更新云站点。

在使用电脑上先运行 `stop.ps1`，等待数据同步结束，再执行：

```powershell
.\apply-update.cmd "D:\Transfer\huyan-code-update.zip"
# 或
python portability.py apply-update --package "D:\Transfer\huyan-code-update.zip"
```

工具会检查默认 8765 服务端口和 18767 同步锁端口。只要被占用就停止更新，明确提示关闭对应程序；不会强杀未知进程。若启动平台时改过端口，使用 `apply-update.ps1 -Package ... -Port 新端口`。

更新前自动生成 `backups/pre-update-时间.zip`，其中包含原代码和 SQLite 一致快照；然后校验全部哈希、项目标识、路径与数据库版本，再逐个原子替换代码。更新失败会尝试回滚已替换代码。出现 `migration/update-in-progress.json` 时，按该文件指向的备份检查恢复后再更新；业务数据不会被更新包覆盖。

同版本号可以补发修复，低版本代码包会被拒绝。当前工具支持数据库结构版本 1；结构变化必须另做明确迁移程序，不能靠覆盖代码完成。更新不会自动删除旧代码文件；若某版本需要移除废弃模块，应在该版本说明中列明并单独执行迁移。

成功后再运行 `start.cmd`。GitHub代码修改另需重新运行 `prepare_source.py` 并提交push，再核验Actions及Pages发布，本工具不会访问云端账户。

## 4. 云端、计划和聊天分别处理

- 换电脑不需要重建已正常运行的GitHub站点；确认仓库授权、手机访问、原有08:00/09:00工作流及研究artifact连续性即可。公开Pages无需网站个人登录。
- `migration/schedules.json` 只是北京时间及相对项目路径的交接模板，不是任务注册记录，不包含旧电脑绝对路径或旧任务 ID，也不会激活旧交易执行计划。
- 若实际使用的是绑定某台电脑或 Codex 任务的计划，需要在新环境重新授权、绑定正确任务/项目目录并测试。不要把旧注册记录当成可用的云端任务。
- ZIP不复制Codex聊天历史、账户登录、浏览器Cookies、Actions artifact或云端数据库。登录与授权通过各服务自己的账户流程完成。完整迁移包含本机数据，属于个人备份，不得把整个ZIP上传到公开仓库。
- `ashare-cloud`原hosting绑定保留但未部署；当前不继续Sites路线，不再create_site或注册其旧计划。
- 保留修改后的整个项目及最新更新包，让下一次 Codex 任务继续读取交接文件，无需从头重建平台。
