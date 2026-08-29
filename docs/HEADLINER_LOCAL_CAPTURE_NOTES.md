# Headliner Local Capture Notes

Local reference folder:

`C:\Users\mavin\Documents\Sites`

Captured app folder:

`C:\Users\mavin\Documents\Sites\acAC`

## What This Capture Contains

The HTTrack capture includes the public `make.headliner.app` single-page
application shell and static webapp assets for Headliner webapp version
`6.33.0`.

Observed shell/assets:

- `make.headliner.app_443/index.html`
- `static/webapp-assets/webapp/public/6.33.0/css/app.min.css`
- `static/webapp-assets/webapp/public/6.33.0/js/app.bundle.min.js`
- `static/webapp-assets/webapp/public/6.33.0/js/vendor_app.bundle.min.js`
- `static/webapp-assets/webapp/public/6.33.0/js/vendor_app_canva.bundle.min.js`

Observed bundled library/style references:

- Cropper
- rc-slider
- video-react
- bootstrap-slider
- react-slick
- react-toggle
- Bootstrap
- Canva integration bundle
- Dropbox chooser script

## Clean-Room Boundary

This capture may be used only as reference evidence for public app structure,
visible workflows, and behavioral expectations.

Do not:

- copy Headliner JavaScript, CSS, images, fonts, logos, names, templates, or
  other assets into Kinder;
- decompile or port Headliner's minified application bundles;
- copy private API contracts or implementation details;
- reuse Headliner branding or commercial assets.

Do:

- independently implement equivalent self-hosted behavior;
- use public documentation and manual observation to update the parity matrix;
- write our own UI, renderer, project format, and backend services;
- keep failures visible in `HEADLINER_PARITY_MATRIX.md`.

## Security Note

HTTrack log/cache files can contain capture URLs and credentials. Do not share
the local capture folder unless the `hts-log.txt` and `hts-cache` files have
been removed or sanitized.

## Product Implications

The local app capture reinforces several parity targets already tracked in the
matrix:

- slider-based editing controls;
- browser video preview;
- crop/replace media behavior;
- toggles for binary style/project options;
- carousel/template browsing;
- third-party import entry points such as Dropbox/Canva.

For this project, third-party integrations remain optional. The self-hosted MVP
should prioritize local upload, RSS import, transcript editing, clip selection,
canvas/layer editing, and local render/export before optional SaaS import
providers.

