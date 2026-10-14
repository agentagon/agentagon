# Build and host the public website

The public website deploys as a static Azure Static Web App containing the landing page with homepage redirects. Documentation and blog source remain on GitHub and are excluded from the deployed site.

## Preview locally

From the repository root, use Python 3.12+:

```sh
python3 -m venv .venv-docs
. .venv-docs/bin/activate
python -m pip install -r requirements-docs.txt
python scripts/build_site.py
python scripts/preview_site.py --port 8000
```

Open `http://127.0.0.1:8000/`. Rebuild after editing content. Stop the server with Ctrl+C. The local preview redirects missing URLs to the homepage, matching Azure’s configured redirect. Azure also applies the configured response headers. The docs environment is separate from application/runtime dependencies.

## Validate and build

```sh
python scripts/build_site.py
python scripts/check_site.py
```

The builder renders the landing template and checks internal links, anchors, and assets before replacing site/. The output contains no application state and requires no backend. MkDocs can still validate repository documentation separately; its output is not included in the public website.

Before release, inspect the landing page at desktop and mobile widths, its contact link, finger motion, independent pulses, and reduced-motion response. Verify that documentation and blog directories are absent from the website output.

## Deploy to Azure Static Web Apps

Use the Free `agentagon-site` Static Web App in resource group `rg-agentagon-site`, subscription `agentagon`. Static content is distributed globally; the resource location is West US 2. The hosted site needs no Node runtime, application worker, database, or visitor authentication.

| Setting | Value |
|---|---|
| Build runtime | Python 3.12+ |
| Install command | `python -m pip install -r requirements-docs.txt` |
| Build command | `python scripts/build_site.py` |
| Output directory | `site` |
| Primary custom domain | `agentagon.ai` |
| Alternate custom domain | `www.agentagon.ai`, redirected to the primary domain |
| Published pages | Landing page only; unknown URLs redirect home |
| Deployment secret | GitHub Actions secret `AZURE_STATIC_WEB_APPS_API_TOKEN` |

The website workflow builds changes on pushes and pull requests. Successful `main` builds deploy production; same-repository pull requests deploy previews, which are removed when the PR closes. Fork PRs only build and validate. Previews use an `X-Robots-Tag: noindex` header and disallow crawling. The deployment action uploads the already-validated output with its own build disabled.

Before a domain cutover:

1. Verify the full site on Azure's generated HTTPS hostname. Do not use its IP address as the preview URL.
2. Verify anonymous access to the public repository and the documented installation path.
3. Add both domains in Azure using TXT validation and retain the exact supplied ownership records.
4. Record the current Namecheap web records for rollback. Set the apex ALIAS and `www` CNAME to the generated Azure hostname, preserving mail and unrelated records. Do not migrate nameservers.
5. Wait for domain validation and HTTPS, then set `agentagon.ai` as Azure's default domain.
6. Verify the landing page and homepage redirects for /docs/, /blogs/, and unknown paths. Retain the old Railway service until its removal is separately authorized.

Building the site changes no cloud resources or DNS. See [Azure deployment configuration](https://learn.microsoft.com/en-us/azure/static-web-apps/build-configuration), [custom domains](https://learn.microsoft.com/en-us/azure/static-web-apps/custom-domain), and [default domain redirects](https://learn.microsoft.com/en-us/azure/static-web-apps/custom-domain-default).

## Maintain the landing page

Website source lives in website/. The landing template presents “Reliability for the robot age.” above transparent titanium and glass-filament hands, with a single Talk to us action. The page uses a fixed charcoal theme and no secondary navigation. All required assets are checked in.

The build publishes only index.html, plus local assets, notices, robots policy, sitemap, and hosting configuration. The sitemap lists only the homepage. Unknown paths, including documentation and blog routes, redirect to the homepage with HTTP 302; there is no catch-all SPA rewrite.

Repository documentation and blog drafts are retained as source material but are not part of the public site.

## Maintain the content

- Use an end-user task as the page title and state prerequisites before commands.
- Prefer coding-host instructions for workflows that need reasoning; link CLI contracts as advanced references.
- Keep each behavioral rule authoritative in one place and link from related guides.
- Retain existing public page paths and anchors when possible. If changing a published route, add redirects through the selected host.
- Keep product claims tied to implementation. Avoid unverified benchmark results, release dates, pricing, or “fully local” processing claims.
- Validate new options against the installed CLI help. Build with strict link validation.
- Do not add the contributor testing playbook or personal run evidence to public navigation.

## Brand and assets

The landing page retains the split-crown logo, Helvetica Neue display typography, editorial serif emphasis, and rounded primary contact button. The hands PNG retains the original glass colors and charcoal backdrop; CSS lighten compositing and edge masks blend it into the page without the colored fringe produced by the extracted alpha matte.

Hover rotates wrists and anatomical finger joints. Finger curls are 50% stronger than the initial PR version; wrist motion stays restrained. Repeated clicks create independent pulses through both hands, with 50% stronger glow and trail intensity. Signals complete the full journey in about 1.5 seconds. The connecting fingertip thread and its crossing pulse are twice as thick as the previous 50%-thicker version. Reduced-motion visitors receive a brief highlight, and the static image remains available without WebGL. The source image, source-coordinate rig, and asset prompt are maintained together in website/assets/.

Documentation branding and theme files remain available for independent documentation builds. The public website adds no analytics, external fonts, or visitor authentication.
