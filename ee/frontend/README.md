# Torque EE frontend skeleton

Enterprise-only remote frontend assets live here. Community builds do not copy
`ee/`, and `webview.html` ships with an empty `#torque-ee-frontend-manifest` so
no remote UI appears unless enterprise packaging injects a manifest before
`static/js/panel_manager.js` runs.

Future Panelsmith work can register no-build panel roots through a manifest like:

```json
{
  "version": 1,
  "panels": [
    {
      "id": "remote",
      "title": "Remote",
      "root_id": "panel-remote",
      "default_zone": "right"
    }
  ]
}
```
