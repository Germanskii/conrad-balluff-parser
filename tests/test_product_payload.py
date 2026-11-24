"""Synthetic public product nodes; no browser sessions or copied full payloads."""

import json
import unittest
from unittest.mock import Mock, patch

from data import DataError
from nuxt_data import ProductPayload
from site_adapter import (
    price_fields,
    public_offer,
    public_url,
    read_consistent_offer_state,
)


class ProductPayloadTests(unittest.TestCase):
    def test_references_wrappers_duplicates_and_undefined_error_entry(self):
        nodes = [
            {"product:details:0012345": 1},
            ["Reactive", 2],
            {"ids": 3, "optional": -1},
            [4, 4],
            "0012345",
            {"product:details:0012345": -1},
        ]
        result = ProductPayload(json.dumps(nodes)).named("product:details:0012345")
        self.assertEqual(result, {"ids": ["0012345", "0012345"], "optional": None})

    def test_account_nodes_and_unsupported_formats_are_rejected(self):
        with self.assertRaises(DataError):
            ProductPayload('[{"productUser":1},"synthetic-private"]').named(
                "productUser"
            )
        with self.assertRaises(DataError):
            ProductPayload('[{"product:details:1":1},["Unknown",2],"x"]').named(
                "product:details:1"
            )

    def test_empty_ref_variants_preserve_falsy_values(self):
        for wrapper in ("EmptyRef", "EmptyShallowRef"):
            for encoded, expected in [
                ("_", None),
                ("null", None),
                ("false", False),
                ("0", 0),
                ('""', ""),
                ("0n", 0),
            ]:
                with self.subTest(wrapper=wrapper, encoded=encoded):
                    nodes = [
                        {"product:details:1": 1},
                        {"optional": 2, "again": 2},
                        [wrapper, 3],
                        encoded,
                    ]
                    result = ProductPayload(json.dumps(nodes)).named(
                        "product:details:1"
                    )
                    for key in ("optional", "again"):
                        self.assertEqual(result[key], expected)
                        self.assertIs(type(result[key]), type(expected))

    def test_empty_ref_payload_is_a_node_reference(self):
        # The 0 payload points at node zero; it is not a literal numeric zero.
        nodes = ['""', {"product:details:1": 2}, ["EmptyRef", 0]]
        self.assertEqual(
            ProductPayload(json.dumps(nodes)).named("product:details:1"), ""
        )

    def test_malformed_empty_refs_fail_instead_of_becoming_missing_data(self):
        for wrapper in ("EmptyRef", "EmptyShallowRef"):
            for value, payload in [
                ([wrapper], "_"),
                ([wrapper, 2], False),
                ([wrapper, 2], "invalid-json"),
                ([wrapper, -1], "_"),
            ]:
                with self.subTest(wrapper=wrapper, value=value, payload=payload):
                    nodes = [{"product:details:1": 1}, value, payload]
                    with self.assertRaises(DataError):
                        ProductPayload(json.dumps(nodes)).named("product:details:1")

    def test_live_price_context_waits_for_quantity_and_total(self):
        page = Mock()

        def snapshot(quantity, embedded, total="9,00 €"):
            return {
                "quantity": quantity,
                "total": total,
                "tax": "inkl. MwSt.",
                "state": {
                    "quantities": {"quantityAtLastTotalPrice": embedded},
                    "currentOffer": {"price": {"totalPrice": 9, "taxSchema": "GROSS"}},
                },
            }

        page.evaluate.side_effect = [
            snapshot("3", None),
            snapshot("3", 3, "3,00 €"),
            snapshot("3", 3),
        ]
        with patch("site_adapter.check_security"):
            state, quantity, total = read_consistent_offer_state(
                page, "123", attempts=3
            )
        self.assertEqual(quantity, "3")
        self.assertEqual(state["quantities"]["quantityAtLastTotalPrice"], 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    def test_tax_label_must_be_ready_before_accepting_price(self):
        page = Mock()
        state = {
            "quantities": {"quantityAtLastTotalPrice": 2},
            "currentOffer": {"price": {"totalPrice": 9, "taxSchema": "GROSS"}},
        }
        page.evaluate.side_effect = [
            {"quantity": "2", "total": "9,00 €", "tax": "", "state": state},
            {"quantity": "2", "total": "9,00 €", "tax": "zzgl. MwSt.", "state": state},
            {"quantity": "2", "total": "9,00 €", "tax": "inkl. MwSt.", "state": state},
        ]
        with patch("site_adapter.check_security"):
            result = read_consistent_offer_state(page, "123", attempts=3)
        self.assertEqual(result[1], "2")
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    def test_live_quantity_mismatch_is_bounded_and_never_defaults(self):
        for embedded in (None, True, "3", 1):
            page = Mock()
            page.evaluate.return_value = {
                "quantity": "3",
                "total": "9,00 €",
                "state": {
                    "quantities": {"quantityAtLastTotalPrice": embedded},
                    "currentOffer": {"price": {"totalPrice": 9, "taxSchema": "GROSS"}},
                },
            }
            with patch("site_adapter.check_security"), self.assertRaises(DataError):
                read_consistent_offer_state(page, "123", attempts=2)
            self.assertEqual(page.evaluate.call_count, 2)

    def test_offer_keeps_tax_scale_savings_and_explicit_exclusion(self):
        source = {
            "productId": "0000012345",
            "offerId": "000007",
            "sellerId": "0008",
            "sellerName": "Synthetic seller",
            "exclusionStatus": "EXCLUDED_NON_BUSINESS_CUSTOMER_GROUP",
            "minimumOrderQuantity": 2,
            "multiplier": 2,
            "price": {
                "currency": "EUR",
                "taxSchema": "GROSS",
                "vatPercentage": 19,
                "unitPrice": 12.34,
                "totalPrice": 24.68,
                "priceScales": [
                    {
                        "scaleFrom": 10,
                        "pricePerUnit": 11.01,
                        "savedAmount": 13.3,
                        "savedPercentage": 11,
                    }
                ],
            },
            "availability": {"stockQuantity": 20},
        }
        offer = public_offer(source, "12345", "2")
        self.assertEqual(offer["offer_id"], "000007")
        self.assertEqual(offer["exclusion_status"], source["exclusionStatus"])
        self.assertIsNone(offer["shipper"])
        self.assertEqual(offer["price"]["priceScales"][0]["savedAmount"], "13.3")
        self.assertEqual(offer["price"]["taxSchema"], "GROSS")
        with self.assertRaises(DataError):
            public_offer(source, "99999", "2")

    def test_unreviewed_url_queries_are_never_exported(self):
        self.assertEqual(
            public_url("https://asset.conrad.com/synthetic.pdf"),
            "https://asset.conrad.com/synthetic.pdf",
        )
        for url in [
            "https://asset.conrad.com/synthetic.pdf?token=synthetic",
            "https://example.org/test.pdf",
        ]:
            with self.assertRaises(DataError):
                public_url(url)


if __name__ == "__main__":
    unittest.main()
