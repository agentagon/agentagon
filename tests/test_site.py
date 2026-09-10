"""Publication checks for the generated site; no documentation dependencies in the CLI."""

import importlib.util
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("mkdocs")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
sys.path.remove(str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def built_site(tmp_path_factory):
    site = tmp_path_factory.mktemp("public-site")
    builder.assemble(site)
    return site


def test_public_routes_metadata_and_sitemap(built_site):
    sitemap = ET.parse(built_site / "sitemap.xml")
    urls = [node.text for node in sitemap.findall(".//{*}loc")]
    assert "https://agentagon.ai/" in urls
    assert "https://agentagon.ai/docs/getting-started/install/" in urls
    assert len([url for url in urls if url.startswith("https://agentagon.ai/blogs/")]) == 4
    assert not any("welcome" in url or "404" in url or "docs.agentagon.ai" in url for url in urls)
    for post in builder.load_posts():
        html = (built_site / post["path"].strip("/") / "index.html").read_text()
        assert f'href="https://agentagon.ai{post["path"]}"' in html
        assert '"@type": "TechArticle"' in html
        assert 'href="/docs/getting-started/first-audit/#your-first-audit"' in html
        assert 'href="/docs/getting-started/install/"' in html
        assert 'href="/auth' not in html


def test_retired_routes_redirect_without_hiding_missing_pages(built_site):
    config = json.loads((built_site / "staticwebapp.config.json").read_text())
    routes = {route["route"]: route for route in config["routes"]}
    assert len({route.rstrip("/") for route in routes}) == len(config["routes"])
    assert routes["/resources/"]["redirect"] == "/blogs/"
    assert (
        routes["/field-notes/what-flat-scores-hide/"]["redirect"] == "/blogs/what-flat-scores-hide/"
    )
    assert routes["/app/*"]["redirect"] == "/welcome/"
    assert routes["/demo"]["statusCode"] == 301
    assert config["responseOverrides"]["404"]["statusCode"] == 404
    assert "navigationFallback" not in config
    assert not (built_site / "field-notes/same-model-different-agent-behavior/index.html").exists()
    assert (
        '<meta name="robots" content="noindex"' in (built_site / "welcome/index.html").read_text()
    )
    assert "X-Robots-Tag" not in config["globalHeaders"]


def test_preview_indexing_is_disabled(tmp_path):
    builder.assemble(tmp_path, preview=True)
    config = json.loads((tmp_path / "staticwebapp.config.json").read_text())
    assert config["globalHeaders"]["X-Robots-Tag"] == "noindex"
    assert "Disallow: /\n" in (tmp_path / "robots.txt").read_text()
    assert '<meta name="robots" content="noindex"' in (tmp_path / "index.html").read_text()


def test_checker_rejects_broken_anchors_and_assets(tmp_path):
    (tmp_path / "index.html").write_text('<a href="#missing">Broken</a><img src="/absent.png">')
    with pytest.raises(ValueError, match="missing anchor"):
        builder.check_site(tmp_path)


def test_distributed_notices_survive_without_a_visible_generator_credit(built_site):
    assert (built_site / "THIRD_PARTY_NOTICES.txt").read_bytes() == (
        ROOT / "THIRD_PARTY_NOTICES.txt"
    ).read_bytes()
    html = (built_site / "docs/index.html").read_text()
    assert "MIT license and permission notice: /THIRD_PARTY_NOTICES.txt" in html
    assert "Made with" not in html
    assert (
        "Copyright (c) 2016-2025 Martin Donath"
        in (built_site / "THIRD_PARTY_NOTICES.txt").read_text()
    )
