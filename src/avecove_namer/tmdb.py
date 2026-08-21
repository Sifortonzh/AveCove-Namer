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
                }
            )
        return output

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
