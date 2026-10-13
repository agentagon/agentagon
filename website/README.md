# Agentagon public landing page

The website build publishes the Agentagon landing page and its 404 response. Documentation and blog source remain in the repository but are not included in the public site. Follow the [build and deployment guide](../docs/contributing/docs-site.md) for local preview and Azure deployment.

The headline is “Reliability for the robot age.”, with one Talk to us contact action. The split-crown wordmark returns to the homepage. There is no Docs, Blogs, GitHub, privacy, or theme navigation.

The fixed charcoal appearance, transparent hands, and curved image boundaries are defined in the landing template and assets. The hands were generated for Agentagon, then extracted onto a transparent PNG. Their background-extraction prompt is retained beside the asset. The animation rig uses the 1774×887 source coordinate system; replacing the composition requires updating joint pivots and signal paths together.

Hover bends both wrists and all finger joints. Finger curls are 50% stronger than the initial PR version; wrist range remains restrained. Each click or keyboard activation creates a separate signal from the AI wrist through the fingertips to the robot wrist, with 50% stronger glow and trail intensity. Signals complete the full journey in about 1.5 seconds. The connecting fingertip thread and its crossing pulse are twice as thick as the previous 50%-thicker version. Motion pauses offscreen. Reduced-motion visitors receive a brief static highlight, and the original image remains visible without WebGL.

Unknown paths, including /docs/ and /blogs/, return a 404 with a link back home. The sitemap contains only the homepage. Builds retain third-party notices and add no analytics, sign-in, forms, or backend.
