# Feature: CutShort Adapter

**Status:** Planned · **Phase:** 1 · **Code:** `src/sortie/sites/cutshort.py`

## Flow
1. `ensure_logged_in` — open cutshort.io; pause for manual login if needed.
2. `search` — job search by role/location/experience; paginate.
3. `get_details` — read JD, skills, salary, location.
4. `apply` — click Apply; answer screening questions via the answerer; generate a short cover note with the LLM when asked; confirm.

## Notes
- Selectors in one `SELECTORS` dict; confirmed against a live session during build.
