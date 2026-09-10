# Agentagon public website

The website ships with the documentation from this repository. Follow the [build and deployment guide](../docs/contributing/docs-site.md) for local preview, validation, Azure deployment, and domain setup.

- `index.html` preserves the approved `agentagon-os/assets/landing-page/index.html` hero.
- `assets/` contains the required causeway artwork, split-crown logo, styles, and scripts.
- `templates/` contains the shared public header, article layouts, and error/transition pages.
- `blogs/` contains the three adapted Aegon articles and their original dates and routes.

The causeway artwork and its sun/moon variants were generated for Agentagon and copied from the approved brand source. The original artwork prompts remain in `agentagon-os/assets/landing-page/index.html`. The GitHub icon's MIT notice is retained in the shared header.

Content renders as static HTML. Theme selection and the landing benefit rotation are the only visitor-side JavaScript; the documentation also uses Material's search, navigation, and code-copy controls. There is no visitor sign-in, form backend, analytics integration, or connection to the retired application.
