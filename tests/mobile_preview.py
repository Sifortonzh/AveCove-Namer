"""Loopback-only browser fixture: production UI, synthetic replies, no cloud calls.

Run: python3 tests/mobile_preview.py, then open http://127.0.0.1:19877/#tmdb.
Search for any nonnumeric name to reproduce a long English result safely.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json

WEB = Path(__file__).resolve().parents[1] / 'src/avecove_namer/web'


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def reply(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if urlsplit(self.path).path.startswith('/api/'):
            return self.reply({'status':'ok', 'items':[], 'artwork_pending':False})
        super().do_GET()

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        path = urlsplit(self.path).path
        if path == '/api/tmdb/search':
            return self.reply({'resolved_kind':'tv','results':[{'id':2691,'kind':'tv','title':'好汉两个半','original_title':'Two and a Half Men','year':'2003','language':'en','recommended_name':'Two and a Half Men (2003) {tmdb=2691}'}]})
        if path == '/api/library/search':
            return self.reply({'found':False,'matches':[],'message':'隔离布局测试，不访问网盘'})
        if path == '/api/tmdb/summary':
            return self.reply({'season_count':12,'episode_count':262,'genres':['喜剧'],'series_type':'Scripted','seasons':[],'content_ratings':[{'country':'US','rating':'TV-14'}]})
        if path == '/api/tmdb/episode':
            return self.reply({'code':'S01E02','name':'Big Flappy Bastards','translated_name':'单集示例','overview':'隔离测试说明','original_language':'en','detail_url':'https://www.themoviedb.org/tv/2691/season/1/episode/2'})
        return self.reply({'error':'Preview is read-only; state/cloud mutations are disabled.'}, 405)


if __name__ == '__main__':
    print('Isolated mobile preview: http://127.0.0.1:19877/#tmdb', flush=True)
    ThreadingHTTPServer(('127.0.0.1',19877),Preview).serve_forever()
