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
