# Feature: Instahyre Adapter

**Status:** Planned · **Phase:** 1 · **Code:** `src/sortie/sites/instahyre.py`

## Flow
1. `ensure_logged_in` — open instahyre.com; pause for manual login if needed.
2. `search` — matched opportunities first, then search results for configured roles/locations.
3. `get_details` — read JD, company, experience, location.
4. `apply` — click "Interested" / "Apply"; answer any follow-up questions; confirm.

## Notes
- Few questions; mostly match-and-click.
- Selectors in one `SELECTORS` dict; confirmed against a live session during build.
