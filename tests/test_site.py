"""Publication checks for the landing-only static site."""

import importlib.util
import json
import sys
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("jinja2")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
preview_spec = importlib.util.spec_from_file_location(
    "preview_site", ROOT / "scripts" / "preview_site.py"
)
preview = importlib.util.module_from_spec(preview_spec)
preview_spec.loader.exec_module(preview)
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
    assert urls == ["https://agentagon.ai/"]
    assert {path.relative_to(built_site).as_posix() for path in built_site.rglob("*.html")} == {
        "index.html",
    }
    assert not (built_site / "docs").exists()
    assert not (built_site / "blogs").exists()
    for page in built_site.rglob("*.html"):
        html = page.read_text()
        assert 'href="/docs' not in html
        assert 'href="/blogs' not in html
        assert 'href="https://github.com/' not in html
    html = (built_site / "index.html").read_text()
    assert 'href="https://agentagon.ai/"' in html
    assert 'href="mailto:founders@agentagon.ai"' in html


def test_unknown_routes_redirect_home(built_site):
    config = json.loads((built_site / "staticwebapp.config.json").read_text())
    assert "routes" not in config
    assert config["responseOverrides"]["404"] == {"redirect": "/", "statusCode": 302}
    assert "navigationFallback" not in config
    assert not (built_site / "welcome").exists()
    assert not (built_site / "field-notes/same-model-different-agent-behavior/index.html").exists()
    assert not (built_site / "404.html").exists()
    assert "X-Robots-Tag" not in config["globalHeaders"]


def test_local_preview_redirects_missing_paths_and_serves_existing_assets(built_site):
    handler = partial(preview.HomepagePreviewHandler, directory=str(built_site))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            for method in ["GET", "HEAD"]:
                for path in ["/docs/", "/blogs/", "/missing/path?from=old-link", "/404.html"]:
                    connection.request(method, path)
                    response = connection.getresponse()
                    assert response.status == 302
                    assert response.getheader("Location") == "/"
                    assert response.read() == b""
            for path in ["/", "/assets/touch-interaction.js"]:
                connection.request("GET", path)
                response = connection.getresponse()
                assert response.status == 200
                assert response.read()
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=5)


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
    html = (built_site / "index.html").read_text()
    assert "Made with" not in html
    assert (
        "Copyright (c) 2016-2025 Martin Donath"
        in (built_site / "THIRD_PARTY_NOTICES.txt").read_text()
    )
