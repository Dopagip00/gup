# 沪研 GitHub 云端适配

目标仓库 `Dopagip00/gup`，计划网站 `https://dopagip00.github.io/gup/`。持有者已确认公开研究网站及仓库改名，目前源码尚未上传、网站尚未部署。GitHub Pages 为公开静态网站，原“仅本人登录查看”需求已由持有者的新选择替代。

GitHub Actions 使用 UTC cron，在北京时间每周一至周五08:00采集、09:00发布，运行时再依据上交所正式日历跳过休市日。Actions 可能排队、延迟，不能保证09:00整点通知；站点显示真实生成时间。09:30起不再新生成当日买入推荐，只保存真实晚发日报和前日建议的卖出参考。没有当日早间研究完成证明、同日估值、已核验来源新闻或合格模型，就不推荐新买入。

仅研究公开行情、财报和资讯，用户手动交易；无富途登录、账户、真实持仓或交易接口。D1买入、D2卖出参考逐笔管理，不是T+0。

## 目录与接口

- `engine/`：现有 `ashare-platform` 的标准库研究模块，至少包含 `core.py`、`market_data.py`、`strategy.py`、`forecast.py`、`longterm.py`、`research_refresh.py`、`daily_plan.py`，由发布准备过程复制，不带本机data或账户记录。
- `runner.py`：定时 `collect`、`publish` 与手动 `fundamentals` 阶段，均使用真实北京时间；不提供伪造历史运行时刻的命令参数。
- `news_collector.py`：`collect(now) -> morning_news`，包括 `date/status/as_of/items/risk_exclusions`，新闻至少有标题、HTTPS原始来源和真实发布时间。缺采集器、缺来源或核验失败标为unavailable。
- `export_static.py`：`export_site(output_dir, service, engine_dir=Path) -> metadata`，只导出公开研究JSON、静态网页及必要资源；页面没有Python服务端，也不执行采集。
- `seed/research.json.gz`：本机及完整迁移包保留的公开研究初始化文件；完整gzip不提交仓库。
- `seed/research.json.gz.parts.json` 和 `seed/research.part000.bin` 等：GitHub首次运行使用的相同gzip字节流，按4 MiB无损分片，末片可较小。清单记录顺序、大小及每片和完整gzip的SHA-256；必须同时保留清单与全部分片。恢复时优先读取本机完整gzip；缺少完整文件时，先逐片校验及重组，全部通过后再按相同研究白名单加载。缺片、改动、非法路径或重复顺序都会中止，不静默跳过。
- `.state/`：当前Actions运行的工作数据库，不能提交或直接上传。
- `.cache/export/`：严格清理后的跨运行研究artifact，仅含 `research.sqlite3` 和 `cache-meta.json`。
- `research/daily-plans.json`：完整公开建议历史的确定性档案，09点发布后由工作流提交到本仓库；无变化不提交。
- `.cache/plans-export/`：完整公开日报的小型独立备份artifact，保留90天。
- `public/`：一次构建的静态网站，默认不提交，由Pages artifact发布；不含账户资料或本机配置。

09点任务从最近一次研究artifact恢复SQLite，读取08点的真实完成时间及新闻核验状态；不会仅凭旧日期缓存标成08点更新成功。无可恢复缓存时读取seed，再合并仓库中的完整日报档案和最新小型日报artifact；种子不含当天08点完成证明，因此不能直接作为当天新买依据。原建议买入日、D2日、区间及止盈失效价保持原样；同日期已发布内容发生冲突则停止，不能用新内容覆盖旧建议。

08点先采新闻，再计算全沪市短线研究和同日报价，尽量复用日期与口径一致的640根历史缓存。长线不在这个时段逐只阻塞抓取财报：只保留已知日期快照，过期时禁用长线新推荐并明确提示待重估。09点短线站点部署完成后，同一次触发继续运行独立 `fundamentals` 阶段，再更新长线区域；不会重新发布或改写当日买卖建议，没有新增定时时刻。同日完整财务快照存在时仅复核报价，新日期重新完整核验。也可手动触发 `fundamentals`。短线工作流预算50分钟，后置长线预算90分钟；失败或超时明确记录，09点不补造成功。

大体积研究cache artifact暂设仅保留1天，不自动删除其他artifact，不启用任何付费设置。完整公开日报独立保存到仓库及90天小artifact，每次运行都会携带全部已有日报，不随大缓存过期而丢失周五原建议或假期后的D2跟踪。仓库档案无变化不提交；远端文件在本次checkout后被改动则拒绝覆盖，重新运行后再合并。写入只使用本仓库的临时 `GITHUB_TOKEN` 与该研究job的 `contents:write`，不需要个人Token，也没有 `actions:write` 删除权限。尚未在真实GitHub Actions运行，实际压缩体积、储存占用、配额和触发延迟都待上线后核验，不能仅凭当前设置声称不会产生配额问题。请在GitHub的Actions储存与账单用量页面查看实际情况。

## 首次配置（须先确认公开范围）

1. 将这些源码和公开seed放入持有者已确认的仓库，不上传本机数据库、聊天、密码、Cookies或令牌。
2. 仓库 Settings → Pages → Source 选择 GitHub Actions。
3. 确认 GitHub Actions 已启用，在 Actions variables 设置 `ENABLE_SCHEDULED_RESEARCH=true`、`ENABLE_PAGES_DEPLOY=true`；未设置时定时任务和对外部署默认关闭。
4. 手动运行 `collect`，检查全市场覆盖、新闻状态及研究artifact，再运行 `publish`。真实时间超过09:30时，没有新买建议属于正常保护。
5. 核验实际Actions触发时间、Pages访问、日报日期和发布时刻后，才可声称云端更新已工作。GitHub可能因仓库长期不活跃停用定时工作流，需要定期核验Actions状态。

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
