"""A ULID generator, in about thirty lines of standard library.

The PHP models use ``HasUlids``; the Node twin takes a dependency on the ``ulid``
package. This package generates its own, and the reason is the rule the Python
ports run on: a third-party package is acceptable for genuinely generic
infrastructure and unacceptable for anything expressing Fancy's own data shapes
-- but a dependency is also a cost paid by every consumer forever, and this one
buys thirty lines.

The output is a real ULID and interoperable with both twins: 26 characters,
Crockford base32, 48 bits of millisecond timestamp followed by 80 bits of
randomness, lexicographically sortable by creation time.

**Monotonic within a millisecond is NOT implemented**, matching neither twin
particularly: PHP's ``HasUlids`` uses ``symfony/uid``, which is monotonic, and
the Node ``ulid`` package is monotonic only through its ``monotonicFactory``.
Ids generated in the same millisecond therefore sort arbitrarily among
themselves. That is fine for a catalog -- nothing here orders by id, and
``Product.order`` exists for display sequence -- and it is recorded because
"ULIDs sort by time" is the kind of assumption that gets built on.
"""

from __future__ import annotations

import secrets
import time

__all__ = ["ulid"]

#: Crockford base32: no I, L, O or U, so a ULID cannot be misread aloud or
#: accidentally contain a word.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIME_CHARS = 10
_RANDOM_CHARS = 16
_MAX_TIME = (1 << 48) - 1


def ulid(timestamp_ms: int | None = None) -> str:
    """A new ULID. Pass ``timestamp_ms`` only to make a test deterministic."""
    ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ms <= _MAX_TIME:
        raise ValueError(f"A ULID timestamp must fit in 48 bits; got {ms}.")

    value = (ms << 80) | secrets.randbits(80)
    return _encode(value, _TIME_CHARS + _RANDOM_CHARS)


def _encode(value: int, length: int) -> str:
    out = [""] * length
    for i in range(length - 1, -1, -1):
        out[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)
