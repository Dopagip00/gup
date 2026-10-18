# 项目交接 · 2026-10-18 / v2.1.0

先读本文件、AGENT_OPERATIONS.md、MIGRATION.md和AGENTS.md，继续修改现有项目，不从头重建。

- 当前模式仅manual_research，用户手动操作。已删除旧七个Codex定时任务；本机旧时钟和券商入口已禁用。agent_runner.py仅接受08:00与09:00研究，不连接券商。
- 目标：北京时间每上交所交易日08:00收集新闻/公告及行情，09:00发布今日买入和前一建议买入日的卖出参考。每日逐笔D1/D2，可同时新买研究和旧建议卖出研究。建议不代表实际持仓。
- 主预测严格为D1开盘到D2收盘，固定岭回归、按时间末60样本验证、训练与验证隔离5样本。误差区间未经概率校准。不能保证准确或收益，不能把规则回测混报成模型策略绩效。
- 全沪市证券列表2320，最近完整交易日2026-10-16。有效2316；指标股推荐排除85；非指标2235中有效2233。银行/证券/保险/石油石化、名单大型权重及已知总市值5000亿元以上排除。
- 长线全部同日估值合格股836只均核验，财务完整836、规则合格175；不是全市场最优结论或未来前景保证。默认不再限制40只。
- 最新严格技术与模型双门槛没有合格短线买入，不得放松验证来强行满足“每天必推荐”。可以展示明确标注的研究价格。
- 本机纯Python标准库。runtime目录为Python.org官方3.12.10 Windows x64嵌入版，已核验官方下载MD5。runtime.ps1优先选择此目录，解压后start.cmd即可启动；其他系统需自行安装Python3.10+。
- 数据：data/market.sqlite3，SQLite WAL，一致备份使用portability.py。完整备份保留数据；export-update/apply-update仅更改代码，更新前自动备份且不覆盖data。
- 当前发布目标为相邻ashare-github：用户已明确同意把空仓库Dopagip00/deng.github.io改名为gup，并公开发布研究数据，目标网址https://dopagip00.github.io/gup/。此前仅本人登录的要求已由本次公开授权替换；普通GitHub Pages不提供这一私人访问承诺。
- GitHub适配沿用本机研究引擎，由prepare_source.py复制engine并从白名单公开研究表/键生成seed/research.json.gz；不上传本机SQLite、真实账户、私人配置、密码、Cookies或令牌。08:00采集与09:00发布由GitHub Actions执行，交易日由SSE日历再次核验；Actions可能排队延迟，不保证精确09:00。迟于09:30不新生成当天买入建议，保留旧建议的卖出参考。
- 本文更新时尚未确认GitHub改名、上传、Pages部署或有效调度。接续需分别核对仓库、Pages实际URL、Actions变量/日志、公开数据内容与日报真实发布时间，不能将源码就绪当作已上线。后续验收结果以实际部署记录补充。
- 后续改动先修改ashare-platform并测试，再在ashare-github运行python prepare_source.py --platform ../ashare-platform，检查只含公开研究内容后提交push到同一仓库。无需在新电脑重做平台；网站正常运行后，迁移电脑不需要重建云端任务。
- ashare-cloud为保留的Vinext/Cloudflare Worker/D1历史原型，原.openai/hosting.json绑定appgprj_6ad41f66c1808191a7c13d7fc4b3f00b及预注册地址保留。此前构建检查通过，但尚未部署、初始化D1或注册08/09任务。当前不继续Sites发布，不再create_site，也不为其新建任务。
- 完整迁移包包含本机一致快照、GitHub源码/公开seed及历史cloud源码。GitHub的engine可从本机源码重建，state/public及Actions产物不当成迁移来源；最新云端日报与研究artifact需从原仓库独立获取。聊天、授权、Cookies及令牌不进入归档，新电脑通过服务本身重新登录。
