"""Minimal Rocketlane REST API client for projects and time entries.

Docs: https://developer.rocketlane.com
Auth: an API key from Rocketlane > Settings > API, sent in the `api-key` header.
"""
from __future__ import annotations

import time
from datetime import date

import requests

BASE_URL = "https://api.rocketlane.com/api/1.0"


ENTRY_FIELDS = "timeEntryId,date,minutes,activityName,project,task,projectPhase,billable,user,notes,status,deleted"


class RocketlaneError(Exception):
    pass


class RocketlaneClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"api-key": api_key.strip(), "accept": "application/json"}
        )

    # ---- low level -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(4):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                raise RocketlaneError(f"Couldn't reach Rocketlane ({type(e).__name__}). "
                                      "Check your internet connection and try again.") from e
            if resp.status_code == 429:  # rate limited: back off and retry
                time.sleep(2 ** attempt)
                continue
            break
        if resp.status_code == 401:
            raise RocketlaneError("Rocketlane rejected the API key (401). Check it in Settings > API.")
        if not resp.ok:
            try:
                errs = resp.json().get("errors", [])
                msg = "; ".join(e.get("errorMessage", "") for e in errs) or resp.text
            except ValueError:
                msg = resp.text
            raise RocketlaneError(f"{resp.status_code} on {path}: {msg[:300]}")
        return resp.json()

    def _paginate(self, path: str, params: dict, progress=None) -> list[dict]:
        params = {**params, "pageSize": 100}
        rows: list[dict] = []
        while True:
            body = self._get(path, params)
            rows.extend(body.get("data", []))
            page = body.get("pagination") or {}
            if progress:
                progress(len(rows), page.get("totalRecordCount") or len(rows))
            token = page.get("nextPageToken")
            if not page.get("hasMore") or not token:
                return rows
            # A page token already encodes the original filters.
            params = {"pageToken": token, "pageSize": 100}

    # ---- endpoints -------------------------------------------------------
    def projects(self, include_archived: bool = False) -> list[dict]:
        params = {"sortBy": "projectName", "sortOrder": "ASC",
                  "includeFields": "projectId,projectName,customer,status,archived"}
        if include_archived:
            params["includeArchive.eq"] = "true"
        return [_normalize_project(p) for p in self._paginate("/projects", params)]

    def time_entries(self, project_id: int, start: date, end: date) -> list[dict]:
        params = {
            "project.eq": project_id,
            "date.ge": start.isoformat(),
            "date.le": end.isoformat(),
            "sortBy": "date",
            "sortOrder": "ASC",
            "match": "all",
        }
        try:
            raw = self._paginate("/time-entries", params)
        except RocketlaneError:
            # Older accounts / API versions expose the same filters on /search.
            raw = self._paginate("/time-entries/search", params)
        return [_normalize_entry(e) for e in raw if not e.get("deleted")]

    def all_time_entries(self, since: date, until: date | None = None, progress=None) -> list[dict]:
        """Every time entry across all projects in a date window (used by Sync)."""
        params = {"date.ge": since.isoformat(), "sortBy": "date", "sortOrder": "ASC",
                  "includeFields": ENTRY_FIELDS}
        if until:
            params["date.le"] = until.isoformat()
        raw = self._paginate("/time-entries", params, progress)
        return [_normalize_entry(e) for e in raw if not e.get("deleted")]


# ---- helpers -------------------------------------------------------------
def _normalize_project(p: dict) -> dict:
    return {
        "id": p.get("projectId") or p.get("id"),
        "name": p.get("projectName") or p.get("name") or f"Project {p.get('projectId')}",
        "customer": (p.get("customer") or {}).get("companyName", ""),
        "status": (p.get("status") or {}).get("label", ""),
        "archived": bool(p.get("archived", False)),
    }


def _person_name(u: dict | None) -> str:
    if not u:
        return "Unknown"
    full = " ".join(x[:1].upper() + x[1:] for x in [u.get("firstName"), u.get("lastName")] if x).strip()
    return full or u.get("fullName") or u.get("emailId") or u.get("email") or f"User {u.get('userId', '')}"


def _normalize_entry(e: dict) -> dict:
    task = e.get("task") or {}
    phase = e.get("projectPhase") or {}
    project = e.get("project") or {}
    return {
        "id": e.get("timeEntryId"),
        "date": e.get("date"),
        "project_id": project.get("projectId"),
        "project": project.get("projectName", ""),
        "person": _person_name(e.get("user")),
        "email": (e.get("user") or {}).get("emailId", ""),
        "task": task.get("taskName") or e.get("activityName") or "(No task)",
        "phase": phase.get("phaseName", ""),
        "hours": round((e.get("minutes") or 0) / 60, 2),
        "billable": bool(e.get("billable", True)),
        "status": e.get("approvalStatus") or e.get("status") or "",
        "user_id": (e.get("user") or {}).get("userId"),
        "notes": e.get("notes") or "",
    }
