"""Run: python app.py [serve|update|status] --port 8765"""
from __future__ import annotations
import argparse
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

import core


def make_handler(service, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'HuYan/1.0'

        def log_message(self, format, *args):
            logging.debug(format, *args)

        def send(self, data, status=200, content_type='application/json; charset=utf-8', attachment=None):
            body = core.encode(data).encode('utf-8') if content_type.startswith('application/json') else data if isinstance(data, bytes) else str(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if attachment:
                self.send_header('Content-Disposition', 'attachment; filename="'+attachment+'"')
            self.end_headers()
            self.wfile.write(body)

        def host_valid(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self.host_valid():
                return self.send({'error': '仅接受本机访问'}, 403)
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == '/api/health':
                    return self.send({'ok': True, 'mode': 'manual_research', 'project_path': str(core.BASE)})
                if url.path == '/api/migration-guide':
                    return self.send((core.BASE/'MIGRATION.md').read_text(encoding='utf-8'),content_type='text/plain; charset=utf-8')
                if url.path == '/api/daily-plan':
                    import daily_plan
                    return self.send(daily_plan.read(query.get('date', [None])[0]))
                if url.path == '/api/daily-watchlist':
                    import daily_watchlist
                    return self.send(daily_watchlist.read(query.get('date', [None])[0], service=service))
                if url.path == '/api/market-brief':
                    import market_brief
                    return self.send(market_brief.read(query.get('date',[None])[0]))
                if url.path == '/api/system':
                    return self.send({'version': json.loads((core.BASE/'version.json').read_text(encoding='utf-8')), 'mode':'manual_research', 'hosted':False,
                        'migration':{'backup_command':'backup.cmd','update_command':'apply-update.cmd','guide':'MIGRATION.md'},
                        'cloud_url':'https://dopagip00.github.io/gup/', 'cloud_deployment_verified':False})
                if url.path == '/api/dashboard':
                    return self.send(service.dashboard(query.get('date', [None])[0]))
                if url.path == '/api/stock':
                    return self.send(service.stock(query.get('symbol', [''])[0]))
                if url.path == '/api/quotes':
                    return self.send(core.getkv('quotes', {'quotes': [], 'status': '暂无报价；交易时段后台自动获取'}))
                if url.path == '/api/config':
                    return self.send(core.config() | {'csrf_token': service.csrf})
                if url.path == '/api/paper':
                    return self.send(service.portfolio())
                if url.path == '/api/reports':
                    return self.send(service.reports())
                if url.path == '/api/forecast-audit':
                    import forecast_audit
                    return self.send(forecast_audit.evaluate(core))
                if url.path == '/api/longterm':
                    return self.send(service.longterm())
                if url.path == '/api/agent':
                    return self.send(service.agent_state())
                if url.path == '/api/export.csv':
                    return self.send(service.export_csv(query.get('date', [None])[0]), content_type='text/csv; charset=utf-8', attachment='ashare-daily.csv')
                allowed = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css', '/favicon.svg': 'favicon.svg', '/favicon.ico': 'favicon.ico', '/favicon-32.png': 'favicon-32.png', '/apple-touch-icon.png': 'apple-touch-icon.png'}
                if url.path in allowed:
                    path = core.BASE / 'static' / allowed[url.path]
                    if path.is_file():
                        return self.send(path.read_bytes(), content_type=(mimetypes.guess_type(path.name)[0] or 'text/plain')+'; charset=utf-8')
                self.send({'error': '未找到'}, 404)
            except (ValueError, TypeError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception:
                logging.exception('GET failed')
                self.send({'error': '读取失败，请查看本机 data/app.log'}, 500)

        def do_POST(self):
            if not self.host_valid() or self.headers.get('X-CSRF-Token') != service.csrf:
                return self.send({'error': '请求校验失败，请刷新页面'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
                return self.send({'error': '拒绝跨站请求'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 65536:
                    return self.send({'error': '请求过大'}, 413)
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('请求体必须为JSON对象')
                path = urlsplit(self.path).path
                if path == '/api/shutdown':
                    if service.lock.locked():
                        return self.send({'error':'正在同步数据，请完成后再停止'},409)
                    self.send({'status':'stopping'})
                    service.stop.set()
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                if path == '/api/refresh':
                    return self.send(service.start_refresh())
                if path == '/api/config':
                    result = core.set_config(data)
                    return self.send(result)
                if path == '/api/paper/orders':
                    return self.send({'error':'手动研究模式已停用交易入口'}, 410)
                if path == '/api/paper/cancel':
                    return self.send(service.cancel_order(data.get('id')))
                return self.send({'error': '未找到'}, 404)
            except (ValueError, TypeError, OverflowError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception:
                logging.exception('POST failed')
                self.send({'error': '操作失败，请查看本机 data/app.log'}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='沪研 A股量化观察台（本机研究与模拟交易）')
    parser.add_argument('command', choices=['serve', 'update', 'status'], nargs='?', default='serve')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--no-auto-refresh', action='store_true')
    args = parser.parse_args()
    core.DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[RotatingFileHandler(core.DATA/'app.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')])
    service = core.Service()
    if args.command == 'update':
        try:
            if not args.force and not core.md.is_trading_day(core.now().date()):
                print(core.encode({'status': 'idle', 'reason': '非交易日，跳过定时更新'}))
                return 0
            print(core.encode(service.refresh(args.force)))
        except Exception as exc:
            logging.exception('scheduled update failed')
            print(core.encode({'status': 'error', 'error': str(exc)}))
            return 1
        return 0 if service.status['status'] in ('success', 'idle', 'running') else 2
    if args.command == 'status':
        print(core.encode(service.dashboard()['meta']))
        return 0
    url = f'http://127.0.0.1:{args.port}'
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(service, args.port))
    except OSError:
        if args.open:
            webbrowser.open(url)
        print('端口已在使用。如果平台已启动，请打开 ' + url)
        return 1
    # Cloud schedules own the 08:00/09:00 workflow; no legacy local timer.
    if not args.no_auto_refresh:
        try:
            completed = core.getkv('last_completed', {})
            universe = core.getkv('universe_info', {})
            if completed.get('target_date') != core.expected_date() or universe.get('mode') != core.config()['universe']:
                service.start_refresh(False)
        except Exception:
            service.start_refresh(False)
    print('Local: ' + url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop.set()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
