# 接续开发约束

在此项目修改前读PROJECT_STATE.md和AGENT_OPERATIONS.md，遵守用户最新的手动08:00/09:00与逐笔D1/D2模式。不得恢复旧券商自动交易或旧计划，不得从聊天中提取账户凭据写入文件。

本地Python程序是研究引擎源码；相邻ashare-github为当前公开发布适配。修改研究目标、排除规则、费用、原价换算或验证方式后，运行相关测试，再运行ashare-github/prepare_source.py同步engine与公开seed，提交到同一GitHub仓库。严格按交易日对齐，禁止用未来财报/行情或把验证误差冒充上涨概率。

使用官方SSE全量证券列表与日历，缺失/过期数据保留错误，不拼造数量或价格。不用ST、指标股或不合格模型作为强制首选。研究区间和通过验证的交易建议须明确区分。

保留data目录和历史日报，代码包不得覆盖data。用户2026-10-19最新偏好：仅在用户明确要求时生成备份；日常维护不得自动创建源码归档、数据库快照、迁移ZIP或旧校验清单备份，不自动调用备份/打包工具。已有备份保留，正常业务日报与研究持久化继续。如用户要求SQLite备份，使用backup API。不要把node_modules、.git、.wrangler、.sites-runtime、浏览器资料或令牌放进迁移包。

用户已明确同意将空仓库Dopagip00/deng.github.io改名为gup，并公开发布研究数据，目标https://dopagip00.github.io/gup/。GitHub Pages为公开静态网站，不再承诺仅本人登录。只上传清理后的研究源码、行情与建议数据，不上传本机SQLite、个人配置、账户或凭据。Actions可能延迟，不能保证09:00整点发布；以实际日志与日报时间验证08/09运行。

ashare-cloud保留为历史Sites/Cloudflare原型，.openai/hosting.json中的原绑定保留，尚未正式部署；当前不继续该发布路线，也不create_site。GitHub源码上传、Pages部署和计划实际运行是独立验收事项，未验证前不宣称完成。
