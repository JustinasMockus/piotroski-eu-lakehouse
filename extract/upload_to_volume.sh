#!/usr/bin/env bash
set -euo pipefail

CATALOG="${1:-piotroski_dev}"
SOURCE="${2:-}"   # optional: one source folder, e.g. prices; default is everything
databricks fs cp -r --overwrite "data/landing${SOURCE:+/$SOURCE}" \
  "dbfs:/Volumes/${CATALOG}/bronze/landing${SOURCE:+/$SOURCE}"
