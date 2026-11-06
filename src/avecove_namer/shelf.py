"""Persistent quick access to saved television series; no cloud mutations."""
import json
from pathlib import Path
import threading
import time
from datetime import date, datetime, timedelta, timezone


WATCH_TIMEZONE = timezone(timedelta(hours=8))


def watch_date(value=None):
    today = datetime.now(WATCH_TIMEZONE).date()
    if value is None:
        return today
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('观看日期请使用 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('观看日期无效') from None
    if parsed.isoformat() != value or parsed > today or parsed.year < 1900:
        raise ValueError('观看日期须在 1900 年至今天之间')
    return parsed


def completion_record(payload, previous):
    day = watch_date(payload.get('watchedDate', previous.get('watchedDate')))
    stars = payload.get('personalStars', previous.get('personalStars'))
    if stars is not None and (type(stars) is not int or not 1 <= stars <= 5):
        raise ValueError('个人评分须为 1 至 5 星，或留空')
    return {'watchedDate': day.isoformat(),
            'watchedAt': int(datetime.combine(day, datetime.min.time(), WATCH_TIMEZONE).timestamp()),
            'watchedRecordedAt': int(time.time()), 'personalStars': stars}


def watch_timeline(rows):
    records = []
    for row in rows:
        if row.get('watched'):
            records.append({**row, 'completedSeason': None})
        for season, record in row.get('seasonCompletions', {}).items():
            records.append({**row, **record, 'completedSeason': int(season)})
    return sorted(records, key=lambda row: (-row.get('watchedAt', 0), -row.get('watchedRecordedAt', 0), row['id']))


class SeriesShelf:
    def __init__(self, path: Path, metadata_loader=None):
        self.path = path
        self.lock = threading.Lock()
        self.metadata_loader = metadata_loader
        self.enriching = False

    def write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def enrich(self):
        """Bounded background metadata fetch, never hold the shelf lock over HTTP."""
        try:
            with self.lock:
                pending = [row['id'] for row in self.read().values()
                           if row.get('artworkRetryAt', 0) <= time.time()
                           and (row.get('artworkVersion') != 2 or row.get('artworkUpdatedAt', 0) < time.time() - 86400)][:20]
            for ident in pending:
                try:
                    metadata = self.metadata_loader(int(ident))
                except Exception:
                    metadata = None
                with self.lock:
                    current = self.read()
                    if ident not in current:
                        continue
                    if metadata is not None:
                        current[ident].update(metadata)
                        current[ident]['artworkUpdatedAt'] = int(time.time())
                        current[ident]['artworkVersion'] = 2
                        current[ident].pop('artworkRetryAt', None)
                    else:
                        current[ident]['artworkRetryAt'] = int(time.time()) + 300
                    self.write(current)
        finally:
            with self.lock:
                self.enriching = False

    def read(self):
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def request(self, payload=None):
        with self.lock:
            rows = self.read()
            if payload is not None:
                ident = str(payload.get('id', ''))
                if not ident.isdecimal() or not 0 < int(ident) < 100000000:
                    raise ValueError('无效的 TMDb 剧集编号')
                action = payload.get('action')
                if action not in ('favorite', 'watching', 'progress', 'watched', 'delete'):
                    raise ValueError('不支持的收藏操作')
                row = dict(rows.get(ident, {'id': ident, 'favorite': False, 'watching': False}))
                if action == 'delete':
                    if payload.get('confirm') is not True:
                        raise ValueError('请确认从我的剧集中移除')
                    if ident in rows:
                        row['shelfHidden'] = True
                elif action == 'progress':
                    values = (payload.get('season'), payload.get('episode'))
                    if any(isinstance(value, bool) or not str(value).isascii() or not str(value).isdecimal() for value in values):
                        raise ValueError('季集号请填写整数')
                    season, episode = map(int, values)
                    if not 0 <= season <= 999 or not 1 <= episode <= 9999:
                        raise ValueError('季集号无效')
                    if ident in rows:
                        row.update(season=season, episode=episode)
                else:
                    if not isinstance(payload.get('enabled'), bool):
                        raise ValueError('状态必须为布尔值')
                    season = payload.get('seasonNumber') if action == 'watched' else None
                    if season is not None:
                        if type(season) is not int or not 0 <= season <= 9999:
                            raise ValueError('观看记录的季号无效')
                        key = str(season)
                        seasons = dict(row.get('seasonCompletions', {}))
                        if payload['enabled']:
                            seasons[key] = completion_record(payload, seasons.get(key, {}))
                        else:
                            seasons.pop(key, None)
                        if seasons:
                            row['seasonCompletions'] = seasons
                        else:
                            row.pop('seasonCompletions', None)
                    elif action == 'watched':
                        row['watched'] = payload['enabled']
                        if payload['enabled']:
                            row.update(completion_record(payload, row))
                            row.pop('shelfRestored', None)
                            if not rows.get(ident, {}).get('watched'):
                                row['watching'] = False
                        else:
                            for key in ('watchedDate', 'watchedAt', 'watchedRecordedAt', 'personalStars'):
                                row.pop(key, None)
                    else:
                        row[action] = payload['enabled']
                        if payload['enabled']:
                            row.pop('shelfHidden', None)
                            row['shelfRestored'] = True
                    title = str(payload.get('title', '')).strip()[:250]
                    if title:
                        row['title'] = title
                if row.get('favorite') or row.get('watching') or row.get('watched') or row.get('seasonCompletions'):
                    row['updatedAt'] = int(time.time())
                    rows[ident] = row
                else:
                    rows.pop(ident, None)
                self.write(rows)
            if self.metadata_loader and not self.enriching and any(
                    row.get('artworkRetryAt', 0) <= time.time() and (row.get('artworkVersion') != 2 or row.get('artworkUpdatedAt', 0) < time.time() - 86400)
                    for row in rows.values()):
                self.enriching = True
                threading.Thread(target=self.enrich, daemon=True).start()
            return {'items': sorted(rows.values(), key=lambda row: (-row.get('updatedAt', 0), row['id'])),
                    'timeline': watch_timeline(rows.values()),
                    'artwork_pending': self.enriching}
