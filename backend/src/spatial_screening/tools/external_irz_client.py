import requests

from backend.src.utilities.http import create_system_trust_session


class ExternalIRZClient:
    def __init__(self):
        self.base_url = (
            "https://environment.data.gov.uk/geoservices/datasets/"
            "eaaeb3e9-f6cf-42ef-be6e-21ae8d77462c"
            "/ogc/features/v1"
        )
        self.headers = {
            "Accept": "application/json"
        }
        self.session = create_system_trust_session()

    def _get(self, path, timeout, **kwargs):
        response = self.session.get(
            f"{self.base_url}/{path}", 
            headers=self.headers, 
            timeout=timeout,
            **kwargs
            )
        
        response.raise_for_status()
        return response.json()

    def _post(
        self,
        path: str,
        body: dict,
        timeout: int,
        **kwargs
        ) -> dict:

        response = self.session.post(
            f"{self.base_url}/{path}",
            json=body,
            headers=self.headers,
            timeout=timeout,
            **kwargs
        )

        response.raise_for_status()
        return response.json()

    def get_collections(self, timeout=30) -> dict:
        path = "collections"

        return self._get(path, timeout=timeout)

    def get_conformance(self, timeout=30) -> dict:
        return self._get("conformance", timeout=timeout)

    def get_collection_metadata(
            self,
            collection_id,
            timeout=30
            ) -> dict:
            path = f"collections/{collection_id}"

            return self._get(path, timeout=timeout)

    def get_features_by_bbox(
            self, 
            bbox, 
            collection_id, 
            limit=100, 
            timeout=30) -> dict:
        path = f"collections/{collection_id}/items"
        params={
                "bbox": bbox,
                "limit": limit
                }

        return self._get(
            path,
            params=params,
            timeout=timeout
            )

    def get_feature(
        self,
        collection_id: str,
        feature_id: str,
        timeout: int = 30
        ) -> dict:

        path = (
            f"collections/{collection_id}"
            f"/items/{feature_id}"
        )

        return self._get(
            path,
            timeout=timeout
        )

    def search_features(
        self,
        collection_id: str,
        payload: dict,
        timeout: int = 30
        ) -> dict:

        path = (
            f"collections/{collection_id}"
            f"/search"
        )

        return self._post(
            path,
            body=payload,
            timeout=timeout
        )