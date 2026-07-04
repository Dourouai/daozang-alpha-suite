#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOWNLOAD_DIR="${ROOT_DIR}/data/downloads"
TARGET_DIR="${DAOZANG_QLIB_PROVIDER_URI:-${ROOT_DIR}/data/qlib/cn_data}"
ARCHIVE_PATH="${DOWNLOAD_DIR}/qlib_bin.tar.gz"
DOWNLOAD_URL="https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"

mkdir -p "${DOWNLOAD_DIR}"
mkdir -p "${TARGET_DIR}"

echo "Downloading Qlib CN data archive..."
echo "URL: ${DOWNLOAD_URL}"
echo "Archive: ${ARCHIVE_PATH}"
curl -L -f -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"

echo "Extracting to ${TARGET_DIR}..."
tar -zxf "${ARCHIVE_PATH}" -C "${TARGET_DIR}" --strip-components=1

echo
echo "Done."
echo "Next:"
echo "  cd ${ROOT_DIR}"
echo "  PYTHONPATH=src python3 -m daozang_alpha doctor"
