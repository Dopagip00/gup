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

## v2.4决策验证与真实订阅云端AI验收

- 来源提交0f24d97bd3be020bfa72b76ae8f0a10106af69b8；发布运行：https://github.com/Dopagip00/gup/actions/runs/39216903742 。北京时间15:34:52开始、15:39:55结束，research、deploy、health、cloud-status全部success；源码push按设计跳过两个长线阶段。公开manifest版本2.4.0，generated_at=2026-10-19T15:38:37.258162+08:00，data_date仍为2026-10-16，没有把盘后重算冒充今日行情采集。
- 沿用固定岭回归点预测，升级ridge-cycle-v3、purged-expanding-3x60-decision-v1：先固定跨分段不重叠的机会，再按当时预测是否超过0.30个百分点成本压力选择交易。完整三段、合并/最近选择数30/10、同时胜现金及同网格被动基准，均为独立硬门槛。后端重新计算原始机会和全部统计，不能用伪造eligible通过；披露亏损尾部及假设回撤，不称为真实成交收益或未来概率。当前没有合格短线候选，不放松门槛强行推荐。
- 实际SSE31天风险标题查询完成9组、完整分页；风险观察协议official-sources-v3-risk31-v1独立核验来源收据。31天窗口不等于风险到期解除，标题目录核验不等于已读公告PDF正文。600241在近期急涨、连板与模型/成本验证下被否决；601811、688305亦未通过，三股线上详情均eligible=false且四个买入价位为空。
- 已在用户登录的ChatGPT Work真实创建并启用两个订阅云端任务：沪研云端AI盘前风险复核（ID 6ad5c536be24819192c01fcd32dc3873，工作日08:25 Asia/Shanghai）；沪研云端AI晨报复核补偿（ID 6ad5c53ea0408191a84cfacb83578006，工作日09:10）。任务详情实际显示活跃、运行位置云端和Asia/Shanghai，关机不依赖本机调度；先核验官方SSE交易日，休市跳过。没有创建本机替代任务、没有接入另付费API、没有新设API密钥。
- 任务所在记录：https://chatgpt.com/c/6ad5c2d4-4970-83ec-8079-2f49f9ec2867 。网页手动实跑使用GPT-6.1 Sol，15:21:08开始、15:25:31完成，真实读取公开研究与认证GitHub，提交research/ai-reviews/2026-10-19.json且回读一致，唯一文件提交72e98766cb453c412740b3662ec61197cbafeac8。记录覆盖3只反馈股、6个实际来源、2项风险否决，诚实注明部分覆盖和未读PDF；status=late，不授权买入。线上system已实际显示valid=true、morning_qualified=false、checked_at=15:38:01。
- 首次真实计划触发仍待发生；按已保存计划推算下一工作日为10月20日08:25/09:10，但任务服务尚未提供next_run_time。定时接口没有模型锁定字段，不能将手动GPT-6.1 Sol等同于未来固定模型配置或保证准点。现有订阅用量与云端服务仍约束实际执行。参考官方说明：https://learn.chatgpt.com/docs/automations 。
- AI只补充负向风险核验，不能提高未通过模型的股票资格；缺失、部分、迟到或错误记录暂停新买入。完整盘前记录需覆盖真实合格候选且来源/时间通过验证；AI记录push可触发发布。今天已发布日报仍保留09:53:39.662239、late=true、morning_ready=false、buy=null；归档SHA256仍为53ec0c3ccf13ba6c1ded415edb891a61846df454842fd87369e751eef0f80e46，运行日志确认没有改写旧建议。
- 203项平台、113项GitHub适配、28项前端检查通过，共344项；真实本机重算2320只中2316成功、4缺失且保守禁用，网络未采集。真实云端重算当前2318只中2316有效、2缺失，公开四JSON健康检查success。seed压缩上限由95MiB调整为128MiB以容纳新增证据，原始700MiB与4MiB分片限制仍在；远端seed原始分片没有上传替换。
- 用户最新偏好已写入交接和操作指引：只有明确要求才备份；本轮没有创建源码ZIP、数据库快照或旧清单备份。维护校验清单允许原位更新，默认不另存旧文件。

## v2.4.1手机缓存容量修复

- 来源提交bcadb7de2bd09f0624e78967f9cbc39b6933288f；真实自动发布：https://github.com/Dopagip00/gup/actions/runs/39220543186 。北京时间16:07:35开始、16:09:42完成，research、deploy、health、cloud-status均success，两个长线阶段按push跳过。公开manifest前后稳定为2.4.1，generated_at=2026-10-19T16:08:38.779232+08:00。
- v2.4新增验证证据后，公开首页曾达到约19.6MB UTF16，超出常见手机缓存空间。此补丁仅压缩表格投影：完整原始机会、风险阈值和解释保留在逐股详情；明确拒绝行省略重复协议和通过标记，缺省资格仍严格关闭。真正合格行保留全部前端硬门槛，不以省略字段放行股票。
- 生产2318行实际dashboard：UTF8=1,273,842字节，JSON字符数977,253，UTF16=1,954,506字节；含实际manifest、calendar、daily-plan及storage key的完整首页缓存约1,959,964字节，低于3MiB总预算。保留缓存schema3，超大旧条目拒收；内存和localStorage均按UTF16总量驱逐，优先保留最新首页与日报；遇真实QuotaExceeded仅有界回收本站自有旧条目后重试。不保证所有手机空间，但存储失败不会破坏在线读取。
- 203项平台检查维持通过，GitHub适配115项、Node前端32项重新通过，共350项；包含大快照容量、真实配额错误、离线撤销买入、缺省资格关闭和合格行保留门槛。44份本机源码与公开engine逐字节同步；本机/api/health返回ok=true，/api/system确认2.4.1。
- health实际于2026-10-19T16:09:32.602186+08:00读取四份公开JSON，ok=true、version=2.4.1、attempts=1；morning.ready=false且require_morning=false，盘后发布成功不冒充盘前晨报就绪。
- 线上system实际显示AI late、valid=true、morning_qualified=false，checked_at=2026-10-19T16:08:02.944593+08:00。600241时代万恒、601811新华文轩、688305科德数控详情均为ridge-cycle-v3，risk_status=blocked、blocked=true、eligible=false，entry_low/entry_high/stop_loss/take_profit四项全null。公开daily-plan逐字段等于main归档原10月19日日报；原归档SHA256仍为53ec0c3ccf13ba6c1ded415edb891a61846df454842fd87369e751eef0f80e46，单条日报规范化SHA256为4986a12efe00614cc9894d79c48195e9a81a4b31688e3ac79b4bca2b8f43929f。本次仍未创建备份。

## v2.4.2日期切换与收盘更新修复

- 用户10月19日20:58反馈仍显示16日，切换19日出现公开快照失败。真实公开manifest仍是16:08发布、data_date=16，reports.json只有16日；当天晨报日期19不等于当天行情快照。根因是原采集仅限08点、09:30后一律跳过，没有收盘采集；日期输入又直接拼接不存在的19日报告URL并把404当整体断连。此前发布成功验收遗漏了收盘后数据更新，本条更正该范围。
- 21:00左右实际读取行情源，指数、600241、601811、688305均已返回19日完整日线。新health于21:06:58.981536真实检查旧2.4.1网站，返回ok=false/status=stale、snapshot_readable=true、data_ready=false、expected_data_date=19、actual=16，证明确实能识别本次问题。
- 新增北京时间工作日16:20/17:10 collect，仍核验官方SSE交易日；原六个盘前时点保留。收盘collect使用真实时刻与独立github_close_collect收据，不更新晨间资讯/AI或github_collect。晚间publish缺少同日有效收盘覆盖时补采；至少99%同日行情、有效指数及当前模型/风险协议才复用，失败明确error和非零退出。原晨报target16及原发布时间保持，close_research.target19另记，不能补造盘前建议。
- 日期先查真实档案清单；未生成日期明确显示所选与实际行情日，保留选择、取消买入/价位、隐藏不存在的CSV，不把缺档当整体断连。真正网络/格式失败仍报错；同批发布和清单CDN延迟有回归。页面分列行情日期与公开快照更新时间。
- 16:20后健康检查要求最新完整交易日行情；16:00—16:19允许等待发布但明确状态，真实已完当日日线可以先通过。晨报有效性与收盘行情独立，绝不要求把原晨报改为收盘数据。相关Python126项、Node41项，共167项重新通过；本机44份源码同步，远端初始seed未修改。本次未创建备份。实际新版本发布及补采结果待后续运行证据记录。
- 首次修复来源8af86fc5a3537a26c132fe12cf9a0f7779461097触发运行39257769609：https://github.com/Dopagip00/gup/actions/runs/39257769609 。21:09:55.306097至21:10:05.834302在获取官方证券列表阶段失败，尚未下载股票（total=0）；从失败研究artifact内存只读查询得到真实error为query.sse.com.cn连续三次HTTP403。研究正确非零退出，新站未部署，没有把16日伪标19日。
- 同一artifact的universe_info真实为mode=shanghai/count=2318/fetched_at=2026-10-19T10:04:25.964452+08:00，active=unique2318；来自本日早先完整官方名单核验。后续仅显式收盘更新、同一官方交易日、有效原时间及完整数量/代码/名称条件下允许网络失败时复用它；保留原fetched_at并公开cache_reused/source_error，不改写名单核验时间。过期、未来时间、部分或格式错误名单、盘前及未授权路径仍失败。2000只仅为防止误用小名单的保守缓存门槛，不代表实际沪市数量。
- 另增加相同官方域名commonQuery.do备用路径，逐条独立执行完整名单校验；它与原接口同host，不能宣称解除云端403。仅两条路径均为真实网络失败才是MarketNetworkError，任何JSON/编码/total/重复等数据错误都不能授权缓存回退。下一次真实云端运行将验收该边界及19日行情。
- 网络回退修复后，平台226项、GitHub适配128项、前端41项，共395项检查通过。包含2318规模同日名单缓存、默认禁用/跨日/小名单/错误总数/未来时间/非沪市代码/坏名称/错误来源拒绝，正常live来源记录、原时间保留和页面透明提示。远端种子分片仍保持原始字节，研究缓存由实际运行延续；没有用本机19日数据库或价格替换云端补采。

