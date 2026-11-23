"""Her changes as GitHub pull requests (Nov 23).

When you install a skill FORGE built, she also opens a pull request with it on your repository, so it is reviewed,
versioned and merged like any other code. The commit is written by E.V.A. with you as co-author
("Co-authored-by: <you>"), which GitHub counts on your contribution graph once it is merged into the default
branch, as long as the co-author email belongs to your account (she uses your GitHub no-reply address).

Everything goes through GitHub's API (git data + pulls): your working folder, branch and running server are never
touched, and nothing is merged without you.

Setup (once):
    config/secrets.env    GITHUB_TOKEN=<fine-grained token: this repo only, Contents + Pull requests: read and write>
    config/settings.yaml  github.pull_requests: true   (repo and base branch below)
"""

from __future__ import annotations

import base64
import re
import threading
import time
from typing import Callable, Optional

import httpx
from loguru import logger

API = "https://api.github.com"
DEFAULTS = {"pull_requests": False, "repo": "", "base": "main", "co_author": "", "author_name": "E.V.A.",
            "author_email": "eva@localhost"}


def cfg() -> dict:
    from core.settings import get_settings
    return {**DEFAULTS, **(get_settings().get("github") or {})}


def token() -> Optional[str]:
    try:
        from core.security.vault import vault
        t = vault.get("GITHUB_TOKEN")
    except Exception:
        t = None
    if not t:
        import os
        t = os.environ.get("GITHUB_TOKEN")
    return t or None


def ready() -> tuple[bool, str]:
    c = cfg()
    if not c.get("pull_requests"):
        return False, "github.pull_requests is off in settings"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(c.get("repo") or "")):
        return False, "github.repo isn't set (owner/name)"
    if not token():
        return False, "there's no GITHUB_TOKEN in config/secrets.env"
    return True, ""


class GitHub:
    def __init__(self, repo: str, tok: str, http: Optional[httpx.Client] = None):
        self.repo = repo
        self.http = http or httpx.Client(timeout=30, headers={
            "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, f"{API}{path}", **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]}: {r.status_code} {r.text[:160]}")
        return r.json() if r.content else {}

    def co_author(self, configured: str = "") -> str:
        """'Name <email>': the configured one, or your GitHub no-reply address (counts on your graph)."""
        if configured:
            return configured
        me = self._req("GET", "/user")
        name = me.get("name") or me["login"]
        return f"{name} <{me['id']}+{me['login']}@users.noreply.github.com>"

    def open_pr(self, *, base: str, branch: str, files: dict[str, str], title: str, message: str, body: str,
                author: dict) -> dict:
        r = f"/repos/{self.repo}"
        head = self._req("GET", f"{r}/git/ref/heads/{base}")["object"]["sha"]
        base_tree = self._req("GET", f"{r}/git/commits/{head}")["tree"]["sha"]
        tree = []
        for path, text in sorted(files.items()):
            blob = self._req("POST", f"{r}/git/blobs", json={
                "content": base64.b64encode(text.encode("utf-8")).decode(), "encoding": "base64"})
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        new_tree = self._req("POST", f"{r}/git/trees", json={"base_tree": base_tree, "tree": tree})["sha"]
        commit = self._req("POST", f"{r}/git/commits", json={"message": message, "tree": new_tree,
                                                             "parents": [head], "author": author})["sha"]
        self._req("POST", f"{r}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        return self._req("POST", f"{r}/pulls", json={"title": title, "head": branch, "base": base, "body": body})


def skill_files(skill_dir, name: str) -> dict[str, str]:
    from pathlib import Path
    d = Path(skill_dir)
    return {f"skills/{name}/{p.name}": p.read_text(encoding="utf-8")
            for p in sorted(d.iterdir()) if p.is_file() and p.suffix in (".md", ".py")}


def propose_skill(name: str, skill_dir, summary: str = "", request: str = "", review: Optional[list] = None,
                  gh: Optional[GitHub] = None) -> dict:
    """Open the pull request for an installed skill. {"url", "number"} or raises with the reason."""
    ok, why = ready() if gh is None else (True, "")
    if not ok:
        raise RuntimeError(why)
    c = cfg()
    gh = gh or GitHub(c["repo"], token())
    co = gh.co_author(c.get("co_author", ""))
    files = skill_files(skill_dir, name)
    pretty = name.replace("_", " ")
    checks = "\n".join(f"- {f.get('msg')}" for f in (review or []) if f.get("msg"))[:3000]
    message = (f"Add the {pretty} skill (built by FORGE)\n\n{summary or request}\n\n"
               f"Requested: {request}\n\nCo-authored-by: {co}")
    body = (f"E.V.A. built this skill with FORGE after you asked: \"{request}\".\n\n{summary}\n\n"
            f"It passed the security review and its tests in the sandbox, and you approved installing it on "
            f"your PC.\n\n**Checks**\n{checks or '- none recorded'}\n\nMerge to keep it in the repository; close "
            f"to leave it only on this PC.")
    branch = f"eva/skill-{name.replace('_', '-')}-{time.strftime('%Y%m%d-%H%M%S')}"
    pr = gh.open_pr(base=c.get("base") or "main", branch=branch, files=files, title=f"New skill: {pretty}",
                    message=message, body=body,
                    author={"name": c["author_name"], "email": c["author_email"],
                            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    logger.info(f"GITHUB: pull request #{pr.get('number')} for skill {name}")
    return {"url": pr.get("html_url", ""), "number": pr.get("number")}


def propose_in_background(name: str, skill_dir, summary: str, request: str, review: list,
                          tell: Callable[[str], None]) -> bool:
    """After an install: open the PR off the conversation's path and say how it went. False when not set up."""
    ok, why = ready()
    if not ok:
        logger.info(f"GITHUB: no pull request for {name} ({why})")
        return False

    def run():
        try:
            pr = propose_skill(name, skill_dir, summary, request, review)
            tell(f"I've opened pull request {pr['number']} on GitHub with the {name.replace('_', ' ')} skill, "
                 f"with you as co-author, sir.")
        except Exception as e:
            logger.warning(f"GITHUB: pull request failed ({e})")
            tell(f"I couldn't open the pull request for {name.replace('_', ' ')}, sir: {str(e)[:120]}.")
    threading.Thread(target=run, daemon=True, name="github-pr").start()
    return True
