"""Check internal HTML links, anchors, and local assets in a built static site."""

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


class PageLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.ids.add(attrs["name"])
        for attribute in ("href", "src", "poster"):
            if attrs.get(attribute):
                self.links.append(attrs[attribute])


def check_site(root):
    root = root.resolve()
    pages = {path: PageLinks(path.read_text()) for path in root.rglob("*.html")}
    problems = []

    def check(source, link):
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc:
            return
        source_url = "/" + source.relative_to(root).as_posix()
        resolved = urlsplit(urljoin(source_url, link))
        target = (root / unquote(resolved.path).lstrip("/")).resolve()
        if not target.is_relative_to(root):
            problems.append(f"{source.relative_to(root)}: link escapes site: {link}")
            return
        if target.is_dir():
            target /= "index.html"
        if not target.is_file():
            problems.append(f"{source.relative_to(root)}: missing {link}")
        elif resolved.fragment and target in pages:
            anchor = unquote(resolved.fragment)
            if not anchor.startswith(":~:text=") and anchor not in pages[target].ids:
                problems.append(f"{source.relative_to(root)}: missing anchor {link}")

    for path, page in pages.items():
        for link in page.links:
            check(path, link)
    for path in root.rglob("*.css"):
        for link in re.findall(r"url\(['\"]?([^)'\"]+)", path.read_text()):
            check(path, link)
    if problems:
        raise ValueError("Broken site links:\n" + "\n".join(sorted(set(problems))))
    print(f"Checked links and assets across {len(pages)} HTML pages")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, nargs="?", default=Path("site"))
    check_site(parser.parse_args().site)
