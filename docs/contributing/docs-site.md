# Build and host the public website

The landing page, documentation, and blog deploy together as a static Azure Static Web App. Public documentation lives at `/docs/`, rendered from Markdown with Material for MkDocs. Contributor-only material remains on GitHub and is excluded from the site build.

## Preview locally

From the repository root, use Python 3.12+:

```sh
python3 -m venv .venv-docs
. .venv-docs/bin/activate
python -m pip install -r requirements-docs.txt
python scripts/build_site.py
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8000/`. Rebuild after editing content. Stop the server with Ctrl+C. This simple local server previews content; Azure applies the configured redirects and error responses. The docs environment is separate from application/runtime dependencies.

## Validate and build

```sh
python scripts/build_site.py
python scripts/check_site.py
```

The builder renders the landing and article templates, runs MkDocs in strict mode, and checks internal links, anchors, and assets before replacing `site/`. That directory and `.venv-docs/` are ignored. The output contains no application state and requires no backend. A plain `python -m mkdocs build --strict` remains useful for checking documentation alone; run the combined build before publishing.

Run link checks and inspect installation, search, host tabs, code-copy controls, mobile navigation, and both themes before release. The source-link hook maps links outside public docs to their GitHub source locations; publish the matching repository content before publicly releasing those links.

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
| Documentation canonical URL | `https://agentagon.ai/docs/` in `mkdocs.yml` |
| Deployment secret | GitHub Actions secret `AZURE_STATIC_WEB_APPS_API_TOKEN` |

The website workflow builds changes on pushes and pull requests. Successful `main` builds deploy production; same-repository pull requests deploy previews, which are removed when the PR closes. Fork PRs only build and validate. Previews use an `X-Robots-Tag: noindex` header and disallow crawling. The deployment action uploads the already-validated output with its own build disabled.

Before a domain cutover:

1. Verify the full site on Azure's generated HTTPS hostname. Do not use its IP address as the preview URL.
2. Verify anonymous access to the public repository and the documented installation path.
3. Add both domains in Azure using TXT validation and retain the exact supplied ownership records.
4. Record the current Namecheap web records for rollback. Set the apex ALIAS and `www` CNAME to the generated Azure hostname, preserving mail and unrelated records. Do not migrate nameservers.
5. Wait for domain validation and HTTPS, then set `agentagon.ai` as Azure's default domain.
6. Verify `/docs/getting-started/install/`, docs search, blog links, legacy redirects, and a genuine 404. Retain the old Railway service until its removal is separately authorized.

Building the site changes no cloud resources or DNS. See [Azure deployment configuration](https://learn.microsoft.com/en-us/azure/static-web-apps/build-configuration), [custom domains](https://learn.microsoft.com/en-us/azure/static-web-apps/custom-domain), and [default domain redirects](https://learn.microsoft.com/en-us/azure/static-web-apps/custom-domain-default).

## Maintain the landing page and articles

Website source lives in `website/`. The landing template preserves the approved causeway design; shared templates place the logo beside the wordmark, center the Docs and Blogs links, and provide the theme control and article layouts. Get Started opens the short first-audit guide. All required assets are checked in, so builds do not depend on sibling repositories.

Add articles as Markdown in `website/blogs/`. Frontmatter requires `title`, `description`, `author`, `published_at`, `updated_at`, and `original_url`. Filenames become `/blogs/<filename>/` routes. Use the approved publication dates and set the revision date to the actual edit date. The builder creates article metadata, related links, and permanent redirects from each original URL.

The initial articles were adapted from the corresponding files under `aegon/web/src/content/seo/pages/`. They describe the current CLI/plugin workflows. The old model-comparison study is excluded because its reported rates and counts require reconciliation.

The build routes former sign-in, demo, and workspace entries to `/welcome/`. Unselected legacy articles and unknown paths return the branded 404; there is no catch-all SPA fallback.

## Maintain the content

- Use an end-user task as the page title and state prerequisites before commands.
- Prefer coding-host instructions for workflows that need reasoning; link CLI contracts as advanced references.
- Keep each behavioral rule authoritative in one place and link from related guides.
- Retain existing public page paths and anchors when possible. If changing a published route, add redirects through the selected host.
- Keep product claims tied to implementation. Avoid unverified benchmark results, release dates, pricing, or “fully local” processing claims.
- Validate new options against the installed CLI help. Build with strict link validation.
- Do not add the contributor testing playbook or personal run evidence to public navigation.

## Brand and assets

The brand reference is `agentagon-os/assets/landing-page/index.html`. Docs use its split-crown logo, frost/navy colors, system sans-serif and monospace fonts, rounded primary buttons, and theme controls. The introduction alone uses the approved light/dark causeway artwork and editorial serif emphasis. Articles keep plain reading surfaces.

The copied logo and causeway images in `docs/assets/` are self-contained; building the docs does not require the sibling repository. Keep those copies aligned when the approved landing-page brand changes. The artwork is cropped by CSS, with no animation. All pages initially follow the system theme and offer a light/dark toggle. Documentation uses Material's own saved preference; the landing page and articles share a separate preference. A visitor's explicit choice takes precedence over their system setting.

`docs_theme/partials/header.html` preserves the Material 9.7.7 header behavior and customizes its first title topic for the wordmark and separate Docs label. The logo returns to the site root. Compare this override with upstream when upgrading Material. CI includes changes to `docs_theme/`.

The retained `dashboard-example.png` asset captures the bundled `examples/local-audit/demo.py` workspace while awaiting evidence review. Recreate it with that example and a loopback dashboard; any use must retain the unfinished-state caption. Use synthetic evidence only.

Legacy capabilities and local-example routes remain available through contextual links. Moved fix sections retain their old anchors and link to the detailed reference. This configuration adds no analytics integration, external font service, or visitor authentication.
