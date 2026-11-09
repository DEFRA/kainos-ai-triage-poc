import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

from backend.src.document_screening.tools.lpa_client import PublicAccessClient, _load_lpa_settings


BASE_URL = "https://planning.test/online-applications/"
REFERENCE = "26/02444/TRE"
TABS = """
<a href="applicationDetails.do?activeTab=summary&amp;keyVal=KEY">Details</a>
<a href="applicationDetails.do?activeTab=details&amp;keyVal=KEY">Further Information</a>
<a href="applicationDetails.do?activeTab=documents&amp;keyVal=KEY"> Documents   ( 2 ) </a>
<a href="applicationDetails.do?activeTab=map&amp;keyVal=KEY">Map</a>
<a href="applicationDetails.do?activeTab=printPreview&amp;keyVal=KEY"></a>
"""
SUMMARY = TABS + """<title>26/02444/TRE</title><table>
<tr><th>Reference</th><td>26/02444/TRE</td></tr>
<tr><th>Decision</th><td></td></tr></table>"""
DOCUMENTS = TABS + """<table><tr><th>Description</th><th>View</th>
<th>Document Type</th><th>Date Published</th></tr>
<tr><td>Decision</td><td><a href="files/token/notice-123.pdf"></a></td>
<td>Notice</td><td>21 Oct 2026</td></tr>
<tr><td>Decision</td><td><a href="files/token/photo.jpg" title="View document (ID 456)"></a></td>
<td>Photo</td><td>20 Oct 2026</td></tr></table>"""
WFS_POLYGONS = """<ms:FeatureCollection
xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
xmlns:gml="http://www.opengis.net/gml/3.2">
<ms:Planning_Application_Polygons>
    <ms:KEYVAL>KEY&amp;1</ms:KEYVAL>
    <ms:msGeometry><gml:MultiSurface srsName="urn:ogc:def:crs:EPSG::27700">
        <gml:surfaceMember><gml:Polygon>
            <gml:exterior><gml:LinearRing>
                <gml:posList srsDimension="2">10 20 30 40 10 20</gml:posList>
            </gml:LinearRing></gml:exterior>
            <gml:interior><gml:LinearRing>
                <gml:posList srsDimension="2">12 22 14 24 12 22</gml:posList>
            </gml:LinearRing></gml:interior>
        </gml:Polygon></gml:surfaceMember>
    </gml:MultiSurface></ms:msGeometry>
</ms:Planning_Application_Polygons>
</ms:FeatureCollection>"""
WFS_DIRECT_POLYGON = """<wfs:FeatureCollection
xmlns:wfs="http://www.opengis.net/wfs/2.0"
xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
xmlns:gml="http://www.opengis.net/gml/3.2">
<wfs:member>
    <ms:Planning_Application_Polygons>
        <ms:KEYVAL>KEY</ms:KEYVAL>
        <ms:msGeometry>
            <gml:Polygon srsName="urn:ogc:def:crs:EPSG::27700">
                <gml:exterior><gml:LinearRing>
                    <gml:posList srsDimension="2">100 200 300 400 100 200</gml:posList>
                </gml:LinearRing></gml:exterior>
            </gml:Polygon>
        </ms:msGeometry>
    </ms:Planning_Application_Polygons>
</wfs:member>
</wfs:FeatureCollection>"""


def response(content, url=BASE_URL, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = content.encode() if isinstance(content, str) else content
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


def search_responses(token="private-token"):
    form = f"""<form id="simpleSearchForm" method="post" action="simpleSearchResults.do?action=firstPage">
    <input type="hidden" name="_csrf" value="{token}">
    <input type="hidden" name="extraToken" value="private-extra">
    </form>"""
    return [response(form), response(SUMMARY)]


class PublicAccessClientTests(unittest.TestCase):
    def setUp(self):
        settings_patch = patch("document_screening.tools.lpa_client._load_lpa_settings", return_value={})
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        sleep_patch = patch("document_screening.tools.lpa_client.sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.client = PublicAccessClient(BASE_URL.rstrip("/"), timeout=12, user_agent="Test/1")
        self.addCleanup(self.client.close)

    def test_no_pacing_when_requests_succeed_by_default(self):
        with patch.object(self.client.session, "request", return_value=response("ok")):
            self.client._request("GET", BASE_URL)
            self.client._request("GET", BASE_URL)
        self.sleep.assert_not_called()

    def test_429_increments_pacing_and_retains_it_after_success(self):
        with patch.object(self.client.session, "request", side_effect=[
            response("", status=429), response("", status=429),
            response("ok"), response("ok"),
        ]) as request:
            self.client._request("GET", BASE_URL)
            self.client._request("GET", BASE_URL)
        self.assertEqual(request.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [3, 6, 6])

    def test_429_stops_after_three_retries(self):
        with patch.object(self.client.session, "request", side_effect=[
            response("", status=429) for _ in range(4)
        ]) as request:
            with self.assertRaises(requests.HTTPError):
                self.client._request("GET", BASE_URL)
        self.assertEqual(request.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [3, 6, 9])

    def test_429_honours_retry_after_seconds(self):
        with patch.object(self.client.session, "request", side_effect=[
            response("", status=429, headers={"Retry-After": "12"}), response("ok"),
        ]):
            self.client._request("GET", BASE_URL)
        self.sleep.assert_called_once_with(12)

    def test_429_rejects_long_wait_without_retrying(self):
        with patch.object(self.client.session, "request", return_value=response(
            "", status=429, headers={"Retry-After": "61"},
        )) as request:
            with self.assertRaisesRegex(requests.HTTPError, "exceeds"):
                self.client._request("GET", BASE_URL)
        request.assert_called_once()
        self.sleep.assert_not_called()

    def test_429_honours_retry_after_http_date(self):
        with patch("document_screening.tools.lpa_client.datetime") as clock, patch.object(
            self.client.session, "request", side_effect=[
                response("", status=429, headers={
                    "Retry-After": "Thu, 29 Oct 2026 12:00:12 GMT",
                }),
                response("ok"),
            ],
        ):
            clock.now.return_value = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
            self.client._request("GET", BASE_URL)
        self.sleep.assert_called_once_with(12)

    def test_invalid_retry_after_warns_and_uses_increment(self):
        with patch.object(self.client.session, "request", side_effect=[
            response("", status=429, headers={"Retry-After": "invalid"}), response("ok"),
        ]), self.assertWarnsRegex(RuntimeWarning, "Invalid Retry-After"):
            self.client._request("GET", BASE_URL)
        self.sleep.assert_called_once_with(3)

    def test_maximum_wait_is_allowed_but_increment_beyond_it_is_rejected(self):
        self.client._max_wait = 3
        with patch.object(self.client.session, "request", side_effect=[
            response("", status=429), response("", status=429),
        ]) as request:
            with self.assertRaisesRegex(requests.HTTPError, "exceeds"):
                self.client._request("GET", BASE_URL)
        self.assertEqual(request.call_count, 2)
        self.sleep.assert_called_once_with(3)
        self.assertEqual(self.client._request_interval, 3)

    def test_loads_pacing_from_yaml_with_process_environment_override(self):
        settings = {
            "request_interval_seconds": "2",
            "retry_increment_seconds": "4",
            "max_retries": "1",
            "max_wait_seconds": "15",
            "refresh_session_on_429": "false",
        }
        with patch("document_screening.tools.lpa_client._load_lpa_settings", return_value=settings) as load, patch.dict(
            "os.environ", {"LPA_REQUEST_INTERVAL_SECONDS": "None"}, clear=True,
        ):
            client = PublicAccessClient(BASE_URL)
            self.addCleanup(client.close)
        load.assert_called_once_with()
        self.assertEqual(client._request_interval, 0)
        self.assertEqual(client._retry_increment, 4)
        self.assertEqual(client._max_retries, 1)
        self.assertEqual(client._max_wait, 15)
        self.assertFalse(client._refresh_session_on_429)
        with patch.object(client.session, "request", side_effect=[
            response("", status=429), response("", status=429),
        ]) as request:
            with self.assertRaises(requests.HTTPError):
                client._request("GET", BASE_URL)
        self.assertEqual(request.call_count, 2)
        self.sleep.assert_called_once_with(4)

    def test_default_yaml_contains_current_lpa_settings(self):
        settings = _load_lpa_settings()

        self.assertEqual(settings, {
            "request_interval_seconds": 0,
            "retry_increment_seconds": 0,
            "max_retries": 0,
            "max_wait_seconds": 60,
            "refresh_session_on_429": True,
            "session_refresh_wait_seconds": 60,
            "max_session_refreshes": 3,
        })

    def test_positive_initial_pacing_applies_between_requests(self):
        self.client._request_interval = 2
        with patch.object(self.client.session, "request", return_value=response("ok")):
            self.client._request("GET", BASE_URL)
            self.client._request("GET", BASE_URL)
        self.sleep.assert_called_once_with(2)

    def test_all_zero_legacy_settings_allow_fixed_cooldown_session_recovery(self):
        with patch.dict("os.environ", {
            "LPA_REQUEST_INTERVAL_SECONDS": "0",
            "LPA_RETRY_INCREMENT_SECONDS": "0",
            "LPA_MAX_RETRIES": "0",
            "LPA_REFRESH_SESSION_ON_429": "true",
            "LPA_SESSION_REFRESH_WAIT_SECONDS": "4",
        }, clear=True):
            client = PublicAccessClient(BASE_URL)
            self.addCleanup(client.close)
        fresh_session = Mock(max_redirects=30)
        fresh_session.request.side_effect = [response("ok"), response("next")]
        with patch.object(client.session, "request", return_value=response("", status=429)) as original, patch.object(
            client, "_new_session", return_value=fresh_session,
        ), self.assertLogs("document_screening.tools.lpa_client", level="WARNING"):
            self.assertEqual(client._request("GET", BASE_URL).content, b"ok")
            self.assertEqual(client._request("GET", BASE_URL).content, b"next")
        original.assert_called_once()
        self.sleep.assert_called_once_with(4)
        self.assertEqual(client._request_interval, 0)

    def test_repeated_metadata_429_stops_at_refresh_limit(self):
        self.client._refresh_session_on_429 = True
        self.client._max_session_refreshes = 2
        fresh_session = Mock(max_redirects=30)
        fresh_session.request.side_effect = [response("", status=429), response("", status=429)]
        with patch.object(self.client.session, "request", return_value=response("", status=429)), patch.object(
            self.client, "_new_session", return_value=fresh_session,
        ) as refresh, self.assertLogs("document_screening.tools.lpa_client", level="WARNING"):
            with self.assertRaisesRegex(requests.HTTPError, "after 2 session refreshes"):
                self.client._request("GET", BASE_URL)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5, 5])

    def test_invalid_pacing_settings_fail_at_initialisation(self):
        for key, value in (
            ("LPA_REQUEST_INTERVAL_SECONDS", "-1"),
            ("LPA_REQUEST_INTERVAL_SECONDS", "61"),
            ("LPA_RETRY_INCREMENT_SECONDS", "-1"),
            ("LPA_RETRY_INCREMENT_SECONDS", "nan"),
            ("LPA_MAX_RETRIES", "-1"),
            ("LPA_MAX_RETRIES", "1.5"),
            ("LPA_MAX_WAIT_SECONDS", "inf"),
            ("LPA_REFRESH_SESSION_ON_429", "maybe"),
        ):
            with self.subTest(key=key, value=value), patch.dict(
                "os.environ", {key: value}, clear=True,
            ), self.assertRaises(ValueError):
                PublicAccessClient(BASE_URL)

    def test_search_fetches_fresh_tokens_and_uses_form_action(self):
        with patch.object(self.client.session, "request", side_effect=(
            search_responses("first") + search_responses("second")
        )) as request:
            self.assertEqual(self.client.search(REFERENCE), "KEY")
            self.assertEqual(self.client.search(REFERENCE), "KEY")
        self.assertEqual(self.client.base_url, BASE_URL)
        self.assertEqual(self.client.session.headers["User-Agent"], "Test/1")
        posts = [call for call in request.call_args_list if call.args[0] == "POST"]
        self.assertEqual(dict(posts[0].kwargs["data"])["_csrf"], "first")
        self.assertEqual(dict(posts[1].kwargs["data"])["_csrf"], "second")
        self.assertEqual(dict(posts[0].kwargs["data"])["extraToken"], "private-extra")
        self.assertEqual(posts[0].args[1], BASE_URL + "simpleSearchResults.do?action=firstPage")
        self.assertEqual(posts[0].kwargs["headers"], {
            "Referer": BASE_URL, "Origin": "https://planning.test",
        })
        self.assertEqual(posts[0].kwargs["timeout"], 12)

    def test_search_rejects_wrong_reference_and_non_summary_pages(self):
        for page in (SUMMARY.replace(REFERENCE, "OTHER"), "<title>No results</title>"):
            with self.subTest(page=page), patch.object(
                self.client.session, "request",
                side_effect=[search_responses()[0], response(page)],
            ):
                with self.assertRaisesRegex(RuntimeError, "page title"):
                    self.client.search(REFERENCE)

    def test_missing_form_and_http_errors_are_rejected(self):
        with patch.object(self.client.session, "request", return_value=response("<title>Error</title>")):
            with self.assertRaisesRegex(RuntimeError, "Simple Search form"):
                self.client.search(REFERENCE)
        with patch.object(self.client.session, "request", return_value=response("", status=503)):
            with self.assertRaises(requests.HTTPError):
                self.client.search(REFERENCE)

    def test_tabs_normalize_counts_and_include_map_and_print_preview(self):
        with patch.object(self.client.session, "request", return_value=response(SUMMARY)):
            tabs = self.client.get_tabs("KEY")
        self.assertEqual(tabs[2]["name"], "Documents")
        self.assertEqual(tabs[2]["count"], 2)
        self.assertEqual(tabs[3]["active_tab"], "map")
        self.assertEqual(tabs[4]["name"], "printPreview")

    def test_summary_returns_both_sections_and_empty_values(self):
        details = TABS + "<table><tr><th>Ward</th><td>North</td></tr></table>"
        with patch.object(self.client.session, "request", side_effect=(
            [response(SUMMARY), response(details)]
        )):
            summary = self.client.get_summary("KEY")
        self.assertEqual(summary["summary"]["Decision"], "")
        self.assertEqual(summary["further_information"], {"Ward": "North"})

    def test_documents_use_headers_and_title_id_fallback(self):
        with patch.object(self.client.session, "request", return_value=response(DOCUMENTS)):
            documents = self.client.get_documents("KEY")
        self.assertEqual([doc["id"] for doc in documents], ["123", "456"])
        self.assertEqual(documents[0]["published"], "21 Oct 2026")
        self.assertEqual(documents[0]["type"], "Notice")
        self.assertIsNone(documents[0]["drawing_number"])
        self.assertEqual(documents[0]["url"], BASE_URL + "files/token/notice-123.pdf")

    def test_off_host_actions_links_and_redirects_are_rejected(self):
        for content in (
            '<form id="simpleSearchForm" method="post" action="https://other.test/search"></form>',
            None,
        ):
            first_response = response(content) if content else response(
                "", status=302, headers={"Location": "https://other.test/"}
            )
            with self.subTest(content=content), patch.object(
                self.client.session, "request", return_value=first_response
            ) as request:
                with self.assertRaises(ValueError):
                    self.client.search(REFERENCE)
                self.assertEqual(request.call_count, 1)
        with self.assertRaises(ValueError):
            self.client._parse_tabs(BeautifulSoup(
                TABS.replace("applicationDetails.do", "https://other.test/applicationDetails.do"),
                "html.parser",
            ), BASE_URL)

    def test_document_download_retries_404_once_with_fresh_session(self):
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = search_responses() + [
            response(DOCUMENTS.replace("token", "fresh")),
            response(b"%PDF-checked", url=BASE_URL + "files/fresh/notice-123.pdf"),
        ]
        with patch.object(self.client.session, "request", side_effect=(
            [response(DOCUMENTS), response("", status=404)]
        )), patch.object(self.client, "_new_session", return_value=second_session):
            self.assertEqual(
                self.client.download_document("KEY", "123", reference=REFERENCE),
                b"%PDF-checked",
            )
        final_request = second_session.request.call_args
        self.assertIn("files/fresh/notice-123.pdf", final_request.args[1])
        self.assertEqual(final_request.kwargs["headers"]["Referer"], BASE_URL)

    def test_429_refresh_resets_pacing_and_uses_new_url(self):
        self.client._refresh_session_on_429 = True
        self.client._request_interval = 12
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = search_responses() + [
            response(DOCUMENTS.replace("token", "fresh")), response(b"recovered"),
        ]
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429),
        ]), patch.object(self.client, "_new_session", return_value=second_session) as refresh, self.assertLogs(
            "document_screening.tools.lpa_client", level="WARNING",
        ) as logs:
            self.assertEqual(
                self.client.download_document("KEY", "123", reference=REFERENCE), b"recovered",
            )
        refresh.assert_called_once()
        self.assertIn("files/fresh/notice-123.pdf", second_session.request.call_args.args[1])
        self.assertEqual(self.client._request_interval, 0)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [12, 5])
        self.assertIn("recovered", logs.output[-1])

    def test_429_refresh_is_disabled_by_default(self):
        self.client._max_retries = 0
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429),
        ]), patch.object(self.client, "_new_session") as refresh:
            with self.assertRaises(requests.HTTPError):
                self.client.download_document("KEY", "123")
        refresh.assert_not_called()

    def test_repeated_429_stops_at_consecutive_session_refresh_limit(self):
        self.client._refresh_session_on_429 = True
        self.client._max_session_refreshes = 1
        self.client._max_retries = 0
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = [response(DOCUMENTS), response("", status=429)]
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429),
        ]), patch.object(self.client, "_new_session", return_value=second_session) as refresh, self.assertLogs(
            "document_screening.tools.lpa_client", level="WARNING",
        ):
            with self.assertRaises(requests.HTTPError):
                self.client.download_document("KEY", "123")
        refresh.assert_called_once()

    def test_document_can_refresh_again_when_the_new_session_also_returns_429(self):
        self.client._refresh_session_on_429 = True
        first_fresh_session = Mock(max_redirects=30)
        first_fresh_session.request.side_effect = [response(DOCUMENTS), response("", status=429)]
        second_fresh_session = Mock(max_redirects=30)
        second_fresh_session.request.side_effect = [response(DOCUMENTS), response(b"recovered")]
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429),
        ]), patch.object(self.client, "_new_session", side_effect=[
            first_fresh_session, second_fresh_session,
        ]) as refresh, self.assertLogs("document_screening.tools.lpa_client", level="WARNING"):
            self.assertEqual(self.client.download_document("KEY", "123"), b"recovered")
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5, 5])

    def test_each_application_document_can_refresh_its_session(self):
        self.client._refresh_session_on_429 = True
        self.client._max_retries = 0
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = [
            response(DOCUMENTS), response(b"recovered"),
            response(DOCUMENTS), response("", status=429),
            response(DOCUMENTS), response(b"next document"),
        ]
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429),
        ]), patch.object(self.client, "_new_session", return_value=second_session) as refresh, self.assertLogs(
            "document_screening.tools.lpa_client", level="WARNING",
        ):
            self.assertEqual(self.client.download_document("KEY", "123"), b"recovered")
            self.assertEqual(self.client.download_document("KEY", "456"), b"next document")
        self.assertEqual(refresh.call_count, 2)

    def test_429_refresh_respects_server_cooldown_and_wait_limit(self):
        self.client._refresh_session_on_429 = True
        self.client._max_retries = 0
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = [response(DOCUMENTS), response(b"recovered")]
        with patch.object(self.client.session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429, headers={"Retry-After": "15"}),
        ]), patch.object(self.client, "_new_session", return_value=second_session), self.assertLogs(
            "document_screening.tools.lpa_client", level="WARNING",
        ):
            self.client.download_document("KEY", "123")
        self.sleep.assert_called_once_with(15)
        with patch.object(second_session, "request", side_effect=[
            response(DOCUMENTS), response("", status=429, headers={"Retry-After": "61"}),
        ]), patch.object(self.client, "_new_session") as refresh:
            with self.assertRaisesRegex(requests.HTTPError, "exceeds"):
                self.client.download_document("KEY", "456")
        refresh.assert_not_called()

    def test_same_host_post_redirect_switches_to_get_without_form_tokens(self):
        with patch.object(self.client.session, "request", side_effect=[
            search_responses()[0],
            response("", status=302, headers={"Location": "applicationDetails.do?keyVal=KEY"}),
            response(SUMMARY),
        ]) as request:
            self.assertEqual(self.client.search(REFERENCE), "KEY")
        redirected = request.call_args_list[-1]
        self.assertEqual(redirected.args[0], "GET")
        self.assertNotIn("data", redirected.kwargs)

    def test_off_host_document_links_are_rejected(self):
        soup = BeautifulSoup(DOCUMENTS.replace(
            'href="files/token/notice-123.pdf"',
            'href="https://other.test/files/notice-123.pdf"',
        ), "html.parser")
        with self.assertRaises(ValueError):
            self.client._parse_documents(soup, BASE_URL)

    def test_unexpected_documents_page_and_unidentifiable_files_fail(self):
        for page in (
            "<title>Session expired</title>",
            TABS + "<p>No table here</p>",
            DOCUMENTS.replace("notice-123.pdf", "unidentified.pdf"),
        ):
            with self.subTest(page=page), patch.object(
                self.client.session, "request",
                return_value=response(page),
            ):
                with self.assertRaises(RuntimeError):
                    self.client.get_documents("KEY")

    def test_repeated_404_fails_clearly(self):
        second_session = Mock(max_redirects=30)
        second_session.request.side_effect = search_responses() + [
            response(DOCUMENTS), response("", status=404)
        ]
        with patch.object(self.client.session, "request", side_effect=(
            [response(DOCUMENTS), response("", status=404)]
        )), patch.object(self.client, "_new_session", return_value=second_session):
            with self.assertRaisesRegex(RuntimeError, "404 after rediscovery"):
                self.client.download_document("KEY", "123", reference=REFERENCE)

    def test_download_accepts_response_without_pdf_or_jpeg_signature(self):
        content = b"arbitrary document bytes"
        with patch.object(self.client.session, "request", side_effect=(
            [response(DOCUMENTS), response(
                content, headers={"Content-Type": "application/octet-stream"}
            )]
        )):
            self.assertEqual(self.client.download_document("KEY", "123"), content)

    def test_save_detects_extension_and_never_overwrites(self):
        document = {"id": "456", "url": BASE_URL + "files/image-456.jpg"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.client, "_document_page", return_value=([document], BASE_URL)), \
                patch.object(
                    self.client,
                    "_download_document_record",
                    return_value=b"bytes that do not match a JPEG signature",
                ):
            path = self.client.save_document("KEY", "456", directory, reference=REFERENCE)
            self.assertEqual(path, Path(directory) / "26_02444_TRE-456.jpg")
            self.assertEqual(path.read_bytes(), b"bytes that do not match a JPEG signature")
            with self.assertRaises(FileExistsError):
                self.client.save_document("KEY", "456", directory, reference=REFERENCE)

    def test_run_all_writes_urls_before_downloading_documents(self):
        summary = {
            "summary": {"Reference": REFERENCE, "Proposal": "Wind turbines"},
            "further_information": {"Ward": "North"},
        }
        documents = [{
            "id": "123",
            "published": "21 Oct 2026",
            "type": "Notice",
            "drawing_number": None,
            "description": "Decision notice",
            "url": "https://planning.test/files/temporary-link.pdf",
        }]

        def verify_manifest_then_download(*args, **kwargs):
            manifest_path = Path(directory) / "KEY-metadata.json"
            saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["documents"][0]["url"], documents[0]["url"])
            return Path(directory) / "26_02444_TRE-123.pdf"

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.client, "get_summary", return_value=summary), \
                patch.object(self.client, "_document_page", return_value=(documents, BASE_URL)), \
                patch.object(self.client, "get_coordinates", return_value={"polygon": [[[1, 2], [3, 4]]]}), \
                patch.object(
                    self.client,
                    "_save_document_record",
                    side_effect=verify_manifest_then_download,
                ) as save_document:
            manifest = self.client.run_all("KEY", directory)
            save_document.assert_called_once_with(
                "KEY", documents[0], BASE_URL, Path(directory), reference=REFERENCE
            )
            self.assertEqual(manifest["id"], "KEY")
            self.assertEqual(manifest["summary"], summary["summary"])
            self.assertEqual(manifest["further_information"], summary["further_information"])
            self.assertEqual(manifest["polygon"], [[[1, 2], [3, 4]]])
            self.assertEqual(manifest["documents"], [{
                "id": "123",
                "published": "21 Oct 2026",
                "type": "Notice",
                "drawing_number": None,
                "description": "Decision notice",
                "url": "https://planning.test/files/temporary-link.pdf",
                "saved_as": "26_02444_TRE-123.pdf",
            }])
            manifest_path = Path(directory) / "KEY-metadata.json"
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)

    def test_run_all_does_not_overwrite_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "KEY-metadata.json"
            manifest.write_text("existing", encoding="utf-8")
            with patch.object(self.client, "get_summary") as get_summary:
                with self.assertRaises(FileExistsError):
                    self.client.run_all("KEY", directory)
            get_summary.assert_not_called()

    def test_ignore_after_includes_cutoff_date_and_excludes_later_documents(self):
        summary = {
            "summary": {"Reference": REFERENCE},
            "further_information": {},
        }
        documents = [
            {"id": "before", "published": "20 Oct 2026", "url": BASE_URL + "before.pdf"},
            {"id": "same-day", "published": "21 Oct 2026", "url": BASE_URL + "same-day.pdf"},
            {"id": "after", "published": "22 Oct 2026", "url": BASE_URL + "after.pdf"},
        ]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.client, "get_summary", return_value=summary), \
                patch.object(self.client, "_document_page", return_value=(documents, BASE_URL)), \
                patch.object(self.client, "get_coordinates", return_value={"polygon": []}):
            manifest = self.client.get_metadata(
                "KEY", directory, ignore_after=date(2026, 10, 21)
            )

            self.assertEqual(
                [document["id"] for document in manifest["documents"]],
                ["before", "same-day"],
            )
            saved_manifest = json.loads(
                (Path(directory) / "KEY-metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_manifest, manifest)

    def test_ignore_after_retains_documents_with_unrecognized_dates(self):
        summary = {
            "summary": {"Reference": REFERENCE},
            "further_information": {},
        }
        documents = [{"id": "unknown", "published": "unknown", "url": BASE_URL + "unknown.pdf"}]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.client, "get_summary", return_value=summary), \
                patch.object(self.client, "_document_page", return_value=(documents, BASE_URL)), \
                patch.object(self.client, "get_coordinates", return_value={"polygon": []}):
            with self.assertWarnsRegex(RuntimeWarning, "retaining it despite ignore_after"):
                manifest = self.client.get_metadata(
                    "KEY", directory, ignore_after=date(2026, 10, 21)
                )
        self.assertEqual(manifest["documents"], documents)

    def test_concurrent_downloads_record_success_and_failure(self):
        documents = [
            {"id": "ok", "url": BASE_URL + "files/ok.pdf"},
            {"id": "failed", "url": BASE_URL + "files/failed.pdf"},
        ]
        metadata = {"id": "KEY", "reference": REFERENCE, "documents": documents}
        rendezvous = threading.Barrier(2)

        def download(keyval, document, page_url, directory, reference=None):
            rendezvous.wait(timeout=3)
            if document["id"] == "failed":
                raise OSError("temporary failure")
            return Path(directory) / "ok.pdf"

        worker = Mock()
        worker.session = requests.Session()
        worker._save_document_record.side_effect = download
        self.client._request_interval = 0
        self.client._has_requested = False
        with tempfile.TemporaryDirectory() as directory, \
                patch("document_screening.tools.lpa_client.PublicAccessClient", return_value=worker):
            with self.assertWarnsRegex(RuntimeWarning, "Could not download document failed"):
                result = self.client.download_documents_concurrently(
                    "KEY", metadata, directory, ["ok", "failed"], max_workers=2
                )

            self.assertEqual(
                [(document["id"], document["downloaded"]) for document in result["documents"]],
                [("ok", True), ("failed", False)],
            )
            self.assertEqual(result["documents"][0]["saved_as"], "ok.pdf")
            self.assertIn("temporary failure", result["documents"][1]["download_error"])
            saved_metadata = json.loads(
                (Path(directory) / "KEY-metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_metadata, result)
        self.assertEqual(worker._save_document_record.call_count, 2)

    def test_get_coordinates_posts_custom_wfs_request_and_parses_rings(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        with patch.object(
            self.client.session,
            "request",
            return_value=response(WFS_POLYGONS, url=endpoint),
        ) as request:
            polygons = self.client.get_coordinates("KEY&1", endpoint, map_name="pa")

        self.assertEqual(polygons, {
            "polygon": [[[10.0, 20.0], [30.0, 40.0], [10.0, 20.0]]],
        })
        call = request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertEqual(call.args[1], endpoint)
        self.assertEqual(call.kwargs["params"], {"map": "pa"})
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "text/xml")
        request_xml = ET.fromstring(call.kwargs["data"])
        wfs_namespace = "{http://www.opengis.net/wfs/2.0}"
        ogc_namespace = "{http://www.opengis.net/ogc}"
        queries = request_xml.findall(f"{wfs_namespace}Query")
        self.assertEqual(
            [query.get("typeName") for query in queries],
            ["Planning_Application_Points", "Planning_Application_Polygons"],
        )
        literals = request_xml.findall(f".//{ogc_namespace}Literal")
        self.assertEqual([literal.text for literal in literals], ["KEY&1", "KEY&1"])

    def test_get_coordinates_parses_direct_polygon_geometry(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        with patch.object(
            self.client.session,
            "request",
            return_value=response(WFS_DIRECT_POLYGON, url=endpoint),
        ):
            coordinates = self.client.get_coordinates("KEY", endpoint)

        self.assertEqual(coordinates, {
            "polygon": [[[100.0, 200.0], [300.0, 400.0], [100.0, 200.0]]],
        })

    def test_get_coordinates_warns_when_polygon_feature_is_missing(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        no_features = '<ms:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver" />'
        with patch.object(self.client.session, "request", return_value=response(no_features)):
            with self.assertWarnsRegex(RuntimeWarning, "No polygon feature"):
                self.assertEqual(self.client.get_coordinates("KEY", endpoint), {"polygon": []})

    def test_get_coordinates_warns_when_feature_has_no_geometry(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        no_geometry = """<ms:FeatureCollection
            xmlns:ms="http://mapserver.gis.umn.edu/mapserver"></ms:FeatureCollection>"""
        feature = no_geometry.replace(
            "</ms:FeatureCollection>",
            "<ms:Planning_Application_Polygons><ms:KEYVAL>KEY</ms:KEYVAL>"
            "</ms:Planning_Application_Polygons></ms:FeatureCollection>",
        )
        with patch.object(self.client.session, "request", return_value=response(feature)):
            with self.assertWarnsRegex(RuntimeWarning, "No polygon coordinates"):
                self.assertEqual(self.client.get_coordinates("KEY", endpoint), {"polygon": []})

    def test_get_coordinates_infers_mapserver_endpoint(self):
        with patch.object(
            self.client.session,
            "request",
            return_value=response(WFS_POLYGONS, url="https://planning.test/PAM/LIVE/MapServer"),
        ) as request:
            self.client.get_coordinates("KEY&1")
        self.assertEqual(
            request.call_args.args[1],
            "https://planning.test/PAM/LIVE/MapServer",
        )

    def test_get_coordinates_keeps_wfs_and_http_errors_fatal(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        with patch.object(
            self.client.session,
            "request",
            return_value=response(
                '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" />'
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "FeatureCollection"):
                self.client.get_coordinates("KEY", endpoint)
        with patch.object(
            self.client.session,
            "request",
            return_value=response("WFS unavailable", status=503),
        ):
            with self.assertRaises(requests.HTTPError):
                self.client.get_coordinates("KEY", endpoint)

    def test_get_coordinates_rejects_invalid_dimensions(self):
        endpoint = "https://planning.test/PAM/LIVE/MapServer"
        invalid_dimension = WFS_POLYGONS.replace('srsDimension="2"', 'srsDimension="3"', 1)
        with patch.object(self.client.session, "request", return_value=response(invalid_dimension)):
            with self.assertRaisesRegex(ValueError, "2D"):
                self.client.get_coordinates("KEY&1", endpoint)

    def test_get_coordinates_rejects_endpoint_on_another_host(self):
        with patch.object(self.client.session, "request") as request:
            with self.assertRaisesRegex(ValueError, "outside the configured"):
                self.client.get_coordinates(
                    "KEY", "https://other.test/PAM/LIVE/MapServer"
                )
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()