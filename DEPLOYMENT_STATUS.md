# 沪研部署验收 / 更新至2026-10-19（Asia/Hong_Kong）

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

原北京时间08:00采集、09:00晨报保留；2026-10-19依用户新授权增加补偿，当前工作日08:00/08:17/08:43尝试采集、09:00/09:07/09:17尝试晨报。每次核验官方SSE日历，休市跳过。09:30后不新生成当日买入建议，已有日报不可改写。GitHub可能延迟，不能保证整点；下方新版本配置验收不冒充未来补偿的真实盘前执行成功。

## 验收边界与接续

截至2026-10-18，首次发布和源码push自动触发已实际通过，真正交易日的盘前采集与定时触发仍待验证；周日closed不代表采集成功。10月19日的后续实际记录见下方v2.2验收。没有合格候选不强行推荐，不把验证误差写成上涨概率。

后续沿用原仓库和研究程序，不需重新开发或让电脑常开。不修改Dopagip00.github.io主页仓库；ashare-cloud只保留历史原型，不继续Sites发布。

本机ashare-github当前保留完整gzip及本次生成的配套分片；公开seed原有远端字节未变，未把本机gzip校验值冒充远端值。研究源码按归一LF的Git blob SHA与远端逐一核对后重建engine；后续先改ashare-platform并测试，再运行prepare_source.py同步，审查公开文件后提交同一gup仓库。Git历史与授权不随个人迁移包复制。

## 跨次自动更新验收

第二次来源提交469e425c3ece3c56aab026b31c6f303fe03da59b自动触发运行39124348820：https://github.com/Dopagip00/gup/actions/runs/39124348820 。research、deploy、fundamentals、deploy-longterm再次全部success。research日志确认已从前一次运行成功下载研究缓存和独立日报artifact，再按真实周日时间publish=closed；研究缓存重新上传58,615,597字节，网站再次部署成功。由此实际验证源码提交自动发布、跨次缓存恢复与重复发布；仍不将此等同于尚未发生的交易日08/09定时采集验收。

## v2.2预测验证、风险来源与真实发布

- 来源提交9daec884ca414748b6f614d20248c6ec45ca0569新增三段时间验证、双基准、非重叠方向、0.30个百分点研究成本压力、官方停复牌否决和事前跟踪；公开方法与局限见ACCURACY_AUDIT.md。固定历史回放未证明模型优于零收益基准，不声称准确率或收益已经提高。
- 首轮运行39187260727研究和短线Pages成功，但财务阶段因“2316只已完成、仅4只重试失败”被误判整体error而停止。公开缓存确认了根因；缺行情的sh600293/sh601059/sh601198/sh688496没有被补造数据或恢复资格。
- 修复提交72e3cd235e51239fe86bb7d9bbf8e7c24afad64c：https://github.com/Dopagip00/gup/commit/72e3cd235e51239fe86bb7d9bbf8e7c24afad64c 。运行39187959655：https://github.com/Dopagip00/gup/actions/runs/39187959655 。北京时间10:00:59触发，research、deploy、fundamentals、deploy-longterm四阶段全部success，10:04:00完成。财务缓存回执真实current=2316、attempted=4、failed=4、status=partial，随后同日完整财报cached成功，报价received=2320、failed_batches=0。
- 此次从上轮缓存及独立日报备份恢复；重复发布保留原published_at=2026-10-19T09:53:39.662239+08:00，归档未重复提交。真实日报data_date=2026-10-16、late=true、morning_ready=false、buy=null、research_forecasts=[]。超过09:30没有补造买入或事前预测。
- 网站data/system.json的版本2.2.0已直接读取；浏览器“每日研究档案”实际显示事前跟踪0条及无成熟结果说明，审计链接有效。2321详情仍保留，仅公开研究；缓存全市场2268个可用模型、2254个完整分段验证、0个推荐合格。没有事前记录，不展示虚构准确率。
- 本轮基础检查：本机153/153、GitHub63/63、网页日期保护4/4；重试修复的相关14/14回归通过，包含2316当前+4失败的连续重试、全部失败仍error和并发回滚。本机源与engine的44个源文件同步检查通过，本机启动/健康/新版模型接口再次通过。
- 09:52查询时尚无schedule记录，随后实际出现schedule运行39188053278，created_at=2026-10-19T02:02:10Z（北京时间10:02:10），等待串行工作流后执行。由此确认定时触发已出现，但显著晚于盘前窗口；不据此宣称08:00/09:00准时采集或合格晨报已成功。当时仅原08/09两个cron；随后用户授权增加交易日补偿，现配置见上方及下方v2.3验收。不伪造历史时间。
- 该schedule运行最终success：https://github.com/Dopagip00/gup/actions/runs/39188053278 。日志真实RESEARCH_STAGE=collect，10:04:20.183513开始、10:08:53.062652结束；target_date=2026-10-16，status=partial，最新证券名单研究total=2318、ok=2316、failed=2，news_status=unavailable。research及deploy成功，后置两阶段按collect分支跳过。它是延迟到盘中的采集与网页刷新，不是09点定时晨报，更不是合格盘前买入证明；上方2320只的数值保留为前一轮缓存验收时刻的实证。

## v2.3手机可用性与追高风险验收

- 来源提交134dddd46683343b925eef885bc0fb890a643923；发布运行：https://github.com/Dopagip00/gup/actions/runs/39191498842 。北京时间10:45:58开始、10:50:29结束，research、deploy、health、cloud-status全部success；push快速发布按设计跳过fundamentals/deploy-longterm。没有新增权限、凭据或付费服务。
- 云端恢复39188053278的缓存及独立日报，再按当前模型和anti-chase-v1规则实际重算；全市场2318只中2316条携带当前风险版本、2条缺失者保守禁用，387条触发风险否决，eligible=0。新研究缓存60,203,176字节，日报产物825字节；远端公开seed原始分片未修改。
- 独立health于10:50:19.527246实际读取公开manifest、daily-plan、calendar和system，ok=true、status=healthy、attempts=1、errors=[]。manifest版本2.3.0、snapshot_schema_version/data_schema_version=1，generated_at=10:49:39.512317、data_date=2026-10-16、stock_files=2319（2318只股票及指数）、reports=1。此检查范围是四个公开JSON，不保证所有手机网络或全天可用性。
- 今天日报保留published_at=09:53:39.662239+08:00、late=true、morning_ready=false、buy=null；仓库归档前后SHA256均为53ec0c3ccf13ba6c1ded415edb891a61846df454842fd87369e751eef0f80e46，运行日志确认Public advice unchanged。没有改写旧日报或盘中补造事前买入。
- 工作日三次采集与三次晨报已配置，官方交易日再判定。未就绪的盘前草稿允许截止前重试；数据库写锁取得后再检查真实09:30截止，防止跨点写入。09:17尾补偿的独立健康检查严格要求真实晨报就绪；失败进入工作流失败状态，长财务等待检查后才运行。新补偿是否及时执行，仍须后续交易日真实记录验收。
- 手机公开页面增加有界重试、同批快照核验和最多7天的完整研究缓存；失败或离线时撤销新买入资格、隐藏买入价位。默认只列真正合格候选，未通过研究须主动展开，原误导性“推荐值”改为“技术规则分”。首次HTML/脚本加载仍需网络，缓存不等同于全天在线服务。
- 截至10月16日完整日线，601811的5根4次涨停形态、最大3连板和急涨偏离被否决；688305的-13.77%、量比4.117被放量急跌否决，二者买入价位清空。当前缓存不能完整复原手机旧画面。详见USER_FEEDBACK_REVIEW.md；固定风险约束没有证明未来收益改善，不把规则分当上涨概率。
- 本机平台172项、GitHub适配93项、前端21项，共286项检查通过；本机服务已重启确认2.3.0。停止本机服务时公开网站仍可读取，随后恢复本机服务成功；云端执行不依赖该电脑常开。公开发布仍可能受GitHub调度、数据源和手机网络影响。

