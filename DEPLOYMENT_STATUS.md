# 沪研部署验收 / 2026-10-18（Asia/Hong_Kong）

当前公开网站：https://dopagip00.github.io/gup/
仓库：https://github.com/Dopagip00/gup
仅公开研究和手动交易参考；不连接券商、不代下单。个人完整迁移包不上传公开仓库。

## 已验收

- GitHub连接登录为Dopagip00，gup仓库公开、默认main、具有管理与写权限。仓库既有源码和4MiB分片公开seed保留，没有用迁移包旧版覆盖远端runner/prepare_source。
- 修复提交：0cb306fc8501d0fe9ea2020b2ba1dc1a15832c20。UTF-8 BOM修复Windows PowerShell5.1启动解析；启动cmd透传参数。9个本机ps1解析、真实根入口启动、重复启动同一进程、健康/首页/数据接口通过，便携Python3.12.10可用。
- 首次真实运行：https://github.com/Dopagip00/gup/actions/runs/39123921471 。push触发，北京时间2026-10-18 19:36:29开始，19:38:55全部完成。research、deploy、fundamentals、deploy-longterm四阶段全部success；fundamentals从同次运行的研究/日报artifact恢复成功。
- 网站首页和每日交易计划已用浏览器实际访问；线上data/manifest.json于19:38后核验，generated_at=2026-10-18T19:38:27.227898+08:00，data_date=2026-10-16，stock_files=2321（含指数），run.stage=fundamentals、run.status=closed。今天休市，没有伪造当日采集或买卖建议。
- 首次产物：huyan-research-cache 58,615,596字节（1天）；huyan-daily-plans 240字节（90天）；github-pages 3,842,098字节（1天）。长期储存与Actions用量仍以GitHub实际记录为准。
- 最新GitHub适配Python38/38、页面日期保护Node4/4、本机迁移9/9检查通过。静态导出2340个文件、41.84MiB、2333份JSON严格解析；2320只证券中2316有效、4待补齐，85只指标股推荐排除；/gup/基址11项静态接口通过。未发现私人账户、订单、持仓、完整数据库或凭据。详细本机验收在根backups目录。

## 已配置的自动运行

只允许Dopagip00/gup动态默认分支自动研究和部署。用户已授权公开，因此变量缺省启用；ENABLE_SCHEDULED_RESEARCH=false暂停定时及源码push自动研究，手动默认分支运行仍可恢复；ENABLE_PAGES_DEPLOY=false停止所有部署。

默认分支的Python源码、engine、pages.js、seed、测试或工作流提交会自动publish，并沿用不可变日报归档、缓存恢复及后置长线链。README/本文件/独立research/daily-plans.json提交不触发，避免归档循环。四个.cache产物均显式include-hidden-files:true，路径精确到公开白名单文件，不上传整个隐藏目录。

仅保留北京时间工作日08:00、09:00两个cron，执行时再核验官方SSE日历。09:30后补跑不新生成当日买入建议。GitHub可能延迟，不能保证09:00整点。

## 验收边界与接续

首次发布和源码push自动触发已实际通过。真正交易日的08:00外部行情/新闻采集、09:00定时触发、完成时间、报价与日报质量尚未发生；2026-10-18周日closed不代表这些成功。下一待核验日期2026-10-19，仍以官方交易日历为准。没有合格候选不强行推荐，不把验证误差写成上涨概率。

后续沿用原仓库和研究程序，不需重新开发或让电脑常开。不修改Dopagip00.github.io主页仓库；ashare-cloud只保留历史原型，不继续Sites发布。

本机ashare-github当前保留完整gzip及本次生成的配套分片；公开seed原有远端字节未变，未把本机gzip校验值冒充远端值。研究源码按归一LF的Git blob SHA与远端逐一核对后重建engine；后续先改ashare-platform并测试，再运行prepare_source.py同步，审查公开文件后提交同一gup仓库。Git历史与授权不随个人迁移包复制。

## 跨次自动更新验收

第二次来源提交469e425c3ece3c56aab026b31c6f303fe03da59b自动触发运行39124348820：https://github.com/Dopagip00/gup/actions/runs/39124348820 。research、deploy、fundamentals、deploy-longterm再次全部success。research日志确认已从前一次运行成功下载研究缓存和独立日报artifact，再按真实周日时间publish=closed；研究缓存重新上传58,615,597字节，网站再次部署成功。由此实际验证源码提交自动发布、跨次缓存恢复与重复发布；仍不将此等同于尚未发生的交易日08/09定时采集验收。

