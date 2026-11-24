"""Decode selected public product nodes in the observed Nuxt/devalue payload."""

import json

from data import DataError


class ProductPayload:
    def __init__(self, text: str):
        self.nodes = json.loads(text)
        if not isinstance(self.nodes, list):
            raise DataError("Expected the observed Nuxt flattened array format")
        self.cache = {}

    def resolve(self, reference):
        if not isinstance(reference, int):
            raise DataError("Unexpected non-integer devalue reference")
        if reference == -1:
            return None
        if reference < 0 or reference >= len(self.nodes):
            raise DataError("Unsupported devalue sentinel/reference")
        if reference in self.cache:
            return self.cache[reference]
        value = self.nodes[reference]
        if isinstance(value, dict):
            result = {}
            self.cache[reference] = result
            result.update({key: self.resolve(ref) for key, ref in value.items()})
            return result
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                if value[0] in {
                    "Reactive",
                    "ShallowReactive",
                    "Ref",
                    "ShallowRef",
                    "Readonly",
                    "ShallowReadonly",
                }:
                    result = self.resolve(value[1])
                elif value[0] in {"EmptyRef", "EmptyShallowRef"}:
                    if len(value) != 2:
                        raise DataError("Invalid empty-ref wrapper shape")
                    encoded = self.resolve(value[1])
                    if not isinstance(encoded, str):
                        raise DataError(
                            "Expected an encoded string in empty-ref wrapper"
                        )
                    # Nuxt stores falsy ref values as strings, referenced by index.
                    # Keep false, zero and empty string distinct from missing data.
                    if encoded == "_":
                        result = None
                    elif encoded == "0n":
                        result = 0
                    else:
                        try:
                            result = json.loads(encoded)
                        except ValueError as exc:
                            raise DataError("Invalid encoded empty-ref value") from exc
                elif value[0] == "Date":
                    result = value[1]
                else:
                    raise DataError(f"Unsupported devalue wrapper: {value[0]}")
                self.cache[reference] = result
                return result
            result = []
            self.cache[reference] = result
            result.extend(self.resolve(ref) for ref in value)
            return result
        self.cache[reference] = value
        return value

    def named(self, key: str):
        # Only product-scoped keys are accessible through this API. Never decode
        # the root payload or the productUser/account stores into diagnostics.
        prefixes = (
            "product:details:",
            "productOffers:",
            "$sproduct:media:assets:",
            "$sproduct:category:",
        )
        if not key.startswith(prefixes):
            raise DataError("Only allowlisted product-scoped payload nodes may be read")
        refs = {
            node[key]
            for node in self.nodes
            if isinstance(node, dict) and key in node and node[key] >= 0
        }
        if len(refs) != 1:
            raise DataError(f"Missing or ambiguous product payload key: {key}")
        return self.resolve(refs.pop())
