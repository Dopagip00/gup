"""Manual research only. Old broker stages are permanently unsupported."""
from __future__ import annotations
import argparse
import json
import core
import daily_plan

STAGES={'08:00':'收集当天资讯与最近完整交易日数据','09:00':'发布今日买入与前一交易日推荐的卖出参考'}

def prepare(stage,moment=None):
    moment=moment or core.now()
    if stage not in STAGES: raise ValueError('旧执行计划已取消；仅支持08:00资讯与09:00手动日报')
    date=moment.date().isoformat()
    if not core.md.is_trading_day(date): return {'status':'skipped','date':date,'stage':stage,'reason':'非上交所交易日'}
    if moment.strftime('%H:%M') < stage: return {'status':'skipped','date':date,'stage':stage,'reason':'尚未到计划时点'}
    return {'date':date,'stage':stage,'title':STAGES[stage],'status':'manual_research','mode':'manual_research','external_orders':[],
            'message':'云端负责自动更新；本机入口仅查看或手动生成研究，不能操作券商。'}

def record_result(stage,result):
    if stage not in STAGES: raise ValueError('旧计划已取消')
    if result.get('external_orders'): raise ValueError('手动模式禁止记录为已执行委托')
    raise ValueError('执行记录接口已停用，请读取每日研究计划')

def main():
    parser=argparse.ArgumentParser(description='手动研究工具，不连接任何券商')
    parser.add_argument('stage',choices=list(STAGES))
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args();core.init_db()
    result=daily_plan.publish() if args.publish and args.stage=='09:00' else prepare(args.stage)
    print(core.encode(result));return 0
if __name__=='__main__':raise SystemExit(main())
