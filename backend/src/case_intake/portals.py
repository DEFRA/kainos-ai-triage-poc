# FROM: https://github.com/Tay-dev-lab/vp-data-scraper/blob/main/planning_scraper/planning_scraper/config/portals.py

"""
Portal URL configuration for UK planning portals.

IDOX is the dominant framework (~55 councils).
URLs are organized by prefix/pattern for easier maintenance.
"""

from typing import Dict, List, Optional

# =============================================================================
# IDOX Portal URLs - PRODUCTION MODE
# =============================================================================

# URLs with "planning." prefix
IDOX_PLANNING = [
    # "https://planning.doncaster.gov.uk/online-applications/search.do?action=advanced",  # Already scraped
    "https://planning.fife.gov.uk/online/search.do?action=advanced",
    "https://planning.inverclyde.gov.uk/Online/search.do?action=advanced",
    "https://planning.brentwood.gov.uk/online-applications/search.do?action=advanced",
]

# URLs with "pa." prefix
IDOX_PA = [
    "https://pa.sevenoaks.gov.uk/online-applications/search.do?action=advanced",
    "https://pa.shropshire.gov.uk/online-applications/search.do?action=advanced",
]

# URLs with "planapp." prefix
IDOX_PLANAPP = [
    "https://planapp.knowsley.gov.uk/online-applications/search.do?action=advanced",
]

# URLs with "publicaccess." prefix
IDOX_PUBLICACCESS = [
    "https://publicaccess.braintree.gov.uk/online-applications/search.do?action=advanced",
    # "https://publicaccess.chichester.gov.uk/online-applications/search.do?action=advanced",  # Already scraped
    "https://publicaccess.cotswold.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.cravendc.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.darlington.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.dartford.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.dover.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.eastherts.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.e-lindsey.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.east-northamptonshire.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.exeter.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.fdean.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.gloucester.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.gosport.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.guildford.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.hart.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.hastings.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.huntingdonshire.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.iow.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.kingston.gov.uk/online-applications/search.do?action=advanced",
    # "https://publicaccess.leeds.gov.uk/online-applications/search.do?action=advanced",  # Already scraped
    "https://publicaccess.maldon.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.mendip.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.newark-sherwooddc.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.newcastle-staffs.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.northumberland.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.nottinghamcity.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.rushmoor.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.rutland.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.solihull.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.southribble.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.southsomerset.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.spelthorne.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.sthelens.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.stevenage.gov.uk/online-applications/search.do?action=advanced&searchType=Application",
    "https://publicaccess.stroud.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.surreyheath.gov.uk/online-applications/search.do?action=advanced",
    "https://publicaccess.clacks.gov.uk/publicaccess/search.do?action=advanced",
    "https://publicaccess.brentwood.gov.uk/online-applications/search.do?action=advanced",
]

# URLs with "planningpublicaccess." prefix
IDOX_PLANNING_PUBLICACCESS = [
    "https://planningpublicaccess.southdowns.gov.uk/online-applications/search.do?action=advanced",
    "https://planningpublicaccess.southampton.gov.uk/online-applications/search.do?action=advanced",
]

# URLs with special prefixes
IDOX_SPECIAL = [
    "https://www.eplanningcnpa.co.uk/online-applications/search.do?action=advanced",
    "https://idoxpa.westminster.gov.uk/online-applications/search.do?action=advanced",
    "https://development.towerhamlets.gov.uk/online-applications/search.do?action=advanced",
    "https://searchapplications.bromley.gov.uk/online-applications/search.do?action=advanced",
    "https://planning-applications.midlothian.gov.uk/OnlinePlanning/search.do?action=advanced",
    "https://boppa.poole.gov.uk/online-applications/search.do?action=advanced",
    "https://regs.thurrock.gov.uk/online-applications/search.do?action=advanced",
]

# Combined list of all active IDOX URLs
IDOX_URLS: List[str] = (
    IDOX_PLANNING
    + IDOX_PA
    + IDOX_PLANAPP
    + IDOX_PUBLICACCESS
    + IDOX_PLANNING_PUBLICACCESS
    + IDOX_SPECIAL
)


# =============================================================================
# LONDON BOROUGH URLS
# =============================================================================

LONDON_IDOX_URLS: Dict[str, str] = {
    "barnet": "https://publicaccess.barnet.gov.uk/online-applications/search.do?action=advanced",
    "bexley": "https://pa.bexley.gov.uk/online-applications/search.do?action=advanced",
    "brent": "https://pa.brent.gov.uk/online-applications/search.do?action=advanced",
    "bromley": "https://searchapplications.bromley.gov.uk/online-applications/search.do?action=advanced",
    "city_of_london": "https://www.planning2.cityoflondon.gov.uk/online-applications/search.do?action=advanced",
    "croydon": "https://publicaccess3.croydon.gov.uk/online-applications/search.do?action=advanced",
    "ealing": "https://pam.ealing.gov.uk/online-applications/search.do?action=advanced",
    "enfield": "https://planningandbuildingcontrol.enfield.gov.uk/online-applications/search.do?action=advanced",
    "greenwich": "https://planning.royalgreenwich.gov.uk/online-applications/search.do?action=advanced",
    "hammersmith_fulham": "https://public-access.lbhf.gov.uk/online-applications/search.do?action=advanced",
    "kingston": "https://publicaccess.kingston.gov.uk/online-applications/search.do?action=advanced",
    "lambeth": "https://planning.lambeth.gov.uk/online-applications/search.do?action=advanced",
    "lewisham": "https://planning.lewisham.gov.uk/online-applications/search.do?action=advanced",
    "newham": "https://pa.newham.gov.uk/online-applications/search.do?action=advanced",
    "southwark": "https://planning.southwark.gov.uk/online-applications/search.do?action=advanced",
    "sutton": "https://planningregister.sutton.gov.uk/online-applications/search.do?action=advanced",
    "tower_hamlets": "https://development.towerhamlets.gov.uk/online-applications/search.do?action=advanced",
    "westminster": "https://idoxpa.westminster.gov.uk/online-applications/search.do?action=advanced",
}

LONDON_ASPX_URLS: Dict[str, str] = {
    # Camden uses /NECSWS/ path (verified working with Playwright)
    "camden": "https://planningrecords.camden.gov.uk/NECSWS/PlanningExplorer/GeneralSearch.aspx",
    # Merton uses /Northgate/PlanningExplorerAA/ path (verified 200 response)
    "merton": "https://planning.merton.gov.uk/Northgate/PlanningExplorerAA/GeneralSearch.aspx",
    # Wandsworth uses /Northgate/PlanningExplorer/ path
    "wandsworth": "https://planning.wandsworth.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
    # Note: Hackney uses Council Direct framework (not ASPX) - requires custom spider
    # Note: Islington uses Agile API, not ASPX - excluded from this list
}

# =============================================================================
# LONDON OCELLA URLS
# =============================================================================

LONDON_OCELLA_URLS: Dict[str, str] = {
    "havering": "https://development.havering.gov.uk/OcellaWeb/planningSearch",
    "hillingdon": "https://planning.hillingdon.gov.uk/OcellaWeb/planningSearch",
}

# =============================================================================
# LONDON AGILE URLS
# =============================================================================
# Agile Applications uses a REST API - no web scraping needed
# API endpoint: https://planningapi.agileapplications.co.uk/api/application/search
# Each council has: url (portal URL) and x_client (API client identifier)
# Note: x_client may differ from council name (e.g., Islington uses "IS")

LONDON_AGILE_URLS: Dict[str, Dict[str, str]] = {
    # Redbridge uses Agile with custom domain
    "redbridge": {
        "url": "https://planning.redbridge.gov.uk/",
        "x_client": "REDBRIDGE",
    },
    # Islington uses x-client "IS" (not "ISLINGTON")
    "islington": {
        "url": "https://planning.agileapplications.co.uk/islington/",
        "x_client": "IS",
    },
    # Richmond upon Thames uses Agile with custom domain
    "richmond": {
        "url": "https://www2.richmond.gov.uk/lbrplanning/",
        "x_client": "RICHMOND",
    },
}

# =============================================================================
# LONDON ATLAS URLS
# =============================================================================
# Atlas is a custom SolidStart-based portal used by RBKC (Kensington & Chelsea).
# It wraps an Idox Uniform backend with a modern SPA frontend.
# Uses server functions with seroval serialization for data fetching.

LONDON_ATLAS_URLS: Dict[str, Dict[str, str]] = {
    # RBKC - Royal Borough of Kensington and Chelsea
    # Note: Legacy ASPX system offline due to cyber incident since Nov 2025
    "rbkc": {
        "url": "https://atlas.rbkc.gov.uk/planningsearch/",
        "server_endpoint": "https://atlas.rbkc.gov.uk/planningsearch/_server/",
    },
}

# =============================================================================
# LONDON FA_SEARCH (Tascomi) URLS
# =============================================================================
# FA_SEARCH is a Tascomi-based portal used by several London boroughs.
# These portals use WAF token protection and require Playwright for token extraction.
# API endpoints: /fastweb/results.aspx, /fastweb/detail.aspx

LONDON_FA_SEARCH_URLS: Dict[str, Dict[str, str]] = {
    # Barking and Dagenham
    "barking": {
        "url": "https://pa.lbbd.gov.uk/online-applications/",
        "search_url": "https://pa.lbbd.gov.uk/online-applications/search.do",
    },
    # Hackney
    "hackney": {
        "url": "https://planning.hackney.gov.uk/planning/",
        "search_url": "https://planning.hackney.gov.uk/planning/search.do",
    },
    # Harrow
    "harrow": {
        "url": "https://planningsearch.harrow.gov.uk/fastweb/",
        "search_url": "https://planningsearch.harrow.gov.uk/fastweb/",
    },
    # Waltham Forest
    "waltham_forest": {
        "url": "https://planning.walthamforest.gov.uk/planning/",
        "search_url": "https://planning.walthamforest.gov.uk/planning/search.do",
    },
}

# =============================================================================
# LONDON ARCUS (Salesforce) URLS
# =============================================================================
# ARCUS is a Salesforce-based portal using Lightning Web Components (LWC).
# Uses Aura API with JSON-RPC style requests. Requires fwuid context from initial page load.

LONDON_ARCUS_URLS: Dict[str, Dict[str, str]] = {
    # Haringey
    "haringey": {
        "url": "https://planningservices.haringey.gov.uk/",
        "aura_endpoint": "https://planningservices.haringey.gov.uk/s/sfsites/aura",
        "register_name": "Arcus_BE_Public_Register",
    },
}


def get_active_idox_urls(region: Optional[str] = None, council: Optional[str] = None) -> List[str]:
    """
    Return IDOX URLs for scraping.

    Args:
        region: Optional region filter. If 'london', returns only London borough URLs.
        council: Optional council name to scrape a single council.

    Returns:
        List of IDOX portal URLs.
    """
    # Single council mode
    if council:
        council_lower = council.lower().replace("-", "_").replace(" ", "_")
        # Check London IDOX URLs first
        if council_lower in LONDON_IDOX_URLS:
            return [LONDON_IDOX_URLS[council_lower]]
        # Check all IDOX URLs for matching council name
        for url in IDOX_URLS:
            if council_lower in url.lower():
                return [url]
        # Not found
        raise ValueError(
            f"Council '{council}' not found in IDOX portals. "
            f"Available London councils: {list(LONDON_IDOX_URLS.keys())}"
        )

    if region == "london":
        return list(LONDON_IDOX_URLS.values())
    return IDOX_URLS.copy()


def get_london_idox_urls() -> List[str]:
    """Return list of London borough IDOX URLs."""
    return list(LONDON_IDOX_URLS.values())


def get_london_aspx_urls() -> List[str]:
    """Return list of London borough ASPX URLs."""
    return list(LONDON_ASPX_URLS.values())


def get_aspx_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, str]:
    """
    Return ASPX URLs for scraping.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> URL mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_ASPX_URLS:
            return {council: LONDON_ASPX_URLS[council]}
        # Could extend to non-London ASPX councils here
        return {}

    if region == "london":
        return LONDON_ASPX_URLS.copy()

    # Return all ASPX URLs (currently just London)
    return LONDON_ASPX_URLS.copy()


def get_ocella_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, str]:
    """
    Return OCELLA URLs for scraping.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> URL mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_OCELLA_URLS:
            return {council: LONDON_OCELLA_URLS[council]}
        return {}

    if region == "london":
        return LONDON_OCELLA_URLS.copy()

    # Return all OCELLA URLs (currently just London)
    return LONDON_OCELLA_URLS.copy()


def get_agile_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    Return Agile Applications URLs for scraping.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> {url, x_client} mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_AGILE_URLS:
            return {council: LONDON_AGILE_URLS[council]}
        return {}

    if region == "london":
        return LONDON_AGILE_URLS.copy()

    # Return all Agile URLs (currently just London)
    return LONDON_AGILE_URLS.copy()


def get_atlas_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    Return Atlas portal URLs for scraping.

    Atlas is a SolidStart-based SPA used by RBKC (Kensington & Chelsea).

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> {url, server_endpoint} mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_ATLAS_URLS:
            return {council: LONDON_ATLAS_URLS[council]}
        return {}

    if region == "london":
        return LONDON_ATLAS_URLS.copy()

    # Return all Atlas URLs (currently just RBKC)
    return LONDON_ATLAS_URLS.copy()


def get_fa_search_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    Return FA_SEARCH (Tascomi) portal URLs for scraping.

    FA_SEARCH portals use WAF token protection and require special handling.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> {url, search_url} mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_FA_SEARCH_URLS:
            return {council: LONDON_FA_SEARCH_URLS[council]}
        return {}

    if region == "london":
        return LONDON_FA_SEARCH_URLS.copy()

    # Return all FA_SEARCH URLs (currently just London)
    return LONDON_FA_SEARCH_URLS.copy()


def get_arcus_urls(region: Optional[str] = None, council: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    Return ARCUS (Salesforce) portal URLs for scraping.

    ARCUS portals use Salesforce Aura API with Lightning Web Components.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> {url, aura_endpoint, register_name} mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_ARCUS_URLS:
            return {council: LONDON_ARCUS_URLS[council]}
        return {}

    if region == "london":
        return LONDON_ARCUS_URLS.copy()

    # Return all ARCUS URLs (currently just Haringey)
    return LONDON_ARCUS_URLS.copy()


# =============================================================================
# Already Scraped URLs (for reference)
# =============================================================================
# IDOX_ALREADY_SCRAPED = [
#     "https://planning.doncaster.gov.uk/online-applications/search.do?action=advanced",
#     "https://publicaccess.chichester.gov.uk/online-applications/search.do?action=advanced",
#     "https://publicaccess.leeds.gov.uk/online-applications/search.do?action=advanced",
# ]


# =============================================================================
# Other Framework URLs (for future phases)
# =============================================================================

# Agile Applications URLs
AGILE_URLS = [
    "https://planning.agileapplications.co.uk/cannock/search-applications/",
    "https://planning.agileapplications.co.uk/exmoor/search-applications/",
    "https://planning.agileapplications.co.uk/ldnpa/search-applications/",
    "https://planning.agileapplications.co.uk/middlesbrough/search-applications/",
    "https://planning.agileapplications.co.uk/mole/search-applications/",
    "https://planning.agileapplications.co.uk/nfnpa/search-applications/",
    "https://planning.agileapplications.co.uk/rugby/search-applications/",
    "https://planning.agileapplications.co.uk/slough/search-applications/",
    "https://planning.agileapplications.co.uk/snowdonia/search-applications/",
    "https://planning.agileapplications.co.uk/tmbc/search-applications/",
    "https://planning.agileapplications.co.uk/pembrokeshire/search-applications/",
    "https://planning.agileapplications.co.uk/flintshire/search-applications/",
    "https://planning.agileapplications.co.uk/islington/search-applications/",
    "https://planning.agileapplications.co.uk/pcnpa/search-applications/",
    "https://planning.agileapplications.co.uk/yorkshiredales/search-applications/",
]

# ASPX/Northgate URLs
ASPX_URLS = [
    "https://planning.blackburn.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
    "https://planningrecords.camden.gov.uk/NECSWS/PlanningExplorer/GeneralSearch.aspx",
    "https://www.eaststaffsbc.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
    "https://planning.merton.gov.uk/Northgate/PlanningExplorerAA/GeneralSearch.aspx",
    "https://planning.tamworth.gov.uk/northgate/planningexplorer/generalsearch.aspx",
    "https://planning.wandsworth.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
]

# OCELLA URLs
OCELLA_URLS = [
    "https://development.havering.gov.uk/OcellaWeb/planningSearch",
]

# ARCUS/Salesforce URLs
ARCUS_URLS = [
    "https://eppingforestdc.my.site.com/pr/s/sfsites/aura",
]

# =============================================================================
# LONDON NECSWS ES/Presentation URLS
# =============================================================================
# NECSWS ES/Presentation is a planning portal framework used by several UK councils.
# It differs from standard ASPX/Northgate with different URL patterns and form elements.
# Requires Playwright for JavaScript-heavy page handling.

LONDON_NECSWS_URLS: Dict[str, Dict[str, str]] = {
    # Hounslow - London Borough
    "hounslow": {
        "url": "https://planningandbuilding.hounslow.gov.uk/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch",
        "search_url": "https://planningandbuilding.hounslow.gov.uk/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch",
        "base_url": "https://planningandbuilding.hounslow.gov.uk",
    },
}


def get_necsws_urls(
    region: Optional[str] = None, council: Optional[str] = None
) -> Dict[str, Dict[str, str]]:
    """
    Return NECSWS ES/Presentation portal URLs for scraping.

    NECSWS portals require Playwright for JavaScript handling.

    Args:
        region: Optional region filter. If 'london', returns London borough URLs.
        council: Optional single council name to scrape.

    Returns:
        Dict of council_name -> {url, search_url, base_url} mappings.
    """
    if council:
        # Single council mode
        if council in LONDON_NECSWS_URLS:
            return {council: LONDON_NECSWS_URLS[council]}
        return {}

    if region == "london":
        return LONDON_NECSWS_URLS.copy()

    # Return all NECSWS URLs (currently just Hounslow)
    return LONDON_NECSWS_URLS.copy()
