"""Utility helpers — lift placeholder.

# UNAVAILABLE_AT_LIFT_TIME: RAGFlow's common/data_source/utils.py is 1284
# lines with heavy module-level imports (boto3, botocore, chardet,
# googleapiclient, mypy_boto3_s3, slack_sdk, retry) that aren't part of
# chunkshop core's dependency set and aren't pulled in by any of the
# placeholder per-connector extras at this stage.
#
# Per the SP-2 plan (Task 2 §"utils.py pruning"): "Lift ONLY the helpers
# that the verified-tier connectors actually reference (`blob`, `rss`,
# `github`, `gdrive`, `slack`). Grep for each helper name in those
# connector files; drop unreferenced ones."
#
# Tasks 4-8 (one per verified connector) are the natural lift points:
# each connector's lift will grep its source for `utils.` references,
# pull the specific helpers, and add them here behind the right
# optional-dep import guard. Until then this module is intentionally
# empty so `_base/` imports cleanly with only `pydantic` + `requests`
# present.
#
# See _PROVENANCE.md for the upstream commit SHA to lift from when
# resuming this work.
"""
