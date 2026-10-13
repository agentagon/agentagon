"""Build the public landing page and its error response as a static deployment."""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from check_site import check_site
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "website"
OUTPUT = ROOT / "site"
ORIGIN = "https://agentagon.ai"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def hosting_config(*, preview):
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }
    if preview:
        headers["X-Robots-Tag"] = "noindex"
    return {
        "trailingSlash": "auto",
        "responseOverrides": {"404": {"rewrite": "/404.html", "statusCode": 404}},
        "globalHeaders": headers,
    }


def assemble(destination, *, preview=False):
    env = Environment(
        loader=FileSystemLoader([SOURCE / "templates", SOURCE]),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    def render(template, path, title, description, **context):
        relative = path.lstrip("/")
        target = destination / (relative + "index.html" if path.endswith("/") else relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            env.get_template(template).render(
                origin=ORIGIN,
                path=path,
                title=title,
                description=description,
                article=context.pop("article", None),
                noindex=preview or path == "/404.html",
                **context,
            )
        )

    shutil.copytree(SOURCE / "assets", destination / "assets")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.txt", destination / "THIRD_PARTY_NOTICES.txt")
    render(
        "index.html",
        "/",
        "Agentagon — Reliability for the robot age.",
        "AI agents that take the controls when a robot gets stuck, find another way, "
        "and keep it working.",
    )
    render(
        "message.html",
        "/404.html",
        "Page not found | Agentagon",
        "Return to the Agentagon landing page.",
        label="404 · Page not found",
        heading="That path ends here.",
        message="This page is not available. Head back to Agentagon.",
    )
    (destination / "staticwebapp.config.json").write_text(
        json.dumps(hosting_config(preview=preview), indent=2) + "\n"
    )
    ET.register_namespace("", SITEMAP_NS)
    sitemap = ET.Element(f"{{{SITEMAP_NS}}}urlset")
    entry = ET.SubElement(sitemap, f"{{{SITEMAP_NS}}}url")
    ET.SubElement(entry, f"{{{SITEMAP_NS}}}loc").text = ORIGIN + "/"
    ET.ElementTree(sitemap).write(
        destination / "sitemap.xml", encoding="utf-8", xml_declaration=True
    )
    robots = (
        "User-agent: *\nDisallow: /\n"
        if preview
        else (f"User-agent: *\nAllow: /\nSitemap: {ORIGIN}/sitemap.xml\n")
    )
    (destination / "robots.txt").write_text(robots)
    check_site(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Prevent preview indexing")
    args = parser.parse_args()
    if OUTPUT.is_symlink():
        sys.exit("Refusing to replace a symlink at site/")
    with tempfile.TemporaryDirectory(prefix="agentagon-site-") as temporary:
        staged = Path(temporary) / "site"
        assemble(staged, preview=args.preview)
        if OUTPUT.exists():
            shutil.rmtree(OUTPUT)
        shutil.copytree(staged, OUTPUT)
    print(f"Built and checked {OUTPUT}")


if __name__ == "__main__":
    main()
