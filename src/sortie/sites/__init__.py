from sortie.sites.base import SiteAdapter
from sortie.sites.indeed import IndeedAdapter
from sortie.sites.instahyre import InstahyreAdapter
from sortie.sites.naukri import NaukriAdapter

# site name -> adapter class; filled in as adapters are built
ADAPTERS: dict[str, type[SiteAdapter]] = {
    NaukriAdapter.name: NaukriAdapter,
    InstahyreAdapter.name: InstahyreAdapter,
    IndeedAdapter.name: IndeedAdapter,
}
