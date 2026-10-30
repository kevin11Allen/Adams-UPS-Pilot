"""Network-free tests: synthetic fixtures, no real credentials or shipments."""
import copy
import http.client
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from .ups_client import CIE, UPSClient, PilotError, validate, shipment
    from .server import LabelCache, PilotServer
except ImportError:
    from ups_client import CIE, UPSClient, PilotError, validate, shipment
    from server import LabelCache, PilotServer


def fixture():
    address = {"name": "Test fixture", "phone": "5550100000", "address1": "Test only", "address2": "", "city": "Test city", "state": "NJ", "postal_code": "00000", "country": "US"}
    return {"origin": address, "destination": copy.deepcopy(address), "packages": [{"length": 10, "width": 8, "height": 6, "weight": 3, "dimension_unit": "IN", "weight_unit": "LBS"}], "customer_account": "", "service_code": "03", "confirm": True, "idempotency_key": "test-key-123456789"}


class PayloadTests(unittest.TestCase):
    def test_empty_account_bills_adams_only(self):
        data = fixture()
        data["payer_type"] = "third_party"
        for label in (False, True):
            built = shipment(validate(data), "ABC123", label)
            payment = built["PaymentInformation" if label else "PaymentDetails"]
            self.assertEqual(payment, {"ShipmentCharge": [{"Type": "01", "BillShipper": {"AccountNumber": "ABC123"}}]})

    def test_receiver_and_third_party_do_not_fallback(self):
        for payer, key in (("receiver", "BillReceiver"), ("third_party", "BillThirdParty")):
            data = fixture()
            data.update(customer_account="XYZ789", payer_type=payer, billing_postal_code="11111", billing_country="US")
            charge = shipment(validate(data), "ABC123", True)["PaymentInformation"]["ShipmentCharge"][0]
            self.assertEqual(set(charge), {"Type", key})
            self.assertEqual(charge[key]["AccountNumber"], "XYZ789")
            self.assertEqual("CountryCode" in charge[key]["Address"], payer == "third_party")
        data["payer_type"] = "shipper"
        with self.assertRaises(PilotError):
            validate(data)

    def test_customer_requires_billing_fields(self):
        data = fixture()
        data.update(customer_account="XYZ789", payer_type="receiver")
        with self.assertRaises(PilotError):
            validate(data)
        data.update(payer_type="third_party", billing_postal_code="11111")
        with self.assertRaises(PilotError):
            validate(data)

    def test_actual_multiple_package_units_and_weights(self):
        data = fixture()
        data["packages"].append({"length": 40, "width": 30, "height": 20, "weight": 5.75, "dimension_unit": "CM", "weight_unit": "KGS"})
        built = shipment(validate(data), "ABC123")
        self.assertEqual(len(built["Package"]), 2)
        self.assertEqual(built["Package"][1]["PackageWeight"], {"UnitOfMeasurement": {"Code": "KGS"}, "Weight": "5.75"})
        self.assertEqual(built["Package"][1]["Dimensions"]["Length"], "40")

    def test_invalid_numbers_and_unsupported_routes(self):
        for bad in (0, -1, True, float("nan"), float("inf"), "bad", [], 10001):
            data = fixture()
            data["packages"][0]["weight"] = bad
            with self.subTest(value=bad), self.assertRaises(PilotError):
                validate(data)
        data = fixture()
        data["destination"]["country"] = "DE"
        with self.assertRaises(PilotError):
            validate(data)

    def test_negotiated_request_and_honest_rate_output(self):
        data = fixture()
        data.update(customer_account="XYZ789", payer_type="receiver", billing_postal_code="11111", negotiated_rates=True)
        self.assertIn("TPFCNegotiatedRatesIndicator", shipment(validate(data), "ABC123")["ShipmentRatingOptions"])
        client = UPSClient()
        client.config = Mock(return_value={"shipper_number": "ABC123"})
        client._api = Mock(return_value={"RateResponse": {"RatedShipment": [{"Service": {"Code": "03"}, "TotalCharges": {"MonetaryValue": "12.34", "CurrencyCode": "USD"}}]}})
        reply = client.rates(validate(data))
        self.assertEqual(reply["rates"][0]["rate_type"], "published")
        self.assertEqual(client._api.call_args.args[0], "/api/rating/v2409/Shop")

    def test_config_status_whitelists_address(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"client_id": "test-client", "client_secret": "DO_NOT_EXPOSE", "shipper_number": "ABC123", "origin": {"city": "Test city", "client_secret": "DO_NOT_EXPOSE"}}))
            result = UPSClient(str(path)).status()
            self.assertTrue(result["configured"])
            self.assertEqual(result["defaults"]["origin"], {"city": "Test city"})
            self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))
            self.assertNotIn("ABC123", json.dumps(result))

    def test_token_cached_and_cie_only(self):
        client = UPSClient()
        client.config = Mock(return_value={"client_id": "id", "client_secret": "secret", "shipper_number": "ABC123"})
        client._request = Mock(return_value={"access_token": "token", "expires_in": 3600})
        self.assertEqual(client.token(), client.token())
        self.assertEqual(client._request.call_count, 1)
        self.assertEqual(CIE, "https://wwwcie.ups.com")

    def test_upstream_http_error_never_exposes_details(self):
        client = UPSClient()
        client._opener = Mock()
        client._opener.open.side_effect = urllib.error.HTTPError(CIE, 400, "SECRET_ACCOUNT_AND_TOKEN", {}, None)
        with self.assertRaises(PilotError) as ctx:
            client._request("/api/rating/v2409/Shop", b"{}", {})
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "ups_rejected")
        client._opener.open.assert_called_once()

    def test_upstream_only_numeric_error_codes_are_exposed(self):
        client = UPSClient()
        client._opener = Mock()
        error = {"response": {"errors": [{"code": "250003", "message": "SECRET ACCOUNT"}, {"code": "SECRET_TOKEN"}]}}
        client._opener.open.side_effect = urllib.error.HTTPError(CIE, 401, "SECRET", {}, io.BytesIO(json.dumps(error).encode()))
        with self.assertRaises(PilotError) as ctx:
            client._request("/api/rating/v2409/Shop", b"{}", {})
        self.assertIn("HTTP 401; UPS code 250003", str(ctx.exception))
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_explicit_ca_bundle_keeps_verified_context(self):
        with patch.dict("os.environ", {"UPS_CA_BUNDLE": "/synthetic/ca.pem"}), patch("ssl.create_default_context") as create:
            UPSClient()
            create.assert_called_once_with(cafile="/synthetic/ca.pem")

    def test_label_requires_gif_and_sanitizes_upstream(self):
        client = UPSClient()
        client.config = Mock(return_value={"shipper_number": "ABC123"})
        client._api = Mock(return_value={"ShipmentResponse": {"ShipmentResults": {"ShipmentIdentificationNumber": "TEST123", "PackageResults": {"TrackingNumber": "TEST123", "ShippingLabel": {"GraphicImage": "R0lGODlh"}}}}})
        result = client.label(validate(fixture()))
        self.assertEqual(result["labels"][0]["format"], "GIF")
        client._api.return_value = {"error": "PRIVATE_RAW_ERROR"}
        with self.assertRaises(PilotError) as ctx:
            client.label(validate(fixture()))
        self.assertNotIn("PRIVATE_RAW_ERROR", str(ctx.exception))


class IdempotencyTests(unittest.TestCase):
    def test_identical_reuses_changed_rejects(self):
        client = Mock()
        client.label.return_value = {"shipment_id": "TEST123"}
        cache = LabelCache()
        data = fixture()
        self.assertEqual(cache.create(client, data), cache.create(client, data))
        client.label.assert_called_once()
        data["packages"][0]["weight"] = 4
        with self.assertRaises(PilotError) as ctx:
            cache.create(client, data)
        self.assertEqual(ctx.exception.code, "idempotency_conflict")

    def test_uncertain_failure_never_retries(self):
        client = Mock()
        client.label.side_effect = PilotError("ups_unavailable", "Synthetic timeout", 502)
        cache = LabelCache()
        for expected in ("ups_unavailable", "shipment_unconfirmed"):
            with self.assertRaises(PilotError) as ctx:
                cache.create(client, fixture())
            self.assertEqual(ctx.exception.code, expected)
        client.label.assert_called_once()

    def test_explicit_confirmation_before_api(self):
        client = Mock()
        data = fixture()
        data["confirm"] = "true"
        with self.assertRaises(PilotError):
            LabelCache().create(client, data)
        client.label.assert_not_called()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = Mock()
        cls.client.status.return_value = {"configured": False, "environment": "CIE", "live_enabled": False}
        cls.client.test_connection.return_value = {"connected": True, "environment": "CIE"}
        cls.server = PilotServer(0, cls.client)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, headers=None, body=b"{}"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        defaults = {"Origin": f"http://127.0.0.1:{self.server.server_port}", "Content-Type": "application/json"}
        defaults.update(headers or {})
        conn.request(method, path, body=body, headers=defaults)
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def test_same_origin_connection(self):
        self.assertEqual(self.request("POST", "/api/test-connection")[0], 200)

    def test_dns_rebinding_and_cross_origin_blocked(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}, {"Origin": "null"}):
            self.assertEqual(self.request("POST", "/api/test-connection", headers)[0], 403)

    def test_json_and_size_and_path_guard(self):
        self.assertEqual(self.request("POST", "/api/test-connection", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/test-connection", body=b"x" * 65537)[0], 413)
        self.assertEqual(self.request("GET", "/../ups_client.py")[0], 404)
        self.assertEqual(self.request("POST", "/api/test-connection", body=b"[]")[0], 400)

    def test_no_store_and_no_credentials(self):
        status, body, headers = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self';", headers["Content-Security-Policy"])

    def test_root_query_prefill_and_strict_api_paths(self):
        with patch.object(Path, "read_bytes", return_value=b"<!doctype html>"):
            self.assertEqual(self.request("GET", "/?length=12&weight=4")[0], 200)
        self.assertEqual(self.request("GET", "/api/status?extra=1")[0], 404)


if __name__ == "__main__":
    unittest.main()
