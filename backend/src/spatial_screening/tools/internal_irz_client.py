import json
import math
from pathlib import Path
from typing import Optional, Union

import requests
import yaml

from backend.src.utilities.http import create_system_trust_session


_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[4]
    / "environment"
    / "configs"
    / "spatial_screening.yaml"
)


def _load_request_timeout(config_path: Union[str, Path]) -> float:
    config_path = Path(config_path)
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid spatial-screening YAML in {config_path}: {error}") from error

    if not isinstance(config, dict) or not isinstance(config.get("internal_irz"), dict):
        raise ValueError(
            f"Spatial-screening config {config_path} must contain an 'internal_irz' mapping."
        )
    settings = config["internal_irz"]
    if settings.keys() - {"request_timeout_seconds"}:
        raise ValueError(
            "Internal IRZ settings may only contain 'request_timeout_seconds'."
        )

    timeout = settings.get("request_timeout_seconds")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("request_timeout_seconds must be a positive finite number.")
    return float(timeout)


class InternalIRZClient:
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        config_path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH
        self.timeout_seconds = _load_request_timeout(config_path)
        self.url = "https://irz.geodata.org.uk/poly_search_internal/"
        self.session = create_system_trust_session()

    @staticmethod
    def _validate_bng_polygon(polygon):
        """Require a ring of plausible British National Grid coordinates."""
        if not isinstance(polygon, (list, tuple)) or not polygon:
            raise ValueError("polygon must be a non-empty BNG coordinate ring.")

        for coordinate in polygon:
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
                raise ValueError(
                    "Each polygon coordinate must be an [easting, northing] pair."
                )
            easting, northing = coordinate
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (easting, northing)
            ):
                raise ValueError("BNG coordinates must be finite numbers in metres.")
            if -180 <= easting <= 180 and -90 <= northing <= 90:
                raise ValueError(
                    "Expected BNG easting/northing coordinates in metres, "
                    "but received values that look like longitude/latitude."
                )
            if not (0 <= easting <= 700_000 and 0 <= northing <= 1_300_000):
                raise ValueError(
                    "BNG coordinates must fall within the plausible UK grid extent "
                    "(easting 0-700000, northing 0-1300000 metres)."
                )

    def _build_payload(self, bng_polygon):
        geojson = {
            "type": "Polygon",
            "coordinates": [bng_polygon],
        }

        return {
            # API expects JSON encoded as a string
            "data": json.dumps(json.dumps(geojson)),
            "type": "GeoJSON",
        }

    def get_internal_link(self, bng_polygon):
        """Find the IRZ record for a ring of BNG [easting, northing] points."""
        self._validate_bng_polygon(bng_polygon)
        payload = self._build_payload(bng_polygon)

        response = self.session.post(
            self.url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data=payload,
            timeout=self.timeout_seconds,
        )

        response.raise_for_status()

        result = response.json()

        link = result.get("link")

        if not link:
            raise ValueError(
                f"No link returned. Response: {result}"
            )

        return link