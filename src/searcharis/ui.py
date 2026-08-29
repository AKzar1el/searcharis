from __future__ import annotations

from html import escape

from searcharis.models import IncidentRecord


def render_incident_timeline(incidents: list[IncidentRecord]) -> str:
    rows = []
    for incident in incidents:
        issue = (
            f'<a href="{escape(str(incident.github_issue_url))}">#{incident.github_issue_number}</a>'
            if incident.github_issue_url and incident.github_issue_number
            else "—"
        )
        rows.append(
            "<tr>"
            f"<td><code>{escape(incident.finding_code)}</code></td>"
            f"<td>{escape(str(incident.affected_url))}</td>"
            f"<td><strong>{escape(incident.state.value)}</strong></td>"
            f"<td>{issue}</td>"
            "</tr>"
        )
    table_rows = "".join(rows) or '<tr><td colspan="4">No incidents yet.</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Searcharis — Autonomous Search Regression Guardian</title>
  <style>
    body{{font-family:system-ui,sans-serif;max-width:1050px;margin:48px auto;padding:0 20px;color:#151515}}
    h1{{margin-bottom:0}} .sub{{color:#555;margin-top:6px}} table{{border-collapse:collapse;width:100%;margin-top:28px}}
    th,td{{text-align:left;padding:12px;border-bottom:1px solid #ddd}} code{{font-size:.92em}}
    .badge{{display:inline-block;padding:5px 9px;border:1px solid #bbb;border-radius:999px;font-size:.85rem}}
  </style>
</head>
<body>
  <span class="badge">Google Cloud Run</span>
  <h1>Searcharis</h1>
  <p class="sub">Autonomous Search Regression Guardian</p>
  <p>Deployment-triggered audit → Gemini diagnosis → deterministic policy → GitHub incident → independent verification.</p>
  <table>
    <thead><tr><th>Finding</th><th>Target</th><th>Workflow state</th><th>GitHub incident</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>"""
