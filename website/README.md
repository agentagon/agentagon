# Agentagon public website

The website ships with the documentation from this repository. Follow the [build and deployment guide](../docs/contributing/docs-site.md) for local preview, validation, Azure deployment, and domain setup.

- `index.html` renders the approved “Reliability for the robot age.” landing page.
- `assets/landing-hands.css`, `assets/touch-interaction.js`, and `assets/titanium-hands-transparent.png` supply the centered robot/AI hands, joint motion, and click pulses.
- `assets/` also contains the shared split-crown logo and the retained causeway artwork used by other page metadata.
- `templates/` contains the shared public header, article layouts, and error/transition pages.
- `blogs/` contains the three adapted Aegon articles and their original dates and routes.

The causeway artwork and its sun/moon variants were generated for Agentagon and copied from the approved brand source. The original artwork prompts remain in `agentagon-os/assets/landing-page/index.html`. The GitHub icon's MIT notice is retained in the shared header.

The hands were generated for Agentagon, then extracted onto a transparent PNG. Their background-extraction prompt is retained beside the asset. The animation rig uses that asset's 1774×887 coordinate system; replacing the composition requires updating the joint pivots and signal paths together.

Content renders as static HTML. The landing page has a fixed charcoal theme and one contact CTA. Hover gently bends the wrists and finger joints; each click or keyboard activation creates a separate signal from the AI wrist through the fingertips to the robot wrist. Motion pauses offscreen, and reduced-motion visitors get a brief connection highlight. The original image remains visible if WebGL is unavailable.

Articles retain theme selection, and documentation uses Material's search, navigation, and code-copy controls. There is no visitor sign-in, form backend, analytics integration, or connection to the retired application.
