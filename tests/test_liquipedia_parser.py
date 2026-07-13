"""Golden HTML fixtures for Liquipedia defensive parsing."""

from dota2_scraper.models import FetchJob, FetchedPage, PageKind, Source
from dota2_scraper.parsers.liquipedia_parser import LiquipediaParser


PORTAL_HTML = """
<html><body>
<table>
  <tr><th>Event</th><th>Tier</th><th>Date</th><th>Prize</th></tr>
  <tr>
    <td><a href="/dota2/The_International_2025">The International 2025</a></td>
    <td>S-Tier</td>
    <td>2025-10-01</td>
    <td>$2,000,000</td>
  </tr>
</table>
<table>
  <tr><th>Team 1</th><th>Score</th><th>Team 2</th></tr>
  <tr>
    <td><a href="/dota2/Team_Spirit">Team Spirit</a></td>
    <td>2-1</td>
    <td><a href="/dota2/Team_Liquid">Team Liquid</a></td>
  </tr>
</table>
<a href="/dota2/Portal:Teams">Teams portal</a>
</body></html>
"""


def test_liquipedia_portal_extracts_tournaments_and_discovers_links() -> None:
    job = FetchJob(
        url="https://liquipedia.net/dota2/Portal:Tournaments",
        source=Source.LIQUIPEDIA,
        kind=PageKind.LIQUIPEDIA_PORTAL,
        depth=0,
    )
    page = FetchedPage(
        job=job,
        html=PORTAL_HTML,
        final_url=job.url,
        status_code=200,
    )
    payload = LiquipediaParser().parse(page)

    tournaments = payload.rows.get("tournaments", [])
    assert any("International" in (t.get("name") or "") for t in tournaments)
    assert payload.discovered_jobs, "expected discovery of dota2 links"
