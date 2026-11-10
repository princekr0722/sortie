from sortie.sites.base import SiteAdapter
from sortie.sites.cutshort import CutshortAdapter
from sortie.sites.indeed import IndeedAdapter
from sortie.sites.instahyre import InstahyreAdapter
from sortie.sites.naukri import NaukriAdapter

# site name -> adapter class; only these can be run or logged in to
ADAPTERS: dict[str, type[SiteAdapter]] = {
    NaukriAdapter.name: NaukriAdapter,
    InstahyreAdapter.name: InstahyreAdapter,
    CutshortAdapter.name: CutshortAdapter,
}

# runnable only when named (`--sites cutshort`), never in "all sites", until verified live in review mode
EXPERIMENTAL: dict[str, str] = {}

# built but switched off: name -> (adapter, why). Not runnable until moved back into ADAPTERS.
# Tracked in doc/TRACKER.md (phase 2) and the site's feature doc.
DISABLED: dict[str, tuple[type[SiteAdapter], str]] = {
    IndeedAdapter.name: (IndeedAdapter, "Yes/No answers aren't registered by Indeed's form, and a CAPTCHA "
                                        "appears before every Submit; see doc/features/site-indeed.md"),
}
