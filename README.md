# 沪研 GitHub 云端适配

现有仓库 `Dopagip00/gup`，网站地址 `https://dopagip00.github.io/gup/`。源码和公开研究种子已上传，发布和运行的实际验收记录见 `DEPLOYMENT_STATUS.md`。GitHub Pages 为公开静态网站，原“仅本人登录查看”需求已由持有者的新选择替代。

GitHub Actions在云端运行，不依赖个人电脑开机。UTC cron对应北京时间08:00/08:17/08:43采集、09:00/09:07/09:17发布，运行时按SSE正式日历跳过休市。新增时点经用户2026-10-19授权。GitHub可能延迟，不能保证准点或100%网络可用；09点起不新启动耗时重算，09:30起不新生成买入建议。缺合格盘前研究时先pending，让补偿继续；已发布记录不可覆写。

v2.3修复公开快照加载失败与“推荐值”误导：有界重试、同批部署校验、最多7天完整离线研究缓存，离线取消全部新买入和价位；默认仅显示合格候选，未通过样本需显式展开。新增近期连板/急涨/均线偏离/放量急跌否决及入场上限；不是提高胜率的证明。案例见 [USER_FEEDBACK_REVIEW.md](USER_FEEDBACK_REVIEW.md)。

仅研究公开行情、财报和资讯，用户手动交易；无富途登录、账户、真实持仓或交易接口。D1买入、D2卖出参考逐笔管理，不是T+0。

v2.2使用三段时间验证、双基准、非重叠方向及每回合0.30个百分点的研究成本压力筛选。固定历史回放未证明模型优于零收益基准，不能声称准确率已经提高；没有证据时不强行推荐。方法、结果和数据限制见 [ACCURACY_AUDIT.md](ACCURACY_AUDIT.md)。新日报只在真实盘前窗口记录事前预测，等D2完成后才在“历史报告→事前预测跟踪”统计结果，不回填旧成绩。

源码升级时 `model_refresh` 在原公开缓存上按原报告日期截断重算并原子保存；失败清空旧预测资格及数字，不改历史日报，也不将重算当晨间采集。远端既有分片seed作为初始化基线保留，本次源码更新不替换大体积行情种子；首次恢复也会按新协议重算。

## 目录与接口

- `engine/`：现有 `ashare-platform` 的标准库研究模块，至少包含 `core.py`、`market_data.py`、`strategy.py`、`forecast.py`、`longterm.py`、`research_refresh.py`、`daily_plan.py`，由发布准备过程复制，不带本机data或账户记录。
- `runner.py`：定时 `collect`、`publish` 与手动 `fundamentals` 阶段，均使用真实北京时间；不提供伪造历史运行时刻的命令参数。
- `news_collector.py`：`collect(now=None, coverage_start=...) -> morning_news`，覆盖至少4天且包含官方最近完整交易日，最多31天；保留公告/央视财经来源收据、发布时间及实际观察时间。并非全媒体、全部消息或公告正文逐份核验。
- `session_risks.py`：在原08/09任务内核验当日上交所停复牌完整分页，明确股票代码才加入否决，转债不映射股票；失败、分页异常或空列表未经核准即暂停新推荐。来源覆盖完整不等于证明股票可成交，标题规则也不能替代人工阅读公告。
- `export_static.py`：`export_site(output_dir, service, engine_dir=Path) -> metadata`，只导出公开研究JSON、静态网页及必要资源；页面没有Python服务端，也不执行采集。
- `seed/research.json.gz`：本机及完整迁移包保留的公开研究初始化文件；完整gzip不提交仓库。
- `seed/research.json.gz.parts.json` 和 `seed/research.part000.bin` 等：GitHub首次运行使用的相同gzip字节流，按4 MiB无损分片，末片可较小。清单记录顺序、大小及每片和完整gzip的SHA-256；必须同时保留清单与全部分片。恢复时优先读取本机完整gzip；缺少完整文件时，先逐片校验及重组，全部通过后再按相同研究白名单加载。缺片、改动、非法路径或重复顺序都会中止，不静默跳过。
- `.state/`：当前Actions运行的工作数据库，不能提交或直接上传。
- `.cache/export/`：严格清理后的跨运行研究artifact，仅含 `research.sqlite3` 和 `cache-meta.json`。
- `research/daily-plans.json`：完整公开建议历史的确定性档案，09点发布后由工作流提交到本仓库；无变化不提交。
- `.cache/plans-export/`：完整公开日报的小型独立备份artifact，保留90天。
- `public/`：一次构建的静态网站，默认不提交，由Pages artifact发布；不含账户资料或本机配置。

09点任务从最近一次研究artifact恢复SQLite，读取08点的真实完成时间及新闻核验状态；不会仅凭旧日期缓存标成08点更新成功。无可恢复缓存时读取seed，再合并仓库中的完整日报档案和最新小型日报artifact；种子不含当天08点完成证明，因此不能直接作为当天新买依据。原建议买入日、D2日、区间及止盈失效价保持原样；同日期已发布内容发生冲突则停止，不能用新内容覆盖旧建议。

08点先采资讯，再计算短线与报价，复用同口径640根缓存；同日完整且模型/追高规则版本正确时幂等复用，不改原完成时间。09:00/09:07仅快速发布；09:17尾补偿在公开站点与晨报健康检查通过后继续长线，避免90分钟财务挤占前两次发布。长线不改日报，可显式手动fundamentals；同日完整财报仅复核报价。健康检查不因buy=null失败，零合格候选不强行推荐。

大体积研究cache artifact暂设仅保留1天，不自动删除其他artifact，不启用任何付费设置。完整公开日报独立保存到仓库及90天小artifact，每次运行都会携带全部已有日报，不随大缓存过期而丢失周五原建议或假期后的D2跟踪。仓库档案无变化不提交；远端文件在本次checkout后被改动则拒绝覆盖，重新运行后再合并。写入只使用本仓库的临时 `GITHUB_TOKEN` 与该研究job的 `contents:write`，不需要个人Token，也没有 `actions:write` 删除权限。2026-10-18真实Actions首次运行已成功，首份研究缓存压缩58,615,596字节、日报备份240字节；四个.cache产物按精确文件清单显式启用隐藏文件上传。实际长期储存占用、配额和定时触发延迟仍需持续以GitHub实际用量和日志核验，不能据首次结果保证未来无配额问题。

## 首次配置（须先确认公开范围）

1. 将这些源码和公开seed放入持有者已确认的仓库，不上传本机数据库、聊天、密码、Cookies或令牌。
2. 仓库 Settings → Pages → Source 选择 GitHub Actions。
3. 确认 GitHub Actions 已启用。用户已授权的 `Dopagip00/gup` 默认分支会自动研究和发布，无需预先创建变量；其他仓库、分支和标签不能运行研究或部署。可将 Actions variable `ENABLE_SCHEDULED_RESEARCH=false` 暂停定时及源码提交触发的研究，仍允许默认分支手动恢复；`ENABLE_PAGES_DEPLOY=false` 暂停所有 Pages 部署。显式设置为 `true` 也可启用。
4. 手动运行 `collect`，检查全市场覆盖、新闻状态及研究artifact，再运行 `publish`。真实时间超过09:30时，没有新买建议属于正常保护。
5. 核验实际Actions触发时间、Pages访问、日报日期和发布时刻后，才可声称云端更新已工作。GitHub可能因仓库长期不活跃停用定时工作流，需要定期核验Actions状态。

默认分支源码、静态资源、seed、测试或工作流修改触发真实时间publish和公开站点检查，沿用交易日和09:30保护；push不追加长财务。README与独立research/daily-plans.json提交不触发，避免归档循环。新增补偿仍待真实交易日验证，配置文件不证明准点执行。

托管与定时行为以 [GitHub Pages说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) 和 [GitHub Actions schedule说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) 为准。

本机调试（示例路径按实际目录调整）：

```sh
python runner.py collect --engine-dir ../ashare-platform --state-dir .state --output-dir public
python runner.py publish --engine-dir ../ashare-platform --state-dir .state --output-dir public
```

两条命令都遵守实际北京时间、交易日与发布窗口，不会自动绕过保护。

## 公开seed格式

```json
{"schema_version":1,"tables":{"securities":[],"histories":[],"signals":[],"runs":[]},"kv":{},"calendar":{}}
```

每个表是行字典列表；`histories.payload`、`signals.payload`可为JSON对象或已编码JSON字符串。键值仅允许公开的行情/研究/资讯状态与`daily_plan:YYYY-MM-DD`建议日报。不会恢复config、cash、orders、lots、trades、浏览器资料或旧券商状态。

artifact使用SQLite backup API取得一致源视图，再从白名单表与键创建全新数据库，避免删除过的私有数据残留在SQLite空闲页。SHA-256检验只确认传输完整性，不替代仓库与Actions访问控制。公开仓库的Actions产物也不能当作私人存储。

预测是含误差的研究估计，不能保证上涨或收益；没有合格候选时明确不推荐。网页保留数据日期、覆盖、模型验证误差、价格依据与真实发布时间。
