"""Media derivers: turn non-text bytes into text plus metadata.

Every deriver's output is stamped with provenance and memoised on
(content_hash, derivation_hash) so nothing is derived twice and nothing is
written without attribution.
"""
