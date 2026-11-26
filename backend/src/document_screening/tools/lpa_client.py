"""Sequential client for Idox Public Access application pages and documents."""

import ipaddress
import json
import logging
import math
import os
import re
import socket
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Optional, Union
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
import yaml

from backend.src.utilities.http import create_system_trust_session


_LOGGER = logging.getLogger(__name__)
_DEFAULT_CONFIG_PATH = (
	Path(__file__).resolve().parents[4] / "environment" / "configs" / "document_screening.yaml"
)
_LPA_ENVIRONMENT_KEYS = {
	"request_interval_seconds": "LPA_REQUEST_INTERVAL_SECONDS",
	"retry_increment_seconds": "LPA_RETRY_INCREMENT_SECONDS",
	"max_retries": "LPA_MAX_RETRIES",
	"max_wait_seconds": "LPA_MAX_WAIT_SECONDS",
	"refresh_session_on_429": "LPA_REFRESH_SESSION_ON_429",
	"session_refresh_wait_seconds": "LPA_SESSION_REFRESH_WAIT_SECONDS",
	"max_session_refreshes": "LPA_MAX_SESSION_REFRESHES",
}


def _load_lpa_settings(config_path: Optional[Union[str, Path]] = None) -> dict:
	config_path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH
	try:
		with config_path.open(encoding="utf-8") as config_file:
			config = yaml.safe_load(config_file)
	except yaml.YAMLError as error:
		raise ValueError(f"Invalid document-screening YAML in {config_path}: {error}") from error
	if not isinstance(config, dict) or not isinstance(config.get("lpa", {}), dict):
		raise ValueError(f"Document-screening config {config_path} must contain an 'lpa' mapping.")
	return config.get("lpa", {})


class PublicAccessClient:
	def __init__(
		self,
		base_url: str,
		timeout=30,
		user_agent: str = "PlanningSearchClient/0.1",
		*,
		lpa_settings: Optional[dict] = None,
	):
		self.base_url = base_url.rstrip("/") + "/"
		base = urlsplit(self.base_url)
		if base.scheme not in ("http", "https") or not base.netloc:
			raise ValueError("base_url must be an absolute HTTP(S) URL.")
		if base.username or base.password or base.query or base.fragment:
			raise ValueError("base_url must not contain credentials, a query, or a fragment.")
		self.timeout = timeout
		self.user_agent = user_agent
		settings = dict(_load_lpa_settings() if lpa_settings is None else lpa_settings)
		for setting, environment_key in _LPA_ENVIRONMENT_KEYS.items():
			if environment_key in os.environ:
				settings[setting] = os.environ[environment_key]
		self._lpa_settings = settings.copy()
		interval = settings.get("request_interval_seconds", "0")
		self._request_interval = (
			0.0 if interval is None or str(interval).strip().lower() == "none"
			else float(interval)
		)
		self._base_request_interval = self._request_interval
		self._retry_increment = float(settings.get("retry_increment_seconds", "3"))
		self._max_retries = int(settings.get("max_retries", "3"))
		self._max_wait = float(settings.get("max_wait_seconds", "60"))
		self._session_refresh_wait = float(settings.get("session_refresh_wait_seconds", "5"))
		self._max_session_refreshes = int(settings.get("max_session_refreshes", "3"))
		refresh = settings.get("refresh_session_on_429", False)
		if isinstance(refresh, bool):
			self._refresh_session_on_429 = refresh
		else:
			refresh = str(refresh).strip().lower()
			if refresh not in {"true", "false"}:
				raise ValueError("refresh_session_on_429 must be true or false.")
			self._refresh_session_on_429 = refresh == "true"
		if (
			not all(math.isfinite(value) for value in (
				self._request_interval, self._retry_increment, self._max_wait,
				self._session_refresh_wait,
			))
			or self._request_interval < 0 or self._retry_increment < 0
			or self._max_retries < 0 or self._max_wait <= 0
			or self._request_interval > self._max_wait
			or self._session_refresh_wait < 0 or self._session_refresh_wait > self._max_wait
			or self._max_session_refreshes < 0
		):
			raise ValueError("Invalid LPA pacing settings: waits must be finite, non-negative and within the positive wait limit; increments and retry counts must be non-negative.")
		self._has_requested = False
		self._origin = f"{base.scheme}://{base.netloc}"
		self.session = self._new_session()

	def _new_session(self):
		session = create_system_trust_session()
		session.headers.update({
			"User-Agent": self.user_agent,
			"Accept": "text/html,application/xhtml+xml",
		})
		return session

	def _check_same_host(self, url):
		target = urlsplit(url)
		if (target.scheme not in ("http", "https")
				or target.netloc.lower() != urlsplit(self.base_url).netloc.lower()
				or target.username or target.password):
			raise ValueError("Refusing a URL outside the configured Public Access host.")
		return url

	def _check_map_url(self, url):
		target = urlsplit(url)
		if target.netloc.lower() == urlsplit(self.base_url).netloc.lower():
			return self._check_same_host(url)
		if (target.scheme != "https" or not target.hostname
				or target.username or target.password or target.port not in (None, 443)):
			raise ValueError("External map URLs must use public HTTPS without credentials.")
		try:
			addresses = socket.getaddrinfo(target.hostname, 443, type=socket.SOCK_STREAM)
		except socket.gaierror as error:
			raise ValueError(f"Cannot resolve external map host {target.hostname!r}.") from error
		if not addresses or any(
			not ipaddress.ip_address(address[4][0]).is_global
			or ipaddress.ip_address(address[4][0]).is_multicast
			for address in addresses
		):
			raise ValueError("External map URLs must resolve only to public addresses.")
		return url

	def _rate_limit_wait(self, response, *, refresh=False):
		retry_after = response.headers.get("Retry-After")
		if retry_after is not None:
			try:
				if retry_after.strip().isdigit():
					wait = float(retry_after)
				else:
					retry_at = parsedate_to_datetime(retry_after)
					if retry_at.tzinfo is None:
						retry_at = retry_at.replace(tzinfo=timezone.utc)
					wait = max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
				if not math.isfinite(wait):
					raise ValueError("Non-finite Retry-After")
			except (ValueError, TypeError, OverflowError):
				warnings.warn("Invalid Retry-After header; using configured LPA wait.", RuntimeWarning)
				retry_after = None
		if retry_after is None:
			wait = self._session_refresh_wait if refresh else self._request_interval + self._retry_increment
		elif refresh:
			wait = max(wait, self._session_refresh_wait)
		if wait > self._max_wait:
			raise requests.HTTPError(
				f"HTTP 429 from {response.url}: required wait {wait:g}s exceeds "
				f"LPA_MAX_WAIT_SECONDS ({self._max_wait:g}s).",
				response=response,
			)
		if retry_after is None and not refresh:
			self._request_interval = wait
		return wait

	def _refresh_rate_limited_session(self, response):
		wait = self._rate_limit_wait(response, refresh=True)
		_LOGGER.warning("HTTP 429; waiting %gs before refreshing the LPA session.", wait)
		if wait:
			sleep(wait)
		response.close()
		self.session.close()
		self.session = self._new_session()
		self._request_interval = self._base_request_interval
		self._has_requested = False

	def _request(self, method, url, *, document_download=False, map_request=False, **kwargs):
		check_url = self._check_map_url if map_request else self._check_same_host
		map_host = urlsplit(url).netloc.lower()
		session_refreshes = 0
		for _redirect in range(self.session.max_redirects + 1):
			check_url(url)
			external_map = map_request and map_host != urlsplit(self.base_url).netloc.lower()
			if external_map and method != "GET":
				raise ValueError("External map requests must use GET.")
			wait = self._request_interval if self._has_requested else 0
			attempt = 0
			while True:
				if wait:
					sleep(wait)
				self._has_requested = True
				try:
					if external_map:
						with self._new_session() as map_session:
							response = map_session.request(
								method, url, timeout=self.timeout, allow_redirects=False, **kwargs
							)
					else:
						response = self.session.request(
							method, url, timeout=self.timeout, allow_redirects=False, **kwargs
						)
				except requests.exceptions.SSLError:
					raise
				except (requests.ConnectionError, requests.Timeout) as error:
					if attempt >= self._max_retries:
						raise
					attempt += 1
					wait = self._retry_increment * attempt
					if wait > self._max_wait:
						raise
					_LOGGER.warning(
						"LPA request failed with %s; retrying attempt %s/%s in %gs.",
						type(error).__name__, attempt, self._max_retries, wait,
					)
					continue
				if response.status_code in (500, 502, 503, 504):
					if attempt >= self._max_retries:
						break
					attempt += 1
					wait = self._retry_increment * attempt
					if wait > self._max_wait:
						break
					_LOGGER.warning(
						"LPA returned HTTP %s; retrying attempt %s/%s in %gs.",
						response.status_code, attempt, self._max_retries, wait,
					)
					response.close()
					continue
				if response.status_code == 429 and self._refresh_session_on_429 and not document_download and not external_map:
					if session_refreshes >= self._max_session_refreshes:
						raise requests.HTTPError(
							f"HTTP 429 persists after {session_refreshes} session refreshes.",
							response=response,
						)
					self._refresh_rate_limited_session(response)
					session_refreshes += 1
					attempt = 0
					wait = 0
					continue
				if (
					response.status_code != 429 or attempt == self._max_retries
					or (document_download and self._refresh_session_on_429)
				):
					break
				wait = self._rate_limit_wait(response)
				response.close()
				attempt += 1
			response.raise_for_status()
			check_url(response.url)
			if not response.is_redirect:
				return response
			url = urljoin(response.url, response.headers["Location"])
			if map_request and urlsplit(url).netloc.lower() != map_host:
				response.close()
				raise ValueError("Refusing a map redirect to a different host.")
			check_url(url)
			if response.status_code == 303 or (
				response.status_code in (301, 302) and method == "POST"
			):
				method = "GET"
				kwargs.pop("data", None)
			response.close()
		raise RuntimeError("Too many redirects from the Public Access site.")

	def _fetch_search_form(self):
		response = self._request("GET", self.base_url)
		soup = BeautifulSoup(response.content, "html.parser")
		form = soup.select_one("form#simpleSearchForm")
		if form is None or form.get("method", "get").lower() != "post":
			title = soup.title.get_text(" ", strip=True) if soup.title else "(none)"
			raise RuntimeError(f"Expected a POST Simple Search form; page title: {title!r}.")
		return form, response.url

	def _submit_search(self, reference):
		form, page_url = self._fetch_search_form()
		search_url = self._check_same_host(urljoin(page_url, form.get("action") or page_url))
		payload = [
			(field["name"], field.get("value", ""))
			for field in form.select('input[type="hidden"][name]')
			if field["name"] not in {
				"searchType", "searchCriteria.caseStatus",
				"searchCriteria.simpleSearchString", "searchCriteria.simpleSearch",
			}
		]
		payload.extend([
			("searchType", "Application"),
			("searchCriteria.caseStatus", ""),
			("searchCriteria.simpleSearchString", reference),
			("searchCriteria.simpleSearch", "true"),
		])
		response = self._request("POST", search_url, data=payload, headers={
			"Referer": page_url, "Origin": self._origin,
		})
		soup = BeautifulSoup(response.content, "html.parser")
		labels = self._parse_label_values(soup)
		returned_reference = next(
			(value for label, value in labels.items()
			 if label.rstrip(":").strip().casefold() == "reference"), None
		)
		tabs = self._parse_tabs(soup, response.url)
		keys = {
			parse_qs(urlsplit(tab["url"]).query)["keyVal"][0]
			for tab in tabs
		}
		if (returned_reference != reference.strip() or len(keys) != 1
				or not any(tab["active_tab"] == "summary" for tab in tabs)):
			title = soup.title.get_text(" ", strip=True) if soup.title else "(none)"
			raise RuntimeError(
				f"Expected the application summary for {reference!r}; "
				f"page title: {title!r}. Search-results lists are not supported."
			)
		return keys.pop(), soup, tabs

	def _parse_tabs(self, soup, page_url):
		tabs = []
		seen = set()
		for anchor in soup.select("a[href]"):
			url = urljoin(page_url, anchor["href"])
			parsed = urlsplit(url)
			query = parse_qs(parsed.query)
			if not parsed.path.endswith("applicationDetails.do"):
				continue
			if "activeTab" not in query or "keyVal" not in query:
				continue
			self._check_same_host(url)
			active_tab = query["activeTab"][0]
			if url in seen:
				continue
			seen.add(url)
			name = " ".join(anchor.get_text(" ", strip=True).split())
			count_match = re.search(r"\s*\(\s*(\d+)\s*\)\s*$", name)
			count = int(count_match.group(1)) if count_match else None
			if count_match:
				name = name[:count_match.start()].strip()
			tabs.append({
				"name": name or active_tab,
				"active_tab": active_tab,
				"count": count,
				"url": url,
			})
		return tabs

	def _parse_label_values(self, soup):
		values = {}
		for row in soup.select("tr"):
			cells = row.find_all(["th", "td"], recursive=False)
			if len(cells) != 2 or cells[1].name != "td":
				continue
			label = " ".join(cells[0].get_text(" ", strip=True).split())
			if label:
				values[label] = " ".join(cells[1].get_text(" ", strip=True).split())
		return values

	def _get_tab(self, keyval, active_tab):
		tab_url = urljoin(self.base_url, "applicationDetails.do")
		response = self._request(
			"GET",
			tab_url,
			params={"activeTab": active_tab, "keyVal": keyval},
		)
		soup = BeautifulSoup(response.content, "html.parser")
		page_tabs = self._parse_tabs(soup, response.url)
		if not any(
			page_tab["active_tab"] == active_tab
			and parse_qs(urlsplit(page_tab["url"]).query)["keyVal"][0] == keyval
			for page_tab in page_tabs
		):
			title = soup.title.get_text(" ", strip=True) if soup.title else "(none)"
			raise RuntimeError(f"Expected application tab {active_tab!r}; page title: {title!r}.")
		return soup, response.url

	def _parse_documents(self, soup, page_url):
		documents = []
		found_table = False
		field_headers = {
			"published": "date published", "type": "document type",
			"drawing_number": "drawing number", "description": "description",
		}
		for table in soup.select("table"):
			header_row = next((row for row in table.select("tr") if row.find("th")), None)
			if header_row is None:
				continue
			headers = [
				" ".join(cell.get_text(" ", strip=True).split()).casefold()
				for cell in header_row.find_all(["th", "td"], recursive=False)
			]
			if not ({"date published", "document type"} & set(headers)) and not table.select_one('a[href*="files/"]'):
				continue
			found_table = True
			for row in table.select("tr"):
				cells = row.find_all("td", recursive=False)
				anchor = row.select_one('a[href*="files/"]')
				if not cells or anchor is None:
					continue
				url = self._check_same_host(urljoin(page_url, anchor["href"]))
				match = re.search(r"-(\d+)\.[^.\/]+$", urlsplit(url).path)
				if match is None:
					match = re.search(r"\bID\s+(\d+)\b", anchor.get("title", ""), re.I)
				if match is None:
					raise RuntimeError("Cannot identify a document ID on the documents page.")
				row_values = {
					header: " ".join(cell.get_text(" ", strip=True).split())
					for header, cell in zip(headers, cells)
				}
				documents.append({
					"id": match.group(1),
					**{field: row_values.get(header) for field, header in field_headers.items()},
					"url": url,
				})
		if not found_table:
			raise RuntimeError("Expected a documents table with column headers.")
		return documents

	def _document_page(self, keyval):
		soup, page_url = self._get_tab(keyval, "documents")
		return self._parse_documents(soup, page_url), page_url

	def _coordinates_from_pos_list(self, pos_list):
		dimension = int(pos_list.get("srsDimension", "2"))
		values = [float(value) for value in (pos_list.text or "").split()]
		if dimension != 2 or not values or len(values) % 2:
			raise ValueError("Expected non-empty 2D easting/northing coordinates.")	
		return [
			[values[index], values[index + 1]]
			for index in range(0, len(values), 2)
		]

	def _arcgis_application_polygon_layer(self, keyval):
		map_page, page_url = self._get_tab(keyval, "map")
		map_form = map_page.select_one("form#mapForm")
		if map_form is not None:
			map_app_url = self._check_same_host(
				urljoin(page_url, map_form.get("action") or page_url)
			)
			form_values = [
				(field["name"], field.get("value", ""))
				for field in map_form.select("input[name]")
			]
			form_keyvals = dict(form_values).get("keyVals")
			if form_keyvals != keyval:
				raise RuntimeError("The application map form has a different application key.")
			self._request(
				"POST",
				map_app_url,
				data=form_values,
				headers={"Origin": self._origin, "Referer": page_url},
			)
		else:
			map_frame = map_page.select_one("iframe[src]")
			if map_frame is None:
				raise RuntimeError("The application map page has no embedded map form or URL.")
			map_app_url = self._check_same_host(
				urljoin(page_url, map_frame["src"])
			)
		app_item_id = parse_qs(urlsplit(map_app_url).query).get("id", [None])[0]
		if not app_item_id:
			raise RuntimeError("The embedded map URL has no ArcGIS application item ID.")

		app_parts = urlsplit(map_app_url)
		if "/apps/" not in app_parts.path:
			raise RuntimeError("The embedded map URL has no ArcGIS portal path.")
		portal_url = f"{app_parts.scheme}://{app_parts.netloc}{app_parts.path.split('/apps/', 1)[0]}"
		app_data_url = f"{portal_url}/sharing/rest/content/items/{app_item_id}/data"
		app_data = self._request(
			"GET",
			app_data_url,
			params={"f": "json"},
			headers={"Referer": map_app_url},
		).json()
		if not isinstance(app_data, dict):
			raise RuntimeError("The embedded map returned invalid application configuration.")
		if isinstance(app_data.get("dataSources"), dict):
			web_map = next(
				(
					source for source in app_data["dataSources"].values()
					if isinstance(source, dict)
					if source.get("type") == "WEB_MAP" and source.get("itemId")
				),
				None,
			)
		else:
			web_map = app_data.get("map")
		if not isinstance(web_map, dict) or not web_map.get("itemId"):
			raise RuntimeError("The embedded map has no supported configured web map.")

		portal_url = self._check_map_url(web_map.get("portalUrl") or portal_url)
		web_map_data_url = (
			f"{portal_url.rstrip('/')}/sharing/rest/content/items/"
			f"{web_map['itemId']}/data"
		)
		web_map_data = self._request(
			"GET",
			web_map_data_url,
			params={"f": "json"},
			headers={"Referer": map_app_url},
			map_request=True,
		).json()
		if not isinstance(web_map_data, dict) or not isinstance(
			web_map_data.get("operationalLayers"), list
		):
			raise RuntimeError("The embedded map returned invalid web-map configuration.")
		polygon_layer = next(
			(
				layer for layer in web_map_data["operationalLayers"]
				if isinstance(layer, dict)
				if "application polygons" in layer.get("title", "").casefold()
				and layer.get("url")
			),
			None,
		)
		if polygon_layer is None:
			raise RuntimeError("The embedded web map has no application polygon layer.")

		layer_url = self._check_map_url(polygon_layer["url"])
		if not re.search(r"/FeatureServer/\d+/?$", urlsplit(layer_url).path):
			raise RuntimeError("The application polygon layer is not an ArcGIS feature layer.")
		return layer_url, map_app_url

	def _arcgis_coordinates(self, layer_url, keyval, reference, referer=None):
		query_url = layer_url.rstrip("/") + "/query"
		escaped_keyval = keyval.replace("'", "''")
		headers = {"Referer": referer} if referer is not None else None
		response = self._request(
			"GET",
			query_url,
			params={
				"where": f"ISPAVISIBLE = 1 AND KEYVAL = '{escaped_keyval}'",
				"outFields": "*",
				"returnGeometry": "true",
				"outSR": "27700",
				"f": "json",
			},
			headers=headers,
			map_request=True,
		)
		result = response.json()
		if not isinstance(result, dict):
			raise RuntimeError("ArcGIS polygon query returned an invalid response.")
		if result.get("error"):
			raise RuntimeError(f"ArcGIS polygon query failed: {result['error']}.")
		features = result.get("features")
		if not isinstance(features, list):
			raise RuntimeError("ArcGIS polygon query did not return a feature list.")

		rings = []
		for feature in features:
			if not isinstance(feature, dict):
				continue
			attributes = feature.get("attributes", {})
			if not isinstance(attributes, dict):
				continue
			if attributes.get("KEYVAL") not in (None, keyval):
				continue
			if reference and attributes.get("REFVAL") not in (None, reference):
				continue
			geometry = feature.get("geometry", {})
			if not isinstance(geometry, dict):
				continue
			feature_rings = geometry.get("rings", [])
			if isinstance(feature_rings, list):
				rings.extend(
					ring for ring in feature_rings
					if isinstance(ring, list) and len(ring) >= 4
					and all(
						isinstance(point, list) and len(point) >= 2
						and type(point[0]) in {int, float}
						and type(point[1]) in {int, float}
						and math.isfinite(point[0]) and math.isfinite(point[1])
						for point in ring
					)
				)
		if not rings:
			return []

		if len(rings) > 1:
			warnings.warn(
				f"ArcGIS returned {len(rings)} BNG rings for keyval {keyval!r}; "
				"using the largest exterior ring.",
				RuntimeWarning,
				stacklevel=2,
			)
		exterior_rings = [
			ring for ring in rings if self._signed_ring_area(ring) < 0
		]
		candidates = exterior_rings or rings
		return [max(candidates, key=lambda ring: abs(self._signed_ring_area(ring)))]

	@staticmethod
	def _signed_ring_area(ring):
		return sum(
			ring[index][0] * ring[(index + 1) % len(ring)][1]
			- ring[(index + 1) % len(ring)][0] * ring[index][1]
			for index in range(len(ring))
		) / 2

	def get_coordinates(
		self,
		keyval: str,
		mapserver_url: Optional[str] = None,
		map_name: str = "pa",
		reference: Optional[str] = None,
	) -> dict:
		"""Return one exterior BNG ring, trying the map's ArcGIS layer and WFS."""
		if not keyval.strip():
			raise ValueError("keyval must be a non-empty string.")
		failures = []
		arcgis_attempted = False
		if mapserver_url is None:
			arcgis_attempted = True
			try:
				layer_url, referer = self._arcgis_application_polygon_layer(keyval)
				rings = self._arcgis_coordinates(
					layer_url, keyval, reference, referer=referer
				)
				if rings:
					return {"polygon": rings}
				failures.append("ArcGIS returned no matching polygon feature")
			except (requests.RequestException, RuntimeError, ValueError, TypeError) as error:
				failures.append(f"ArcGIS lookup failed: {error}")

		if mapserver_url is None:
			mapserver_url = urljoin(self._origin, "/PAM/LIVE/MapServer")
		self._check_same_host(mapserver_url)
		try:
			wfs_namespace = "http://www.opengis.net/wfs/2.0"
			ogc_namespace = "http://www.opengis.net/ogc"
			gml_namespace = "http://www.opengis.net/gml/3.2"
			mapserver_namespace = "http://mapserver.gis.umn.edu/mapserver"

			request = ET.Element(
				f"{{{wfs_namespace}}}GetFeature",
				{"service": "WFS", "version": "2.0.0"},
			)
			for feature_type in (
				"Planning_Application_Points",
				"Planning_Application_Polygons",
			):
				query = ET.SubElement(
					request,
					f"{{{wfs_namespace}}}Query",
					{"typeName": feature_type},
				)
				filter_element = ET.SubElement(query, f"{{{ogc_namespace}}}Filter")
				equal = ET.SubElement(
					filter_element,
					f"{{{ogc_namespace}}}PropertyIsEqualTo",
				)
				ET.SubElement(equal, f"{{{ogc_namespace}}}PropertyName").text = "KEYVAL"
				ET.SubElement(equal, f"{{{ogc_namespace}}}Literal").text = keyval

			response = self._request(
				"POST",
				mapserver_url,
				params={"map": map_name},
				data=ET.tostring(request, encoding="utf-8"),
				headers={
					"Content-Type": "text/xml",
					"Accept": "*/*",
					"Origin": self._origin,
					"Referer": self.base_url,
				},
			)
			root = ET.fromstring(response.content)
			if root.tag.rsplit("}", 1)[-1] != "FeatureCollection":
				raise RuntimeError("WFS did not return a FeatureCollection.")
			namespaces = {"ms": mapserver_namespace, "gml": gml_namespace}
			features = root.findall(".//ms:Planning_Application_Polygons", namespaces)
			if not features:
				failures.append("No polygon feature returned by WFS")
			else:
				exterior_rings = []
				for feature in features:
					multi_surface = feature.find(
						"ms:msGeometry/gml:MultiSurface", namespaces
					)
					if multi_surface is not None:
						polygon_elements = multi_surface.findall(".//gml:Polygon", namespaces)
					else:
						polygon_elements = feature.findall(
							"ms:msGeometry/gml:Polygon", namespaces
						)
					for polygon in polygon_elements:
						exterior = polygon.find(
							"gml:exterior/gml:LinearRing/gml:posList", namespaces
						)
						if exterior is not None and (exterior.text or "").strip():
							exterior_rings.append(
								self._coordinates_from_pos_list(exterior)
							)
				if exterior_rings:
					if len(exterior_rings) > 1:
						warnings.warn(
							f"WFS returned {len(exterior_rings)} BNG rings for keyval "
							f"{keyval!r}; using the largest exterior ring.",
							RuntimeWarning,
							stacklevel=2,
						)
					return {"polygon": [max(
						exterior_rings,
						key=lambda ring: abs(self._signed_ring_area(ring)),
					)]}
				failures.append("No polygon coordinates returned by WFS")
		except (requests.RequestException, ET.ParseError, RuntimeError, ValueError, TypeError) as error:
			failures.append(f"WFS lookup failed: {error}")

		if not arcgis_attempted:
			try:
				layer_url, referer = self._arcgis_application_polygon_layer(keyval)
				rings = self._arcgis_coordinates(
					layer_url, keyval, reference, referer=referer
				)
				if rings:
					return {"polygon": rings}
				failures.append("ArcGIS returned no matching polygon feature")
			except (requests.RequestException, RuntimeError, ValueError, TypeError) as error:
				failures.append(f"ArcGIS lookup failed: {error}")

		warnings.warn(
			f"Could not retrieve an LPA polygon for keyval {keyval!r}; "
			"document-coordinate inference will be attempted. " + "; ".join(failures),
			RuntimeWarning,
			stacklevel=2,
		)
		return {"polygon": []}

	def search(self, reference: str) -> str:
		"""Search with fresh form tokens and return the exact application's key."""
		return self._submit_search(reference)[0]

	def get_tabs(self, keyval: str) -> list[dict]:
		soup, page_url = self._get_tab(keyval, "summary")
		return self._parse_tabs(soup, page_url)

	def get_summary(self, keyval: str) -> dict:
		summary, _ = self._get_tab(keyval, "summary")
		further_information, _ = self._get_tab(keyval, "details")
		further_values = self._parse_label_values(further_information)
		if not further_values:
			raise RuntimeError("Expected label/value rows on the Further Information tab.")
		return {
			"summary": self._parse_label_values(summary),
			"further_information": further_values,
		}

	def get_documents(self, keyval: str) -> list[dict]:
		"""Rediscover document links; returned URLs are not permanent addresses."""
		return self._document_page(keyval)[0]

	def _download_document_record(
		self,
		keyval: str,
		document: dict,
		documents_page_url: str,
		reference: Optional[str] = None,
	) -> bytes:
		not_found_retried = False
		session_refreshes = 0
		while True:
			try:
				response = self._request("GET", document["url"], document_download=True, headers={
					"Referer": documents_page_url,
					"Accept": "*/*",
				})
			except requests.HTTPError as error:
				if error.response is None:
					raise
				if error.response.status_code == 429:
					if not self._refresh_session_on_429:
						raise
					if session_refreshes >= self._max_session_refreshes:
						raise requests.HTTPError(
							f"Document {document['id']} still returns HTTP 429 after "
							f"{session_refreshes} session refreshes.", response=error.response,
						) from error
					self._refresh_rate_limited_session(error.response)
					session_refreshes += 1
				elif error.response.status_code == 404:
					if not_found_retried:
						raise RuntimeError(
							f"Document {document['id']} returned 404 after rediscovery in a new session."
						) from error
					not_found_retried = True
					error.response.close()
					self.session.close()
					self.session = self._new_session()
					self._request_interval = self._base_request_interval
					self._has_requested = False
				else:
					raise
				if reference is not None:
					refreshed_keyval, _, _ = self._submit_search(reference)
					if refreshed_keyval != keyval:
						raise RuntimeError(
							f"Search for {reference!r} returned a different application key."
						)
				documents, documents_page_url = self._document_page(keyval)
				matches = [doc for doc in documents if doc["id"] == document["id"]]
				if len(matches) != 1:
					raise RuntimeError(
						f"Expected one document with ID {document['id']} after rediscovery; "
						f"found {len(matches)}."
					)
				document = matches[0]
				continue
			if session_refreshes:
				_LOGGER.warning("Session refresh recovered document %s after HTTP 429.", document["id"])
			return response.content

	def download_document(
		self,
		keyval: str,
		document_id: str,
		*,
		reference: Optional[str] = None,
	) -> bytes:
		"""Download by keyval, optionally using reference for 404 re-search."""
		documents, page_url = self._document_page(keyval)
		matches = [doc for doc in documents if doc["id"] == str(document_id)]
		if len(matches) != 1:
			raise RuntimeError(f"Expected one document with ID {document_id}; found {len(matches)}.")
		return self._download_document_record(
			keyval, matches[0], page_url, reference=reference
		)

	def _save_document_record(
		self,
		keyval: str,
		document: dict,
		documents_page_url: str,
		directory: Union[str, Path],
		reference: Optional[str] = None,
	) -> Path:
		content = self._download_document_record(
			keyval, document, documents_page_url, reference=reference
		)
		extension = Path(urlsplit(document["url"]).path).suffix
		filename_prefix = reference or keyval
		filename = f"{filename_prefix.replace('/', '_')}-{document['id']}{extension}"
		if Path(filename).name != filename or "\\" in filename:
			raise ValueError("Reference and document ID must not contain path separators.")
		directory = Path(directory)
		directory.mkdir(parents=True, exist_ok=True)
		destination = directory / filename
		with destination.open("xb") as output:
			output.write(content)
		return destination

	def save_document(
		self,
		keyval: str,
		document_id: str,
		directory: Union[str, Path],
		*,
		reference: Optional[str] = None,
	) -> Path:
		documents, page_url = self._document_page(keyval)
		matches = [doc for doc in documents if doc["id"] == str(document_id)]
		if len(matches) != 1:
			raise RuntimeError(f"Expected one document with ID {document_id}; found {len(matches)}.")
		return self._save_document_record(
			keyval, matches[0], page_url, directory, reference=reference
		)

	def get_metadata(
		self,
		keyval: str,
		directory: Union[str, Path],
		*,
		ignore_after: Optional[date] = None,
	) -> dict:
		"""Write application metadata, including document URLs, without downloading."""
		if ignore_after is not None and (
			not isinstance(ignore_after, date) or isinstance(ignore_after, datetime)
		):
			raise TypeError("ignore_after must be a datetime.date or None.")
		directory = Path(directory)
		directory.mkdir(parents=True, exist_ok=True)
		if Path(keyval).name != keyval or "\\" in keyval:
			raise ValueError("keyval must not contain path separators.")
		metadata_path = directory / f"{keyval}-metadata.json"
		if metadata_path.exists():
			raise FileExistsError(metadata_path)

		summary = self.get_summary(keyval)
		documents, _ = self._document_page(keyval)
		if ignore_after is not None:
			included_documents = []
			for document in documents:
				try:
					published = datetime.strptime(
						document.get("published"), "%d %b %Y"
					).date()
				except (TypeError, ValueError):
					warnings.warn(
						f"Could not interpret the published date for document {document['id']}; "
						"retaining it despite ignore_after.",
						RuntimeWarning,
						stacklevel=2,
					)
					included_documents.append(document)
					continue
				if published <= ignore_after:
					included_documents.append(document)
			documents = included_documents
		reference = next(
			(value for label, value in summary["summary"].items()
			 if label.rstrip(":").strip().casefold() == "reference"),
			None,
		)
		coordinates = self.get_coordinates(keyval, reference=reference)
		metadata = {
			"reference": reference,
			"id": keyval,
			"summary": summary["summary"],
			"further_information": summary["further_information"],
			"polygon": coordinates["polygon"],
			"documents": documents,
		}
		with metadata_path.open("x", encoding="utf-8") as output:
			json.dump(metadata, output, ensure_ascii=False, indent=2)
			output.write("\n")
		return metadata

	def download_documents(
		self,
		keyval: str,
		metadata: dict,
		directory: Union[str, Path],
	) -> dict:
		"""Download documents listed in metadata and record their saved filenames."""
		directory = Path(directory)
		if metadata.get("id") != keyval:
			raise ValueError("Metadata application ID does not match keyval.")
		documents, documents_page_url = self._document_page(keyval)
		for metadata_document in metadata["documents"]:
			matches = [
				document for document in documents
				if document["id"] == metadata_document["id"]
			]
			if len(matches) != 1:
				raise RuntimeError(
					f"Expected one document with ID {metadata_document['id']}; found {len(matches)}."
				)
			saved_path = self._save_document_record(
				keyval,
				matches[0],
				documents_page_url,
				directory,
				reference=metadata.get("reference"),
			)
			metadata_document["saved_as"] = saved_path.name
			metadata_path = directory / f"{keyval}-metadata.json"
			with metadata_path.open("w", encoding="utf-8") as output:
				json.dump(metadata, output, ensure_ascii=False, indent=2)
				output.write("\n")
		return metadata

	def download_documents_concurrently(
		self,
		keyval: str,
		metadata: dict,
		directory: Union[str, Path],
		document_ids: list[str],
		*,
		max_workers: int,
	) -> dict:
		"""Download selected metadata records concurrently and persist outcomes."""
		if type(max_workers) is not int or max_workers < 1:
			raise ValueError("max_workers must be a positive integer.")
		if metadata.get("id") != keyval:
			raise ValueError("Metadata application ID does not match keyval.")
		if len(document_ids) != len(set(document_ids)):
			raise ValueError("document_ids must not contain duplicates.")

		directory = Path(directory)
		directory.mkdir(parents=True, exist_ok=True)
		if Path(keyval).name != keyval or "\\" in keyval:
			raise ValueError("keyval must not contain path separators.")
		documents_by_id = {str(document["id"]): document for document in metadata["documents"]}
		unknown_ids = set(document_ids) - documents_by_id.keys()
		if unknown_ids:
			raise ValueError(
				"Unknown document IDs: " + ", ".join(sorted(unknown_ids)) + "."
			)
		for document in metadata["documents"]:
			document["downloaded"] = False
			document.pop("download_error", None)

		cookies = self.session.cookies.copy()
		documents_page_url = urljoin(
			self.base_url,
			"applicationDetails.do?" + urlencode({
				"activeTab": "documents", "keyVal": keyval,
			}),
		)
		request_interval = self._request_interval if self._has_requested else 0
		request_lock = Lock()
		last_request_started = [monotonic() if request_interval else None]

		def download_one(document_id: str) -> Path:
			with request_lock:
				if last_request_started[0] is not None:
					wait = request_interval - (monotonic() - last_request_started[0])
					if wait > 0:
						sleep(wait)
				last_request_started[0] = monotonic()

			worker_client = PublicAccessClient(
				self.base_url,
				timeout=self.timeout,
				user_agent=self.user_agent,
				lpa_settings=self._lpa_settings,
			)
			worker_client.session.cookies.update(cookies)
			try:
				return worker_client._save_document_record(
					keyval,
					documents_by_id[document_id],
					documents_page_url,
					directory,
					reference=metadata.get("reference"),
				)
			finally:
				worker_client.close()

		with ThreadPoolExecutor(max_workers=max_workers) as executor:
			future_documents = {
				executor.submit(download_one, document_id): document_id
				for document_id in document_ids
			}
			for future in as_completed(future_documents):
				document_id = future_documents[future]
				document = documents_by_id[document_id]
				try:
					saved_path = future.result()
				except Exception as error:
					document["download_error"] = f"{type(error).__name__}: {error}"
					warnings.warn(
						f"Could not download document {document_id}: {error}",
						RuntimeWarning,
						stacklevel=2,
					)
				else:
					document["downloaded"] = True
					document["saved_as"] = saved_path.name

		metadata_path = directory / f"{keyval}-metadata.json"
		with metadata_path.open("w", encoding="utf-8") as output:
			json.dump(metadata, output, ensure_ascii=False, indent=2)
			output.write("\n")
		return metadata

	def run_all(
		self,
		keyval: str,
		directory: Union[str, Path],
		*,
		ignore_after: Optional[date] = None,
	) -> dict:
		"""Write metadata first, then download all application documents."""
		metadata = self.get_metadata(keyval, directory, ignore_after=ignore_after)
		return self.download_documents(keyval, metadata, directory)

	def close(self) -> None:
		self.session.close()
