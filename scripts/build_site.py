"""Build the public website and MkDocs documentation as one static deployment."""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import markdown
import yaml
from check_site import check_site
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from mkdocs.commands.build import build as build_docs
from mkdocs.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "website"
OUTPUT = ROOT / "site"
ORIGIN = "https://agentagon.ai"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
RETIRED_PREFIXES = (
    "/auth",
    "/verify-email",
    "/request-access",
    "/onboarding",
    "/app",
    "/demo",
    "/login",
    "/signup",
    "/dashboard",
    "/providers",
    "/settings",
    "/schema-review",
)


def load_posts():
    posts = []
    for source in sorted((SOURCE / "blogs").glob("*.md")):
        _, frontmatter, body = source.read_text().split("---", 2)
        post = yaml.safe_load(frontmatter)
        for key in ("title", "description", "author", "original_url"):
            if not isinstance(post.get(key), str) or not post[key].strip():
                raise ValueError(f"{source.name}: missing {key}")
        for key in ("published_at", "updated_at"):
            value = date.fromisoformat(str(post[key]))
            post[key] = value.isoformat()
            post[key.replace("_at", "_label")] = value.strftime("%B %d, %Y").replace(" 0", " ")
        post["path"] = f"/blogs/{source.stem}/"
        post["html"] = markdown.markdown(body, extensions=["fenced_code", "tables", "toc"])
        post["structured_data"] = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": post["title"],
            "description": post["description"],
            "author": {"@type": "Person", "name": post["author"]},
            "datePublished": post["published_at"],
            "dateModified": post["updated_at"],
            "mainEntityOfPage": ORIGIN + post["path"],
            "image": ORIGIN + "/assets/tidal-causeway-light-sun.png",
        }
        posts.append(post)
    return sorted(posts, key=lambda post: post["published_at"], reverse=True)


def hosting_config(posts, *, preview):
    redirects = {"/resources/": "/blogs/", "/privacy/": "/docs/privacy/"}
    redirects.update({post["original_url"]: post["path"] for post in posts})
    routes = []
    for old, new in redirects.items():
        # Azure normalizes trailing slashes when checking route uniqueness.
        routes.append({"route": old, "redirect": new, "statusCode": 301})
    for prefix in RETIRED_PREFIXES:
        for route in (prefix, prefix + "/*"):
            routes.append({"route": route, "redirect": "/welcome/", "statusCode": 301})
    routes.append({"route": "/welcome/*", "headers": {"X-Robots-Tag": "noindex"}})
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }
    if preview:
        headers["X-Robots-Tag"] = "noindex"
    return {
        "trailingSlash": "auto",
        "routes": routes,
        "responseOverrides": {"404": {"rewrite": "/404.html", "statusCode": 404}},
        "globalHeaders": headers,
    }


def assemble(destination, *, preview=False):
    posts = load_posts()
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
                noindex=preview or path in ("/welcome/", "/404.html"),
                posts=posts,
                **context,
            )
        )

    shutil.copytree(SOURCE / "assets", destination / "assets")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.txt", destination / "THIRD_PARTY_NOTICES.txt")
    render(
        "index.html",
        "/",
        "Agentagon — Improve your AI agents. Continuously.",
        "Audit your AI agent, prepare evaluations, and compare measured improvements "
        "with the coding agent and tools you already use.",
    )
    render(
        "blogs.html",
        "/blogs/",
        "Agentagon Blog — Better agents. Measured, not assumed.",
        "Notes on agent behavior, regression testing, and measured improvements.",
    )
    for post in posts:
        render(
            "article.html",
            post["path"],
            post["title"] + " | Agentagon",
            post["description"],
            article=post,
        )
    render(
        "message.html",
        "/welcome/",
        "A new way to use Agentagon",
        "Get started with the Agentagon CLI and plugin for your coding agent.",
        label="A new way to use Agentagon",
        heading="Bring Agentagon to your coding agent.",
        message="The previous web workspace and demo are retired. "
        "Install the CLI and ag plugin, open your agent’s code repo in Codex or Claude Code, "
        "and start with an audit. Core workflows need no Agentagon account.",
    )
    render(
        "message.html",
        "/404.html",
        "Page not found | Agentagon",
        "Find Agentagon documentation, articles, and installation instructions.",
        label="404 · Page not found",
        heading="That path ends here.",
        message="This page has moved or is no longer available. "
        "Start with the docs, or head back to Agentagon.",
    )
    config = load_config(config_file=str(ROOT / "mkdocs.yml"), site_dir=str(destination / "docs"))
    build_docs(config)
    (destination / "staticwebapp.config.json").write_text(
        json.dumps(hosting_config(posts, preview=preview), indent=2) + "\n"
    )
    ET.register_namespace("", SITEMAP_NS)
    sitemap = ET.parse(destination / "docs" / "sitemap.xml").getroot()
    for path in ("/", "/blogs/", *(post["path"] for post in posts)):
        entry = ET.SubElement(sitemap, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(entry, f"{{{SITEMAP_NS}}}loc").text = ORIGIN + path
    ET.ElementTree(sitemap).write(
        destination / "sitemap.xml", encoding="utf-8", xml_declaration=True
    )
    robots = (
        "User-agent: *\nDisallow: /\n"
        if preview
        else (f"User-agent: *\nAllow: /\nDisallow: /welcome/\nSitemap: {ORIGIN}/sitemap.xml\n")
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
