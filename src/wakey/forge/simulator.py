# SPDX-License-Identifier: Apache-2.0
"""Local GitHub simulator (founder directive: no live GitHub testing).

Implements the subset of the GitHub REST API that GitHubAdapter uses —
create issue, get issue, add comment — with token auth, exactly like the
real thing from the adapter's point of view. In-memory store; assert on
the ``issues`` list directly in tests/demos.
"""

from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class GitHubSimulator:
    """In-memory GitHub stand-alone: issues + comments + token auth."""

    def __init__(self, token: str = "sim-token") -> None:
        self.token = token
        self.issues: list[dict[str, object]] = []
        self._next_number = 1

    def create_app(self) -> FastAPI:
        sim = FastAPI(title="github-simulator", docs_url=None, redoc_url=None)

        def authorize(request: Request) -> JSONResponse | None:
            header = request.headers.get("Authorization", "")
            if header != f"Bearer {self.token}":
                return JSONResponse({"message": "Bad credentials"}, status_code=401)
            return None

        def find_issue(repo_path: str, number: int) -> dict[str, object] | None:
            return next(
                (
                    issue
                    for issue in self.issues
                    if issue["number"] == number and issue["repo"] == repo_path
                ),
                None,
            )

        @sim.post("/repos/{owner}/{repo}/issues")
        async def create_issue(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            issue = {
                "number": self._next_number,
                "repo": f"{owner}/{repo}",
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "labels": data.get("labels", []),
                "comments": [],
                "state": "open",
            }
            self._next_number += 1
            issue["html_url"] = f"https://github.sim/{owner}/{repo}/issues/{issue['number']}"
            self.issues.append(issue)
            return JSONResponse(
                {"number": issue["number"], "html_url": issue["html_url"]},
                status_code=201,
            )

        @sim.get("/repos/{owner}/{repo}/issues/{number}")
        async def get_issue(owner: str, repo: str, number: int, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            issue = find_issue(f"{owner}/{repo}", number)
            if issue is None:
                return JSONResponse({"message": "Not Found"}, status_code=404)
            return JSONResponse(issue)

        @sim.post("/repos/{owner}/{repo}/issues/{number}/comments")
        async def add_comment(owner: str, repo: str, number: int, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            issue = find_issue(f"{owner}/{repo}", number)
            if issue is None:
                return JSONResponse({"message": "Not Found"}, status_code=404)
            comments = issue["comments"]
            assert isinstance(comments, list)  # narrowed: we created it as a list
            data = await request.json()
            comments.append({"body": data.get("body", "")})
            return JSONResponse({"id": len(comments)}, status_code=201)

        @sim.post("/repos/{owner}/{repo}/pulls")
        async def create_pull(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            pull = {
                "number": self._next_number,
                "repo": f"{owner}/{repo}",
                "title": data.get("title", ""),
                "draft": data.get("draft", True),
                "state": "open",
            }
            self._next_number += 1
            self.issues.append(pull)
            return JSONResponse(
                {
                    "number": pull["number"],
                    "html_url": f"https://github.sim/{owner}/{repo}/pull/{pull['number']}",
                },
                status_code=201,
            )

        return sim


def serve_in_background(app: FastAPI, port: int) -> uvicorn.Server:
    """Run the simulator on 127.0.0.1:port in a daemon thread (tests/demos)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server
