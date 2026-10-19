# 项目交接 · 2026-10-19 / v2.3.0

先读本文件、AGENT_OPERATIONS.md、MIGRATION.md和AGENTS.md，继续修改现有项目，不从头重建。

- v2.3修复手机读取一次失败即断连：公开JSON有界重试、同次部署校验、完整快照离线缓存；离线禁用新买入和价位。默认只列合格候选，未通过研究需显式展开，详情“推荐值”改为技术规则分。anti-chase-v1新增近期连板、急涨、MA20偏离、放量急跌等否决，缺原价/除权参考时不放行；最高入场上限须本人核对D1开盘价，09:00未知。旧缓存需按新规则重算，不能只沿用eligible标记。
- 用户10月19日最新授权云端补偿：08:00/08:17/08:43采集，09:00/09:07/09:17发布，均核验SSE交易日；09点后不新启动耗时重算，同日合格缓存复用原完成时间。盘前未准备只记pending，允许补偿重试；真正published仍不可覆写。最终获得写锁后重核09:30，越界取消买入和事前预测。长线只跟尾补偿，公开健康检查先于长线，失败记入Actions。GitHub可能延迟，不能保证准点或全天网络可用。本机定时任务不能作为关机后保障。最新部署证据见相邻ashare-github/DEPLOYMENT_STATUS.md；本条替代下方旧版“仅两个cron”的历史描述。

- 当前模式仅manual_research，用户手动操作。已删除旧七个Codex定时任务；本机旧时钟和券商入口已禁用。agent_runner.py仅接受08:00与09:00研究，不连接券商。
- 目标：北京时间每上交所交易日08:00收集新闻/公告及行情，09:00发布今日买入和前一建议买入日的卖出参考。每日逐笔D1/D2，可同时新买研究和旧建议卖出研究。建议不代表实际持仓。
- 主预测严格为D1开盘到D2收盘，ridge-cycle-v2沿用固定岭回归点预测，新增3段各60样本的扩展窗口验证，每段训练与验证隔离5样本；汇总与最近段均须胜过历史中位数和零收益基准，至少2/3段胜过两者。检查非重叠方向、连续基准交易日、每回合0.30个百分点研究成本压力；未证明稳定优势，当前没有模型合格短线。误差区间未经概率校准，不能把历史方向率当本次上涨概率。
- v2.2准确性维护：官方公告覆盖至少4天且跨长假回溯到最近完整行情日（最多31天），每项保留来源/发布时间/实际观察时间；08/09原任务内核验当日官方SSE停复牌列表，失败或空列表未核准即暂停新推荐。仅覆盖明确声明的SSE公告和央视财经来源，不声称掌握全部消息或核验每份公告正文。源码升级会在既有公开缓存上原子重算模型，严格截断到原报告日期，重算不冒充晨间采集、不覆写旧日报。新日报在真实盘前窗口保存最多20条事前预测，D2完成后独立评估，拒绝回填历史成绩。详见相邻ashare-github/ACCURACY_AUDIT.md。
- 全沪市证券列表2320，最近完整交易日2026-10-16。有效2316；指标股推荐排除85；非指标2235中有效2233。银行/证券/保险/石油石化、名单大型权重及已知总市值5000亿元以上排除。
- 长线全部同日估值合格股836只均核验，财务完整836、规则合格175；不是全市场最优结论或未来前景保证。默认不再限制40只。
- 最新严格技术与模型双门槛没有合格短线买入，不得放松验证来强行满足“每天必推荐”。可以展示明确标注的研究价格。
- 本机纯Python标准库。runtime目录为Python.org官方3.12.10 Windows x64嵌入版，已核验官方下载MD5。runtime.ps1优先选择此目录，解压后start.cmd即可启动；其他系统需自行安装Python3.10+。
- 数据：data/market.sqlite3，SQLite WAL，一致备份使用portability.py。完整备份保留数据；export-update/apply-update仅更改代码，更新前自动备份且不覆盖data。
- 当前发布目标为相邻ashare-github：用户已明确同意把空仓库Dopagip00/deng.github.io改名为gup，并公开发布研究数据，目标网址https://dopagip00.github.io/gup/。此前仅本人登录的要求已由本次公开授权替换；普通GitHub Pages不提供这一私人访问承诺。
- GitHub适配沿用本机研究引擎，由prepare_source.py复制engine并从白名单公开研究表/键生成seed/research.json.gz；不上传本机SQLite、真实账户、私人配置、密码、Cookies或令牌。08:00采集与09:00发布由GitHub Actions执行，交易日由SSE日历再次核验；Actions可能排队延迟，不保证精确09:00。迟于09:30不新生成当天买入建议，保留旧建议的卖出参考。
- 2026-10-18晚维护验收：GitHub账号Dopagip00及gup管理/写权限已核实；源码与公开seed已存在。修复提交0cb306fc8501d0fe9ea2020b2ba1dc1a15832c20已进入main，Actions push运行39123921471的research、deploy、fundamentals、deploy-longterm全部success。网站https://dopagip00.github.io/gup/已实际访问，manifest真实生成时间2026-10-18T19:38:27+08:00，行情日2026-10-16、2321详情（含指数），当日closed。首次发布及同次缓存恢复已验收；真正交易日08/09定时触发、外部采集及合格日报仍待后续真实记录，不把周日closed当作采集成功。
- 当前工作流仅对Dopagip00/gup的动态默认分支默认启用；源码push会自动publish，research/daily-plans.json及根README提交不触发，避免归档循环。ENABLE_SCHEDULED_RESEARCH=false停止定时/push自动研究，手动恢复仍可运行；ENABLE_PAGES_DEPLOY=false停止全部部署。仅两个原定08/09 cron，交易日/09:30门槛不变。四个.cache白名单产物已显式允许隐藏目录上传，实际研究缓存压缩58,615,596字节、保留1天；日报240字节、保留90天。详见相邻ashare-github/DEPLOYMENT_STATUS.md。
- 本机启动已修复：全部9个ps1采用UTF-8 BOM兼容Windows PowerShell5.1；根和平台启动cmd支持透传参数。Windows PowerShell5.1解析9/9、根入口真实启动/重复启动及健康/首页/数据接口均通过；便携Python3.12.10标准库可用，不需额外安装。原始文件与验收记录保留在根backups/local-startup-fix-20261018-192630。
- 后续改动先修改ashare-platform并测试，再在ashare-github运行python prepare_source.py --platform ../ashare-platform，检查只含公开研究内容后提交push到同一仓库。无需在新电脑重做平台；网站正常运行后，迁移电脑不需要重建云端任务。
- ashare-cloud为保留的Vinext/Cloudflare Worker/D1历史原型，原.openai/hosting.json绑定appgprj_6ad41f66c1808191a7c13d7fc4b3f00b及预注册地址保留。此前构建检查通过，但尚未部署、初始化D1或注册08/09任务。当前不继续Sites发布，不再create_site，也不为其新建任务。
- 完整迁移包包含本机一致快照、GitHub源码/公开seed及历史cloud源码。GitHub的engine可从本机源码重建，state/public及Actions产物不当成迁移来源；最新云端日报与研究artifact需从原仓库独立获取。聊天、授权、Cookies及令牌不进入归档，新电脑通过服务本身重新登录。
