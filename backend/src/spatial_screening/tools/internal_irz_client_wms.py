import requests
from bs4 import BeautifulSoup
from pyproj import Transformer

from backend.src.utilities.http import create_system_trust_session


class InternalIRZClientWMS:
    def __init__(self, feature_count=100):
        self.feature_count = feature_count
        self._WGS84_TO_BNG = Transformer.from_crs(
                            "EPSG:4326",
                            "EPSG:27700",
                            always_xy=True,
                        )
        self.base_url = (
                "https://irz.geodata.org.uk/cgi-bin/mapserv"
                "?map=/www/irz_live/maps/irz_internal_restricted.map"
                "&SERVICE=WMS"
                "&VERSION=1.3.0"
                "&REQUEST=GetFeatureInfo"
                "&LAYERS=irz-internal"
                "&QUERY_LAYERS=irz-internal"
                "&STYLES="
                "&CRS=EPSG:27700"
                "&WIDTH=1000"
                "&HEIGHT=1000"
                "&I=500"
                "&J=500"
                "&INFO_FORMAT=text/html"
                f"&FEATURE_COUNT={feature_count}"
            )

        self.session = create_system_trust_session()

    def _parse_url(self, response):
        soup = BeautifulSoup(response.text, "html.parser")

        for link in soup.find_all("a"):
            href = link.get("href")

            if href and "IRZ_internal/IRZ.php" in href:
                href = "".join(href.split())
                return href

        return None

    def _lonlat_to_bng(self, bbox):
        """Convert longitude/latitude to BNG easting and northing, in metres."""
        bng_bbox = ()
        n_pairs = len(bbox)//2
        for i in range(n_pairs):
            longitude = bbox[2 * i]
            latitude = bbox[2 * i + 1]
            easting, northing = self._WGS84_TO_BNG.transform(longitude, latitude)
            bng_bbox += (easting, northing)
        return bng_bbox

    def navigate(self, bbox):
        minx, miny, maxx, maxy = bbox
        url = self.base_url + f"&BBOX={minx},{miny},{maxx},{maxy}"

        response = self.session.get(url)
        response.raise_for_status()

        return response

    def get_internal_link(self, bbox):
        bng_bbox = self._lonlat_to_bng(bbox)
        response = self.navigate(bng_bbox)
        parsed_url = self._parse_url(response)

        if parsed_url:
            return parsed_url  
        else:
            raise ValueError("No internal link found in the response.")