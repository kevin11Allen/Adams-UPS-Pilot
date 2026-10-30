"""UPS CIE REST adapter. No production host or shipment retry is supported.

Payload contracts: UPS-API/api-documentation/{Shipping,Rating,
OAuthClientCredentials}.yaml, v2409. Credentials remain server-side.
"""
import base64
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CIE = "https://wwwcie.ups.com"
ADDRESS_FIELDS = ("name", "phone", "address1", "address2", "city", "state", "postal_code", "country")


class PilotError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def text(value, field, required=True, limit=100):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise PilotError("invalid_input", f"Invalid {field}.")
    value = value.strip()
    if required and not value:
        raise PilotError("invalid_input", f"Required: {field}.")
    return value


def address(value, field):
    if not isinstance(value, dict):
        raise PilotError("invalid_input", f"Required: {field} address.")
    result = {k: text(value.get(k, ""), f"{field}.{k}", k in ("name", "address1", "city", "postal_code", "country"), 100) for k in ADDRESS_FIELDS}
    result["country"] = result["country"].upper()
    result["state"] = result["state"].upper()
    if not re.fullmatch(r"[A-Z]{2}", result["country"]):
        raise PilotError("invalid_input", f"{field}.country requires a two-letter country code.")
    if result["country"] in ("US", "CA") and not re.fullmatch(r"[A-Z]{2}", result["state"]):
        raise PilotError("invalid_input", f"{field}.state requires a two-letter state/province.")
    return result


def validate(data):
    if not isinstance(data, dict):
        raise PilotError("invalid_input", "JSON object required.")
    result = {k: address(data.get(k), k) for k in ("origin", "destination")}
    # This pilot is domestic US parcel shipping only: international customs data
    # and pallet/freight flows have not been implemented.
    if any(result[k]["country"] != "US" for k in ("origin", "destination")):
        raise PilotError("unsupported_route", "This CIE pilot supports US-to-US parcels only.")
    packages = data.get("packages")
    if not isinstance(packages, list) or not 1 <= len(packages) <= 50:
        raise PilotError("invalid_input", "Provide 1–50 packages.")
    result["packages"] = []
    for package in packages:
        if not isinstance(package, dict):
            raise PilotError("invalid_input", "Invalid package.")
        p = {}
        for key in ("length", "width", "height", "weight"):
            value = package.get(key)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise PilotError("invalid_input", f"Invalid package {key}.")
            try:
                number = float(value)
            except (ValueError, OverflowError):
                raise PilotError("invalid_input", f"Invalid package {key}.") from None
            if not math.isfinite(number) or not 0 < number <= 10000:
                raise PilotError("invalid_input", f"Package {key} must be positive and finite (maximum 10000).")
            p[key] = format(number, ".10g")
        for key, choices in (("dimension_unit", ("IN", "CM")), ("weight_unit", ("LBS", "KGS"))):
            if package.get(key) not in choices:
                raise PilotError("invalid_input", f"Invalid {key}.")
            p[key] = package[key]
        result["packages"].append(p)
    result["service_code"] = text(data.get("service_code", "03"), "service_code", limit=3)
    if not re.fullmatch(r"\d{2,3}", result["service_code"]):
        raise PilotError("invalid_input", "Invalid service code.")
    result["customer_account"] = text(data.get("customer_account", ""), "customer_account", False, 6).upper()
    result["payer_type"] = "shipper"
    result["billing_postal_code"] = ""
    result["billing_country"] = ""
    if result["customer_account"]:
        if not re.fullmatch(r"[A-Z0-9]{6}", result["customer_account"]):
            raise PilotError("invalid_input", "Customer UPS account must contain six letters/digits.")
        if data.get("payer_type") not in ("receiver", "third_party"):
            raise PilotError("invalid_input", "Choose receiver or third_party for the customer account.")
        result["payer_type"] = data["payer_type"]
        # Recipient postal code must be the account's registered pickup postal,
        # not inferred from destination. Require it conservatively in this US pilot.
        result["billing_postal_code"] = text(data.get("billing_postal_code", ""), "billing_postal_code", limit=16)
        if result["payer_type"] == "third_party":
            result["billing_country"] = text(data.get("billing_country", ""), "billing_country", limit=2).upper()
            if not re.fullmatch(r"[A-Z]{2}", result["billing_country"]):
                raise PilotError("invalid_input", "Billing country requires a two-letter code.")
    if not isinstance(data.get("negotiated_rates", False), bool):
        raise PilotError("invalid_input", "negotiated_rates must be boolean.")
    result["negotiated_rates"] = data.get("negotiated_rates", False)
    return result


def payment(data, shipper_number):
    charge = {"Type": "01"}  # Transportation only. No duty/tax payment.
    if not data["customer_account"]:
        charge["BillShipper"] = {"AccountNumber": shipper_number}
    else:
        payer = {"AccountNumber": data["customer_account"], "Address": {"PostalCode": data["billing_postal_code"]}}
        if data["payer_type"] == "third_party":
            payer["Address"]["CountryCode"] = data["billing_country"]
            charge["BillThirdParty"] = payer
        else:
            charge["BillReceiver"] = payer
    return {"ShipmentCharge": [charge]}


def party(data):
    result = {"Name": data["name"], "Address": {"AddressLine": [data["address1"]] + ([data["address2"]] if data["address2"] else []), "City": data["city"], "PostalCode": data["postal_code"], "CountryCode": data["country"]}}
    if data["state"]:
        result["Address"]["StateProvinceCode"] = data["state"]
    if data["phone"]:
        result["Phone"] = {"Number": data["phone"]}
    return result


def shipment(data, shipper_number, label=False):
    shipper = party(data["origin"])
    shipper["ShipperNumber"] = shipper_number
    result = {"Shipper": shipper, "ShipFrom": party(data["origin"]), "ShipTo": party(data["destination"]), "Package": [], "PaymentInformation" if label else "PaymentDetails": payment(data, shipper_number)}
    for p in data["packages"]:
        result["Package"].append({"Packaging" if label else "PackagingType": {"Code": "02"}, "Dimensions": {"UnitOfMeasurement": {"Code": p["dimension_unit"]}, "Length": p["length"], "Width": p["width"], "Height": p["height"]}, "PackageWeight": {"UnitOfMeasurement": {"Code": p["weight_unit"]}, "Weight": p["weight"]}})
    if label:
        result["Description"] = "Local CIE parcel test"
        result["Service"] = {"Code": data["service_code"]}
    if data["negotiated_rates"]:
        result["ShipmentRatingOptions"] = {"NegotiatedRatesIndicator": "Y"}
        if data["customer_account"]:
            result["ShipmentRatingOptions"]["TPFCNegotiatedRatesIndicator"] = "Y"
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UPSClient:
    def __init__(self, config_path=None):
        self.config_path = config_path or os.environ.get("UPS_CONFIG_PATH", "")
        self._token, self._expires = None, 0
        self._token_lock = threading.Lock()
        self.connected = False
        # Explicit trust bundle supports local Python installs lacking a system
        # CA path. TLS verification always remains enabled.
        tls = ssl.create_default_context(cafile=os.environ.get("UPS_CA_BUNDLE") or None)
        self._opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=tls))

    def config(self):
        try:
            if not self.config_path:
                raise ValueError()
            config = json.loads(Path(self.config_path).read_text())
            if not isinstance(config, dict) or not all(isinstance(config.get(k), str) and config[k].strip() for k in ("client_id", "client_secret", "shipper_number")):
                raise ValueError()
            if not re.fullmatch(r"[A-Za-z0-9]{6}", config["shipper_number"]):
                raise ValueError()
            return config
        except (OSError, ValueError, TypeError):
            raise PilotError("not_configured", "UPS CIE credentials are not configured correctly.", 503) from None

    def status(self):
        result = {"configured": False, "connected": self.connected, "environment": "CIE", "live_enabled": False, "defaults": {"origin": {}}}
        try:
            cfg = self.config()
            result["configured"] = True
            origin = cfg.get("origin", {})
            if isinstance(origin, dict):
                result["defaults"]["origin"] = {k: v for k, v in origin.items() if k in ADDRESS_FIELDS and isinstance(v, str) and len(v) <= 100 and not any(ord(c) < 32 for c in v)}
        except PilotError:
            pass
        return result

    def _request(self, path, body, headers):
        request = urllib.request.Request(CIE + path, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=25) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except urllib.error.HTTPError as exc:
            # Never forward UPS raw error messages, bodies, headers or credentials.
            status = exc.code
            codes = []
            try:
                error_body = json.loads(exc.read(65536))
                errors = error_body.get("response", {}).get("errors", [])
                if isinstance(errors, list):
                    codes = [item["code"] for item in errors[:3] if isinstance(item, dict) and isinstance(item.get("code"), str) and re.fullmatch(r"\d{5,7}", item["code"])]
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            exc.close()
            diagnostic = f"HTTP {status}" + ("; UPS code " + ", ".join(codes) if codes else "")
            raise PilotError("ups_rejected", f"UPS CIE rejected this request ({diagnostic}). Verify account permissions, addresses and package/billing details.", 502) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise PilotError("ups_unavailable", "UPS CIE response could not be confirmed. Shipment requests are not retried.", 502) from None

    def token(self):
        with self._token_lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            cfg = self.config()
            basic = base64.b64encode((cfg["client_id"] + ":" + cfg["client_secret"]).encode()).decode()
            reply = self._request("/security/v1/oauth/token", b"grant_type=client_credentials", {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
            token = reply.get("access_token")
            if not isinstance(token, str) or not token or len(token) > 32768 or any(ord(c) < 32 for c in token):
                raise PilotError("ups_response", "UPS CIE did not return a valid access token.", 502)
            try:
                expires = max(0, min(float(reply.get("expires_in", 0)), 86400) - 60)
            except (ValueError, TypeError):
                expires = 0
            self._token, self._expires, self.connected = token, time.monotonic() + expires, True
            return token

    def test_connection(self):
        self.token()
        return {"connected": True, "environment": "CIE"}

    def _api(self, path, payload):
        return self._request(path, json.dumps(payload).encode(), {"Authorization": "Bearer " + self.token(), "Content-Type": "application/json", "transId": "local-cie-pilot", "transactionSrc": "AdamsLocalPilot"})

    def rates(self, data):
        payload = {"RateRequest": {"Request": {"TransactionReference": {"CustomerContext": "Local CIE pilot"}}, "Shipment": shipment(data, self.config()["shipper_number"])}}
        reply = self._api("/api/rating/v2409/Shop", payload)
        try:
            rated = reply["RateResponse"]["RatedShipment"]
            if isinstance(rated, dict):
                rated = [rated]
            result = []
            for item in rated:
                negotiated = item.get("NegotiatedRateCharges", {}).get("TotalCharge")
                charges = negotiated or item["TotalCharges"]
                amount = float(charges["MonetaryValue"])
                currency = charges["CurrencyCode"]
                service = item["Service"]["Code"]
                if not math.isfinite(amount) or amount < 0 or not re.fullmatch(r"[A-Z]{3}", currency) or not re.fullmatch(r"\d{2,3}", service):
                    raise ValueError()
                result.append({"service_code": service, "total": str(charges["MonetaryValue"]), "currency": currency, "rate_type": "negotiated" if negotiated else "published"})
            if not result:
                raise ValueError()
            return {"rates": result, "environment": "CIE"}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PilotError("ups_response", "UPS CIE returned no usable rates.", 502) from None

    def label(self, data):
        if not data["origin"]["phone"]:
            raise PilotError("invalid_input", "Origin phone is required to request a CIE label.")
        payload = {"ShipmentRequest": {"Request": {"RequestOption": "validate"}, "Shipment": shipment(data, self.config()["shipper_number"], True), "LabelSpecification": {"LabelImageFormat": {"Code": "GIF"}, "HTTPUserAgent": "Mozilla/4.5", "LabelStockSize": {"Height": "6", "Width": "4"}}}}
        reply = self._api("/api/shipments/v2409/ship", payload)
        try:
            result = reply["ShipmentResponse"]["ShipmentResults"]
            packages = result["PackageResults"]
            if isinstance(packages, dict):
                packages = [packages]
            labels = []
            for package in packages:
                graphic = package["ShippingLabel"]["GraphicImage"]
                decoded = base64.b64decode(graphic, validate=True)
                tracking = package["TrackingNumber"]
                if not decoded.startswith((b"GIF87a", b"GIF89a")) or not re.fullmatch(r"[A-Za-z0-9]{1,40}", tracking):
                    raise ValueError()
                labels.append({"format": "GIF", "data_base64": graphic, "tracking_number": tracking})
            shipment_id = result["ShipmentIdentificationNumber"]
            if len(labels) != len(data["packages"]) or not re.fullmatch(r"[A-Za-z0-9]{1,40}", shipment_id):
                raise ValueError()
            return {"labels": labels, "shipment_id": shipment_id, "environment": "CIE"}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PilotError("ups_response", "CIE label response could not be confirmed. Do not repeat this shipment with a new key.", 502) from None
