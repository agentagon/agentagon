"""Keep repository-relative source links useful on the public documentation site."""

import re
from pathlib import Path
from urllib.parse import quote, urlsplit


def on_page_markdown(markdown, *, page, config, files):
    docs = Path(config["docs_dir"]).resolve()
    repo = docs.parent
    current = Path(page.file.abs_src_path).parent
    source_url = config["repo_url"].rstrip("/") + "/blob/main/"

    def replace(match):
        target = match.group(1)
        parsed = urlsplit(target)
        if parsed.scheme or target.startswith(("#", "/")):
            return match.group(0)
        path = (current / parsed.path).resolve()
        if path.is_relative_to(docs) and not path.is_relative_to(docs / "contributing"):
            return match.group(0)
        if not path.is_relative_to(repo) or not path.exists():
            return match.group(0)
        suffix = "#" + parsed.fragment if parsed.fragment else ""
        return "](" + source_url + quote(path.relative_to(repo).as_posix()) + suffix + ")"

    return re.sub(r"\]\(([^\s)]+)\)", replace, markdown)
