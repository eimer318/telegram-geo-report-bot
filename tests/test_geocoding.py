import unittest
from unittest.mock import Mock, patch

from telegram_geo_report_bot.config import GeocodingConfig
from telegram_geo_report_bot.geocoding import NominatimClient


class GeocodingTests(unittest.TestCase):
    def _config(self, **updates):
        values = {
            "country_codes": ("de",),
            "viewbox": None,
            "bounded": False,
            "result_limit": 5,
            "allowed_states": (),
            "allowed_counties": (),
            "allowed_municipalities": (),
            "postcode_prefixes": (),
        }
        values.update(updates)
        return GeocodingConfig(**values)

    @patch("telegram_geo_report_bot.geocoding.requests.get")
    def test_regional_filters_are_combined_and_request_more_candidates(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "lat": "52.51",
                "lon": "13.40",
                "address": {
                    "road": "Beispielstraße",
                    "house_number": "1",
                    "postcode": "10115",
                    "city": "Berlin",
                    "state": "Berlin",
                    "county": "Berlin",
                    "country_code": "de",
                },
            },
            {
                "lat": "53.55",
                "lon": "9.99",
                "address": {
                    "road": "Beispielstraße",
                    "house_number": "1",
                    "postcode": "20095",
                    "city": "Hamburg",
                    "state": "Hamburg",
                    "country_code": "de",
                },
            },
        ]
        request_get.return_value = response
        client = NominatimClient(
            self._config(
                allowed_states=("Berlin",),
                allowed_municipalities=("Berlin",),
                postcode_prefixes=("10",),
            ),
            "GeoReportBot/1.0 contact@example.invalid",
        )

        results = client.search("Beispielstraße 1")

        self.assertEqual(1, len(results))
        self.assertEqual("Berlin", results[0]["address"]["state"])
        self.assertEqual("de", results[0]["address"]["country_code"])
        params = request_get.call_args.kwargs["params"]
        self.assertEqual("de", params["countrycodes"])
        self.assertEqual(40, params["limit"])

    @patch("telegram_geo_report_bot.geocoding.requests.get")
    def test_empty_region_lists_keep_countrywide_search(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        request_get.return_value = response
        client = NominatimClient(self._config(result_limit=7), "GeoReportBot/1.0")

        self.assertEqual([], client.search("Musterstraße 1"))
        self.assertEqual(7, request_get.call_args.kwargs["params"]["limit"])


if __name__ == "__main__":
    unittest.main()
