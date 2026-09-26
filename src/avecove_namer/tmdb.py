from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request


class TMDBError(RuntimeError):
    pass


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
        return self._get(f"/{kind}/{tmdb_id}", {"language": language})

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
            "tmdb_id": tmdb_id,
            "original_language": original_language,
            "original_name": str(details.get("original_name") or ""),
            "year": int(str(details.get("first_air_date"))[:4]) if details.get("first_air_date") else None,
            "episodes": episodes,
        }
