"""Owner-invoked initial seed after private publication; no stored credentials.

Uploads public market research only, not local cash, orders or account records.
Routine unattended updates run in the cloud, independently of this script.
"""
import argparse
import getpass
import json
import urllib.parse
import urllib.request
import core

def main():
    p=argparse.ArgumentParser(description='已发布私有站点的初始研究快照导入')
    p.add_argument('--url',required=True);p.add_argument('--histories',action='store_true')
    args=p.parse_args();url=urllib.parse.urlsplit(args.url)
    if url.scheme!='https' or not url.hostname or not url.hostname.endswith('.chatgpt.site') or url.username or url.password or url.query or url.fragment:
        raise SystemExit('只接受当前项目正式HTTPS站点原点，不接受用户名、参数或其他来源')
    origin='https://'+url.netloc
    token=getpass.getpass('从当前站点Sites get_site取得的服务令牌（隐藏输入，不保存）：')
    def call(records):
        body=core.encode({'action':'seed','records':records}).encode('utf-8')
        req=urllib.request.Request(origin+'/api/update',data=body,method='POST',headers={'Content-Type':'application/json','OAI-Sites-Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=180) as response:
            result=json.load(response)
        if result.get('saved')!=len(records):raise ValueError('云端未确认完整保存，停止导入')
    core.init_db();target=core.expected_date()
    quotes=core.getkv('valuation_quotes',{}).get('quotes',{})
    with core.connect() as con:
        universe=[dict(x) for x in con.execute('SELECT symbol,name FROM securities WHERE active=1 ORDER BY symbol')]
        signals=[]
        for r in con.execute('SELECT symbol,payload FROM signals WHERE report_date=?',(target,)):
            value=json.loads(r['payload'])
            if r['symbol']!='sh000001':value['quote_snapshot']=quotes.get(r['symbol'])
            signals.append({'key':'sig:'+target+':'+r['symbol'],'value':value})
    first=[{'key':'universe','value':universe},{'key':'latest_date','value':target},{'key':'longterm','value':core.getkv('longterm',{})},
           {'key':'hist:sh000001','value':core.load_history('sh000001')}]
    call(first)
    for offset in range(0,len(signals),40):call(signals[offset:offset+40]);print('研究导入',min(offset+40,len(signals)),'/',len(signals),flush=True)
    if args.histories:
        for offset in range(0,len(universe),8):
            call([{'key':'hist:'+s['symbol'],'value':core.load_history(s['symbol'])} for s in universe[offset:offset+8]])
            print('日线导入',min(offset+8,len(universe)),'/',len(universe),flush=True)
    print('导入完成；仍需认证读取/api/dashboard核对覆盖，并实际演练云端start/batch更新。此脚本不注册任务。')
if __name__=='__main__':main()
