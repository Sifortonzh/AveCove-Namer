from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def ranked_images(images: list[dict[str, object]], limit: int = 5) -> list[dict[str, object]]:
    """Rank image votes, not programme votes; keep unrated images honest."""
    unique: dict[str, dict[str, object]] = {}
    for raw in images:
        path = str(raw.get("file_path") or "")
        if not re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", path, re.IGNORECASE):
            continue
        item = dict(raw)
        votes = int(item.get("vote_count") or 0)
        item["vote_count"] = votes
        item["vote_average"] = float(item.get("vote_average") or 0) if votes else None
        item["preview_url"] = "https://image.tmdb.org/t/p/w500" + path
        item["original_url"] = "https://image.tmdb.org/t/p/original" + path
        previous = unique.get(path)
        if previous is None or votes > int(previous.get("vote_count") or 0):
            unique[path] = item
    return sorted(unique.values(), key=lambda item: (
        -int(bool(item["vote_count"])), -float(item["vote_average"] or 0),
        -int(item["vote_count"]), -int(item.get("width") or 0), str(item["file_path"]),
    ))[:limit]


class TMDBError(RuntimeError):
    pass


def series_summary(details: dict[str, object]) -> dict[str, object]:
    """One metadata contract for query details, artwork and persistent shelves."""
    def count(value):
        try:
            number = int(value) if value is not None else None
            return number if number is not None and number >= 0 else None
        except (ValueError, TypeError):
            return None
    seasons = {}
    for row in details.get('seasons') or []:
        if not isinstance(row, dict):
            continue
        number = count(row.get('season_number'))
        if number is None:
            continue
        seasons[number] = {'number': number, 'name': str(row.get('name') or ''),
                           'episode_count': count(row.get('episode_count'))}
    ordered = [seasons[number] for number in sorted(seasons)]
    regular = [row for row in ordered if row['number'] > 0]
    reported = count(details.get('number_of_seasons'))
    season_count = reported if reported is not None else len(regular) if ordered else None
    episode_count = count(details.get('number_of_episodes'))
    if episode_count is None and regular and all(row['episode_count'] is not None for row in regular):
        episode_count = sum(row['episode_count'] for row in regular)
    ratings, seen = [], set()
    content_ratings = details.get('content_ratings') or {}
    for row in content_ratings.get('results') or []:
        if not isinstance(row, dict):
            continue
        country, rating = str(row.get('iso_3166_1') or '').strip(), str(row.get('rating') or '').strip()
        if country and rating and (country, rating) not in seen:
            ratings.append({'country': country, 'rating': rating})
            seen.add((country, rating))
    # A rating always keeps its region; never infer a universal age restriction.
    origins = [str(value) for value in details.get('origin_country') or []]
    preferred = list(dict.fromkeys([*origins, 'US', 'CN', 'GB']))
    ratings.sort(key=lambda row: (preferred.index(row['country']) if row['country'] in preferred else len(preferred), row['country'], row['rating']))
    return {'seasons': ordered, 'season_count': season_count, 'episode_count': episode_count,
            'has_specials': 0 in seasons,
            'genres': list(dict.fromkeys(str(row['name']) for row in details.get('genres') or [] if isinstance(row, dict) and row.get('name'))),
            'series_type': str(details.get('type') or ''), 'content_ratings': ratings}


class TMDBClient:
    def __init__(self, credential: str, timeout: float = 15.0):
        self.credential = credential.strip()
        self.timeout = timeout
        if not self.credential:
            raise TMDBError("TMDB credential is empty")
        self.is_api_key = bool(re.fullmatch(r"[0-9a-fA-F]{32}", self.credential))

    def _get(self, endpoint: str, params: dict[str, object]) -> dict[str, object]:
        params = dict(params)
        headers = {
            "Accept": "application/json",
            "User-Agent": "AveCove-Namer/0.1",
        }
        if self.is_api_key:
            params["api_key"] = self.credential
        else:
            headers["Authorization"] = f"Bearer {self.credential}"
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"https://api.themoviedb.org/3{endpoint}?{query}",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TMDBError("TMDb 未找到该条目，请检查剧集、季号和集号") from exc
            raise TMDBError(f"TMDb 请求失败（HTTP {exc.code}），请稍后重试") from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise TMDBError(f"TMDB request failed: {exc}") from exc

    def search(self, query: str, kind: str = "tv", year: int | None = None, language: str = "en-US") -> list[dict[str, object]]:
        if kind not in {"tv", "movie"}:
            raise TMDBError("TMDB kind must be tv or movie")
        params: dict[str, object] = {"query": query, "language": language, "include_adult": "false"}
        if year:
            params["first_air_date_year" if kind == "tv" else "year"] = year
        payload = self._get(f"/search/{kind}", params)
        output: list[dict[str, object]] = []
        for item in payload.get("results", [])[:10]:
            title = item.get("name") if kind == "tv" else item.get("title")
            original = item.get("original_name") if kind == "tv" else item.get("original_title")
            date = item.get("first_air_date") if kind == "tv" else item.get("release_date")
            output.append(
                {
                    "id": item.get("id"),
                    "title": title,
                    "original_title": original,
                    "year": int(date[:4]) if isinstance(date, str) and len(date) >= 4 else None,
                    "language": item.get("original_language"),
                    "popularity": item.get("popularity"),
                    "overview": item.get("overview"),
                    "poster_path": item.get("poster_path"),
                    "rating": item.get("vote_average"),
                }
            )
        return output

    def trending(self, kind: str = "tv", language: str = "zh-CN") -> list[dict[str, object]]:
        if kind not in {"tv", "movie"}:
            raise TMDBError("TMDB kind must be tv or movie")
        payload = self._get(f"/trending/{kind}/week", {"language": language})
        output: list[dict[str, object]] = []
        for item in payload.get("results", [])[:20]:
            title = item.get("name") if kind == "tv" else item.get("title")
            original = item.get("original_name") if kind == "tv" else item.get("original_title")
            date = item.get("first_air_date") if kind == "tv" else item.get("release_date")
            output.append(
                {
                    "id": item.get("id"),
                    "title": title,
                    "original_title": original,
                    "year": int(date[:4]) if isinstance(date, str) and len(date) >= 4 else None,
                    "language": item.get("original_language"),
                    "overview": item.get("overview"),
                    "genre_ids": item.get("genre_ids") or [],
                    "poster_path": item.get("poster_path"),
                    "popularity": item.get("popularity"),
                    "rating": item.get("vote_average"),
                    "kind": kind,
                }
            )
        return output

    def discover(
        self,
        kind: str,
        *,
        original_language: str | None = None,
        page: int = 1,
        language: str = "zh-CN",
    ) -> list[dict[str, object]]:
        """Return a high-quality discovery pool with the same shape as trending()."""
        if kind not in {"tv", "movie"}:
            raise TMDBError("TMDB kind must be tv or movie")
        params: dict[str, object] = {
            "language": language,
            "page": max(1, page),
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "vote_count.gte": 30,
        }
        if original_language:
            params["with_original_language"] = original_language
        payload = self._get(f"/discover/{kind}", params)
        output: list[dict[str, object]] = []
        for item in payload.get("results", [])[:20]:
            title = item.get("name") if kind == "tv" else item.get("title")
            original = item.get("original_name") if kind == "tv" else item.get("original_title")
            date = item.get("first_air_date") if kind == "tv" else item.get("release_date")
            output.append(
                {
                    "id": item.get("id"),
                    "title": title,
                    "original_title": original,
                    "year": int(date[:4]) if isinstance(date, str) and len(date) >= 4 else None,
                    "language": item.get("original_language"),
                    "overview": item.get("overview"),
                    "genre_ids": item.get("genre_ids") or [],
                    "poster_path": item.get("poster_path"),
                    "popularity": item.get("popularity"),
                    "rating": item.get("vote_average"),
                    "kind": kind,
                }
            )
        return output

    def trailer(self, tmdb_id: int, kind: str) -> dict[str, object] | None:
        """Pick the best official YouTube trailer, preferring Chinese metadata."""
        if kind not in {"tv", "movie"}:
            raise TMDBError("TMDB kind must be tv or movie")
        candidates: list[dict[str, object]] = []
        seen: set[str] = set()
        for language in ("zh-CN", "en-US"):
            payload = self._get(f"/{kind}/{tmdb_id}/videos", {"language": language})
            for item in payload.get("results", []):
                key = str(item.get("key") or "")
                if not key or key in seen or str(item.get("site") or "").casefold() != "youtube":
                    continue
                seen.add(key)
                candidates.append(item)
        if not candidates:
            return None

        type_rank = {"trailer": 0, "teaser": 1, "clip": 2}
        candidates.sort(
            key=lambda item: (
                type_rank.get(str(item.get("type") or "").casefold(), 3),
                not bool(item.get("official")),
                -int(item.get("size") or 0),
            )
        )
        selected = candidates[0]
        key = str(selected["key"])
        return {
            "name": str(selected.get("name") or "预告片"),
            "type": str(selected.get("type") or "Trailer"),
            "official": bool(selected.get("official")),
            "site": "YouTube",
            "key": key,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{key}?autoplay=1&rel=0",
            "watch_url": f"https://www.youtube.com/watch?v={key}",
        }

    def details(self, tmdb_id: int, kind: str, language: str) -> dict[str, object]:
        if kind not in {"tv", "movie"}:
            raise TMDBError("TMDB kind must be tv or movie")
        params = {"language": language}
        if kind == 'tv':
            params['append_to_response'] = 'content_ratings'
        return self._get(f"/{kind}/{tmdb_id}", params)

    def collection(self, collection_id: int) -> dict[str, object]:
        if collection_id < 1:
            raise TMDBError("合集编号必须是正整数")
        return self._get(f"/collection/{collection_id}", {"language": "zh-CN"})

    def resolve_title(self, tmdb_id: int, kind: str, style: str = "auto") -> dict[str, object]:
        if style not in {"auto", "english", "chinese", "original", "bilingual"}:
            raise TMDBError("Unsupported TMDB title style")
        chinese_details = self.details(tmdb_id, kind, "zh-CN")
        english_details = self.details(tmdb_id, kind, "en-US")
        title_key = "name" if kind == "tv" else "title"
        original_key = "original_name" if kind == "tv" else "original_title"
        date_key = "first_air_date" if kind == "tv" else "release_date"

        original_language = str(english_details.get("original_language") or chinese_details.get("original_language") or "")
        original_title = str(english_details.get(original_key) or chinese_details.get(original_key) or "").strip()
        english_title = str(english_details.get(title_key) or original_title).strip()
        chinese_title = str(chinese_details.get(title_key) or original_title).strip()
        chinese_origin = original_language.casefold() in {"zh", "cn", "yue"}

        if style == "english":
            selected_title, primary_language = english_title, "en"
        elif style == "chinese":
            selected_title, primary_language = chinese_title, "zh"
        elif style == "original":
            selected_title = original_title
            primary_language = "zh" if chinese_origin else "en"
        else:
            primary = chinese_title if chinese_origin else english_title
            secondary = english_title if chinese_origin else chinese_title
            selected_title = primary
            primary_language = "zh" if chinese_origin else "en"
            if style == "bilingual" and secondary and secondary.casefold() != primary.casefold():
                selected_title = f"{primary} {secondary}"

        date = english_details.get(date_key) or chinese_details.get(date_key)
        year = int(str(date)[:4]) if date and len(str(date)) >= 4 else None
        return {
            "id": tmdb_id,
            "title": selected_title,
            "year": year,
            "primary_language": primary_language,
            "original_language": original_language,
            "english_title": english_title,
            "chinese_title": chinese_title,
            "original_title": original_title,
        }

    def episode_details(self, tmdb_id: int, season: int, episode: int) -> dict[str, object]:
        """Fetch one episode, keeping source text separate from its translation."""
        if tmdb_id < 1 or not 0 <= season <= 999 or not 1 <= episode <= 9999:
            raise ValueError("剧集编号须为正整数，季号为 0–999，集号为 1–9999")
        series = self.details(tmdb_id, "tv", "zh-CN")
        language = str(series.get("original_language") or "en")
        endpoint = f"/tv/{tmdb_id}/season/{season}/episode/{episode}"
        original = self._get(endpoint, {"language": language})
        translated = original if language == "zh-CN" else self._get(endpoint, {"language": "zh-CN"})
        return {
            "tmdb_id": tmdb_id, "season": season, "episode": episode,
            "code": f"S{season:02d}E{episode:02d}",
            "series_name": str(series.get("name") or series.get("original_name") or ""),
            "original_series_name": str(series.get("original_name") or ""),
            "original_language": language,
            "name": str(original.get("name") or ""),
            "translated_name": str(translated.get("name") or ""),
            "overview": str(translated.get("overview") or original.get("overview") or ""),
            "original_overview": str(original.get("overview") or ""),
            "air_date": original.get("air_date"), "runtime": original.get("runtime"),
            "rating": original.get("vote_average"), "vote_count": original.get("vote_count") or 0,
            "detail_url": f"https://www.themoviedb.org/tv/{tmdb_id}/season/{season}/episode/{episode}",
        }

    def series_artwork(self, tmdb_id: int, season: int | None = None, on_progress=None) -> dict[str, object]:
        if tmdb_id < 1 or (season is not None and not 0 <= season <= 999):
            raise ValueError("剧集编号或季号无效")
        details = self.details(tmdb_id, "tv", "zh-CN")
        language = str(details.get("original_language") or "en")
        summary = series_summary(details)
        seasons = summary['seasons']
        if season is not None and season not in {item["number"] for item in seasons}:
            raise ValueError("TMDb 没有该季，请从季列表中选择")
        result = {
            **summary,
            "tmdb_id": tmdb_id, "title": str(details.get("name") or ""),
            "original_name": str(details.get("original_name") or details.get("name") or ""),
            "original_language": language, "year": str(details.get("first_air_date") or "")[:4],
            "overview": str(details.get("overview") or ""), "seasons": seasons,
            "season": season, "episodes": [], "warnings": [],
            "detail_url": f"https://www.themoviedb.org/tv/{tmdb_id}",
        }
        if season is None:
            images = self._get(f"/tv/{tmdb_id}/images", {})
            result.update(posters=ranked_images(images.get("posters") or []),
                          backdrops=ranked_images(images.get("backdrops") or []), stills=[])
            return result
        source = self._get(f"/tv/{tmdb_id}/season/{season}", {"language": language})
        result["season_name"] = str(source.get("name") or "")
        episodes = sorted((item for item in source.get("episodes", []) if isinstance(item, dict) and item.get("episode_number")), key=lambda item: int(item["episode_number"]))
        result["episodes"] = [{"season": season, "episode": int(item["episode_number"]),
                               "name": str(item.get("name") or ""), "overview": str(item.get("overview") or ""),
                               "air_date": item.get("air_date")} for item in episodes]
        images = self._get(f"/tv/{tmdb_id}/season/{season}/images", {})
        stills = []
        if on_progress:
            on_progress(0, len(episodes))
        def fetch_episode(item):
            number = int(item["episode_number"])
            images = self._get(f"/tv/{tmdb_id}/season/{season}/episode/{number}/images", {})
            return [{**image, "season": season, "episode": number} for image in images.get("stills", [])]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_episode, item): item for item in episodes}
            for completed, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                try:
                    stills.extend(future.result())
                except TMDBError:
                    result["warnings"].append(f"E{int(item['episode_number']):02d} 剧照读取失败；本次排序仅包含成功读取的图片，可重试")
                if on_progress:
                    on_progress(completed, len(episodes))
        result.update(posters=ranked_images(images.get("posters") or []), backdrops=[], stills=ranked_images(stills))
        return result

    def original_episode_metadata(self, tmdb_id: int) -> dict[str, object]:
        """Return a TV series and every episode in its original language."""
        details = self.details(tmdb_id, "tv", "en-US")
        original_language = str(details.get("original_language") or "").strip()
        if not original_language:
            raise TMDBError("TV series has no original_language")

        episodes: list[dict[str, object]] = []
        seasons = sorted(
            (item for item in details.get("seasons", []) if isinstance(item, dict)),
            key=lambda item: int(item.get("season_number") or 0),
        )
        for season in seasons:
            season_number = int(season.get("season_number") or 0)
            payload = self._get(
                f"/tv/{tmdb_id}/season/{season_number}",
                {"language": original_language},
            )
            season_episodes = sorted(
                (item for item in payload.get("episodes", []) if isinstance(item, dict)),
                key=lambda item: int(item.get("episode_number") or 0),
            )
            for episode in season_episodes:
                episodes.append(
                    {
                        "season": season_number,
                        "episode": int(episode.get("episode_number") or 0),
                        "name": str(episode.get("name") or ""),
                        "overview": str(episode.get("overview") or ""),
                    }
                )

        return {
            **series_summary(details),
            "tmdb_id": tmdb_id,
            "original_language": original_language,
            "original_name": str(details.get("original_name") or ""),
            "year": int(str(details.get("first_air_date"))[:4]) if details.get("first_air_date") else None,
            "episodes": episodes,
        }
