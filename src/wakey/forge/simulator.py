# SPDX-License-Identifier: Apache-2.0
"""Local GitHub simulator (founder directive: no live GitHub testing).

Implements the subset of the GitHub REST API that GitHubAdapter uses —
issues, comments, labels, draft pulls, and the git data API (refs, blobs,
trees, commits) — with token auth, exactly like the real thing from the
adapter's point of view. In-memory store; tests/demos assert directly on
``issues``/``pulls``/``repos``. The git surface is what makes branch
publication (R-06) contract-testable: a PR must reference a branch whose
tree actually contains the committed files.
"""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class GitHubSimulator:
    """In-memory GitHub stand-in: issues, pulls, branches, trees, commits."""

    def __init__(self, token: str = "sim-token") -> None:
        self.token = token
        self.issues: list[dict[str, object]] = []
        self.pulls: list[dict[str, object]] = []
        self.repos: dict[str, dict[str, object]] = {}
        self.blobs: dict[str, str] = {}  # sha -> content (tree verification, R-06)
        self._next_number = 1
        self._next_sha = 1
        self._lock = threading.Lock()

    # --- internal git model ----------------------------------------------------

    def _repo(self, full_name: str) -> dict[str, object]:
        if full_name not in self.repos:
            root_tree = self._empty_tree_sha()
            root_commit = self._sha("commit")
            self.repos[full_name] = {
                "default_branch": "main",
                "branches": {"main": root_commit},
                "commits": {root_commit: {"tree": root_tree, "parents": [], "message": "root"}},
                "trees": {root_tree: {}},
                "files": {},  # path -> content (contents API, E3-T6)
            }
        return self.repos[full_name]

    def _sha(self, kind: str) -> str:
        self._next_sha += 1
        return f"sim-{kind}-{self._next_sha:08d}"

    def _empty_tree_sha(self) -> str:
        return self._sha("tree")

    def _tree_files(self, repo: dict[str, object], tree_sha: str) -> dict[str, str]:
        trees: dict[str, dict[str, str]] = repo["trees"]  # type: ignore[assignment]
        return dict(trees.get(tree_sha, {}))

    def set_file(self, full_name: str, path: str, content: str) -> None:
        """Create/replace a file in the repo (tests/demos: config reload, R-06)."""
        with self._lock:
            data = self._repo(full_name)
            files: dict[str, str] = data["files"]  # type: ignore[assignment]
            files[path] = content

    def branch_files(self, full_name: str, branch: str) -> dict[str, str]:
        """Materialize a branch's files (blob shas decoded to content)."""
        data = self._repo(full_name)
        branches: dict[str, str] = data["branches"]  # type: ignore[assignment]
        commits: dict[str, dict[str, object]] = data["commits"]  # type: ignore[assignment]
        tip = branches[branch]
        tree = str(commits[tip]["tree"])
        files_by_blob = self._tree_files(data, tree)
        return {path: self.blobs.get(blob, blob) for path, blob in files_by_blob.items()}

    # --- HTTP surface ------------------------------------------------------------

    def _register_ref_routes(
        self, sim: FastAPI, authorize: Callable[[Request], JSONResponse | None]
    ) -> None:
        """Ref surface: branch lookup, creation, fast-forward updates."""

        @sim.post("/app-manifests/{code}/conversions")
        async def convert_manifest(code: str, request: Request) -> JSONResponse:
            """E3-T1: exchange a temporary code for app credentials (once).

            No Authorization header — like the real API, the one-time code
            itself is the credential.
            """
            if code != "valid-code":
                return JSONResponse({"message": "Not Found"}, status_code=404)
            if getattr(self, "_code_used", False):
                return JSONResponse({"message": "code already used"}, status_code=422)
            self._code_used = True
            return JSONResponse(
                {
                    "slug": "wakey-on-test",
                    "client_id": "Iv1.simclient",
                    "client_secret": "sim-client-secret",
                    "pem": "-----BEGIN RSA PRIVATE KEY-----\nsim\n-----END RSA PRIVATE KEY-----",
                    "webhook_secret": "sim-app-hook-secret",
                },
                status_code=201,
            )

        @sim.get("/repos/{owner}/{repo}/contents/{file_path:path}")
        async def get_contents(
            owner: str, repo: str, file_path: str, request: Request
        ) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            with self._lock:
                data = self._repo(f"{owner}/{repo}")
                files: dict[str, str] = data["files"]  # type: ignore[assignment]
            content = files.get(file_path)
            if content is None:
                return JSONResponse({"message": "Not Found"}, status_code=404)
            encoded = base64.b64encode(content.encode()).decode()
            return JSONResponse(
                {
                    "name": file_path.rsplit("/", 1)[-1],
                    "path": file_path,
                    "content": encoded,
                    "encoding": "base64",
                }
            )

        @sim.get("/repos/{owner}/{repo}/git/ref/heads/{branch:path}")
        async def get_ref(owner: str, repo: str, branch: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            with self._lock:
                data = self._repo(f"{owner}/{repo}")
                branches: dict[str, str] = data["branches"]  # type: ignore[assignment]
                if branch not in branches:
                    return JSONResponse({"message": "Not Found"}, status_code=404)
                return JSONResponse({"object": {"sha": branches[branch]}})

        @sim.post("/repos/{owner}/{repo}/git/refs")
        async def create_ref(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            name = str(data.get("ref", "")).removeprefix("refs/heads/")
            with self._lock:
                data_repo = self._repo(f"{owner}/{repo}")
                branches: dict[str, str] = data_repo["branches"]  # type: ignore[assignment]
                if name in branches:
                    return JSONResponse({"message": "Reference already exists"}, status_code=422)
                branches[name] = str(data.get("sha", ""))
            return JSONResponse({"ref": f"refs/heads/{name}"}, status_code=201)

        @sim.patch("/repos/{owner}/{repo}/git/refs/heads/{branch:path}")
        async def patch_ref(owner: str, repo: str, branch: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            if data.get("force"):
                return JSONResponse({"message": "force-push rejected"}, status_code=422)
            with self._lock:
                data_repo = self._repo(f"{owner}/{repo}")
                branches: dict[str, str] = data_repo["branches"]  # type: ignore[assignment]
                if branch not in branches:
                    return JSONResponse({"message": "Not Found"}, status_code=404)
                branches[branch] = str(data.get("sha", ""))
            return JSONResponse({"ref": f"refs/heads/{branch}"})

    def _register_tree_routes(
        self, sim: FastAPI, authorize: Callable[[Request], JSONResponse | None]
    ) -> None:
        """Blob/tree/commit surface: content publication (R-06)."""

        @sim.post("/repos/{owner}/{repo}/git/blobs")
        async def create_blob(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            content = str(data.get("content", ""))
            sha = f"sim-blob-{abs(hash(content)) & 0xFFFFFFFF:08d}"
            with self._lock:
                self.blobs[sha] = content
            return JSONResponse({"sha": sha}, status_code=201)

        @sim.post("/repos/{owner}/{repo}/git/trees")
        async def create_tree(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            with self._lock:
                data_repo = self._repo(f"{owner}/{repo}")
                files = self._tree_files(data_repo, str(data.get("base_tree", "")))
                for item in data.get("tree", []):
                    sha = str(item.get("sha", ""))
                    # blob shas carry their content: sim-blob-<hash of content>
                    files[str(item["path"])] = sha
                tree_sha = self._sha("tree")
                trees: dict[str, dict[str, str]] = data_repo["trees"]  # type: ignore[assignment]
                trees[tree_sha] = files
            return JSONResponse({"sha": tree_sha}, status_code=201)

        @sim.get("/repos/{owner}/{repo}/git/commits/{sha}")
        async def get_commit(owner: str, repo: str, sha: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            with self._lock:
                data = self._repo(f"{owner}/{repo}")
                commits: dict[str, dict[str, object]] = data["commits"]  # type: ignore[assignment]
                if sha not in commits:
                    return JSONResponse({"message": "Not Found"}, status_code=404)
                return JSONResponse({"tree": {"sha": commits[sha]["tree"]}})

        @sim.post("/repos/{owner}/{repo}/git/commits")
        async def create_commit(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            sha = self._sha("commit")
            with self._lock:
                data_repo = self._repo(f"{owner}/{repo}")
                commits: dict[str, dict[str, object]] = data_repo["commits"]  # type: ignore[assignment]
                commits[sha] = {
                    "tree": str(data.get("tree", "")),
                    "parents": list(data.get("parents", [])),
                    "message": str(data.get("message", "")),
                }
            return JSONResponse({"sha": sha}, status_code=201)

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
                    if issue["number"] == number
                    and issue["repo"] == repo_path
                    and issue.get("kind") == "issue"
                ),
                None,
            )

        @sim.get("/repos/{owner}/{repo}")
        async def get_repo(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            with self._lock:
                data = self._repo(f"{owner}/{repo}")
                return JSONResponse(
                    {"full_name": f"{owner}/{repo}", "default_branch": data["default_branch"]}
                )

        self._register_issue_routes(sim, authorize, find_issue)
        self._register_ref_routes(sim, authorize)
        self._register_tree_routes(sim, authorize)
        return sim

    def _register_issue_routes(
        self,
        sim: FastAPI,
        authorize: Callable[[Request], JSONResponse | None],
        find_issue: Callable[[str, int], dict[str, object] | None],
    ) -> None:
        """Issue/comment/label/pull surface (M0 tickets + R-09 effects)."""

        @sim.post("/repos/{owner}/{repo}/issues")
        async def create_issue(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            issue = {
                "kind": "issue",
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

        @sim.patch("/repos/{owner}/{repo}/issues/{number}")
        async def patch_issue(owner: str, repo: str, number: int, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            issue = find_issue(f"{owner}/{repo}", number)
            if issue is None:
                return JSONResponse({"message": "Not Found"}, status_code=404)
            data = await request.json()
            if "state" in data:
                issue["state"] = data["state"]
            return JSONResponse({"number": number, "state": issue["state"]})

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

        @sim.post("/repos/{owner}/{repo}/issues/{number}/labels")
        async def add_labels(owner: str, repo: str, number: int, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            issue = find_issue(f"{owner}/{repo}", number)
            if issue is None:
                return JSONResponse({"message": "Not Found"}, status_code=404)
            labels = issue["labels"]
            assert isinstance(labels, list)
            data = await request.json()
            labels.extend(data.get("labels", []))
            return JSONResponse({"labels": labels}, status_code=200)

        @sim.post("/repos/{owner}/{repo}/pulls")
        async def create_pull(owner: str, repo: str, request: Request) -> JSONResponse:
            denied = authorize(request)
            if denied is not None:
                return denied
            data = await request.json()
            pull = {
                "kind": "pull",
                "number": self._next_number,
                "repo": f"{owner}/{repo}",
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "head": data.get("head", ""),
                "base": data.get("base", ""),
                "draft": data.get("draft", True),
                "state": "open",
            }
            self._next_number += 1
            pull["html_url"] = f"https://github.sim/{owner}/{repo}/pull/{pull['number']}"
            self.pulls.append(pull)
            return JSONResponse(
                {
                    "number": pull["number"],
                    "html_url": pull["html_url"],
                },
                status_code=201,
            )


def serve_in_background(app: FastAPI, port: int) -> uvicorn.Server:
    """Run the simulator on 127.0.0.1:port in a daemon thread (tests/demos)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server
