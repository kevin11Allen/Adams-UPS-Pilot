# UPS shipping pilot

A standalone local page for US domestic parcels: enter box dimensions, gross weight and addresses, retrieve UPS test rates, then create and print a test label. It is not yet connected to Odoo. All carrier calls use UPS CIE (Customer Integration Environment); production endpoints are not implemented.

## Technology and files

| Component | Language / files |
|---|---|
| Backend and local web server | Python 3.10+ · `server.py` |
| UPS OAuth, rating and shipping | Python · `ups_client.py` |
| Browser interface | HTML/CSS · `index.html`; plain JavaScript · `app.js` |
| Automated tests | Python unittest · `test_ups.py` |

Only the Python standard library and a modern browser are needed. There is no pip/npm installation, Node.js runtime or database requirement. Internet access is needed for UPS calls; the page and mocked tests run without it.

## Start

The commands below run from the extracted package root, containing `README.md` and the `shipping_pilot` directory:

```sh
python3 shipping_pilot/server.py --port 8765
```

On Windows:

```powershell
py -3 shipping_pilot/server.py --port 8765
```

Open **http://127.0.0.1:8765/** on the same computer. The terminal remains open while the app runs; Ctrl+C stops it. Opening `index.html` directly does not start the backend. The exact `127.0.0.1` host is used for origin validation.

Without credentials the form is available, but UPS calls report that configuration is missing.

## UPS configuration

`shipping_pilot/UPS_CONFIG.example.json` is a template. A copy is kept outside the package/repository as a private `config.json`, with the placeholder values replaced:

- `client_id`, `client_secret`: credentials from the UPS Developer application with OAuth, Rating and Shipping access.
- `shipper_number`: the Adams UPS shipping account number.
- `origin`: optional ship-from defaults. Country/state use two-letter codes. Placeholder address values are replaced with real details or the optional origin block can be omitted.

No credentials or customer accounts are included in this package. Access tokens are obtained automatically by the backend and kept in memory.

macOS/Linux (replace the example path):

```sh
export UPS_CONFIG_PATH="/absolute/private/path/config.json"
python3 shipping_pilot/server.py --port 8765
```

PowerShell (replace the example path):

```powershell
$env:UPS_CONFIG_PATH = "C:\private\config.json"
py -3 shipping_pilot/server.py --port 8765
```

The configuration file is server-side and is not served to the browser. `UPS_CA_BUNDLE` optionally selects an existing trusted CA bundle. On the tested Mac, `export UPS_CA_BUNDLE=/etc/ssl/cert.pem` resolved Python's certificate-chain error. Other systems may use a different trusted bundle. TLS verification stays enabled.

`run-local.command` is an optional macOS launcher with a default path specific to the original workspace. The explicit commands above are the portable startup method.

## Use

1. **Test UPS connection** confirms authentication.
2. Enter outside box dimensions and packaged gross weight, including their units.
3. Complete the origin and destination addresses. Phone fields are also required by the UI for label creation.
4. Optionally enter the customer UPS account and its billing details.
5. **Get UPS test rates**, select a service, confirm the shipment details, then **Create test label**.
6. **Print test label** opens the browser print dialog.

An empty customer account assigns transportation charges to Adams (`BillShipper`). A supplied account uses `BillReceiver` when owned by the delivery recipient or `BillThirdParty` when owned by a different customer. Registered billing postal code and, for third-party billing, country are passed to UPS. Rejected customer billing does not automatically fall back to Adams. Negotiated-rate visibility is separate from permission to bill the customer account.

Rates and labels from CIE are test outputs. Actual customer billing remains unverified. The pilot covers US-to-US parcels, not Germany origins, customs, duties/taxes, pallet freight or pickup booking. The UI handles one box; the backend accepts package arrays.

## Technical notes

API routes: `GET /api/status`; JSON `POST /api/test-connection`, `/api/rates`, `/api/label`. The browser supplies the same-origin header. Label creation requires confirmation and an idempotency key.

The label duplicate guard is process-memory only. It does not survive restart or a new key. Persistent shipment records and uncertain-result reconciliation remain part of the future Odoo integration. Reprinting an existing label is separate from creating another shipment.

Numeric prefilling is available, for example `/?length=12&width=8&height=6&weight=3&dimension_unit=IN&weight_unit=LBS`. This supplies form inputs, not an authenticated Odoo shipment link.

## Tests

From the same extracted package root:

```sh
python3 -m unittest discover -s shipping_pilot -p 'test_ups.py' -v
```

Windows uses `py -3 -m unittest` with the same remaining arguments. Tests use mocked UPS responses and local loopback HTTP; they need no credentials and create no carrier shipments.

References: [UPS OAuth](https://github.com/UPS-API/api-documentation/blob/main/OAuthClientCredentials.yaml), [Rating](https://github.com/UPS-API/api-documentation/blob/main/Rating.yaml), [Shipping](https://github.com/UPS-API/api-documentation/blob/main/Shipping.yaml).
