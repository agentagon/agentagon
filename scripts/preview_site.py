"""Preview the built landing page locally, redirecting missing URLs home."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "site"


class HomepagePreviewHandler(SimpleHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == 404:
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            super().send_error(code, message, explain)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if not (OUTPUT / "index.html").is_file():
        parser.error("Build the site first with python scripts/build_site.py")
    handler = partial(HomepagePreviewHandler, directory=str(OUTPUT))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Preview at http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
