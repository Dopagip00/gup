# GitHub发布接续 / 2026-10-18

用户已明确同意：把其空的公开仓库 `Dopagip00/deng.github.io` 改名为 `gup`，公开发布研究网站，目标 `https://dopagip00.github.io/gup/`。不得修改已有 `Dopagip00.github.io` 主页仓库。仅用户手动交易，不接券商。

已通过公开GitHub API核实旧仓库存在、空仓库、默认main、Pages未启用。以上只是查询时状态，正式写入前重新核查，避免覆盖用户后来加入的文件。

用户完成了GitHub授权（曾遇到ChatGPT侧OAuth state not found，随后报告“已授权”）。插件目录现已确认 `installed: true`；当前这轮聊天的可调用工具列表尚未出现GitHub操作工具，因此尚未验证账号身份/仓库写入权限，也没有改名、push、启用Pages、配置仓库变量或运行Actions。不要让用户重复授权；先在下一轮重新发现GitHub工具及其附带技能。若可用则读取其技能并直接继续已授权工作。

下一步：验证Dopagip00写权限→重查仓库→把空仓库改名gup→上传本目录源码（含engine及market-only seed）→Pages Source=GitHub Actions→仓库变量ENABLE_SCHEDULED_RESEARCH=true、ENABLE_PAGES_DEPLOY=true→手动publish首次建站→实际访问/data/manifest.json和手机页面验收→真实collect网络可达与覆盖核验。今天周日，真实runner应closed，不伪造交易日08/09记录。不得宣称准确率/未来收益有保证。

测试：标准库引擎104项；GitHub Python24项、Pages日期保护4项；原始公开seed2320证券/2321历史/2321信号约60MB，仅市场研究白名单，无cash/config/orders/lots/trades或登录；真实cold bootstrap在周日完成closed、静态导出及324MB公开研究缓存创建。初始完整静态站点未上线。Pages列表已压缩到约1.6MB，个股详情按需加载；历史图仅导出date/close，完整行情留研究缓存。

每日时刻仅08:00/09:00北京时间，按官方交易日跳过休市。09:30以后不补造新买入。09短线发布后同次工作流更新长线，不增加时刻。大行情缓存仅1天；公开daily-plan档案在仓库与90天小artifact中单独保存，跨周末和假期不丢前日D2跟踪。Actions会排队/失败，不能保证精确9点。无个人Token、删除权限、付费设置。

迁移：本目录是可公开源码；相邻ashare-platform包含个人本机数据，只能用完整迁移包自用，不得把其data或完整迁移ZIP上传公开仓库。未来先改ashare-platform并测试，再运行prepare_source.py生成此处engine与公开研究seed；完整迁移包保留三个项目和接续文档。
