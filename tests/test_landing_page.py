"""The public landing stays usable without motion and has no secondary navigation."""

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")
ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def render(template, path):
    source = ROOT / "website"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([source / "templates", source]),
        undefined=jinja2.StrictUndefined,
        autoescape=jinja2.select_autoescape(["html"]),
    )
    return env.get_template(template).render(
        origin="https://agentagon.ai",
        path=path,
        title="Agentagon",
        description="Robot reliability",
        article=None,
        noindex=False,
        posts=[],
    )


def test_landing_has_static_art_contact_and_accessible_motion_control():
    html = render("index.html", "/")
    elements = Elements(html).elements
    image = next(attrs for tag, attrs in elements if tag == "img" and attrs.get("fetchpriority"))
    assert image["src"] == "/assets/titanium-hands-clean.png"
    assert image["alt"] and image["width"] == "1774" and image["height"] == "887"
    assert (ROOT / "website" / image["src"].lstrip("/")).is_file()
    assert any(
        tag == "a" and attrs.get("href") == "mailto:founders@agentagon.ai"
        for tag, attrs in elements
    )
    control = next(attrs for tag, attrs in elements if attrs.get("id") == "ag-touch-stage")
    assert control["type"] == "button" and control["aria-label"]
    assert "disabled" in control  # No dead control before JS/renderer initialization.
    assert control["aria-label"] == "Robot and AI hands (static image)"
    assert "Reliability <em>for the robot age.</em>" in html


def test_landing_has_fixed_theme_and_only_home_contact_links():
    homepage = Elements(render("index.html", "/")).elements
    assert next(attrs for tag, attrs in homepage if tag == "html")["data-theme"] == "dark"
    assert not any(attrs.get("class") == "theme-toggle" for _, attrs in homepage)
    assert not any(
        attrs.get("src") in {"/assets/theme-init.js", "/assets/site.js"} for _, attrs in homepage
    )
    links = {attrs["href"] for tag, attrs in homepage if tag == "a"}
    assert links == {"#main", "/", "mailto:founders@agentagon.ai"}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_landing_animation_lifecycle():
    subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "landing_interaction.test.cjs")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
