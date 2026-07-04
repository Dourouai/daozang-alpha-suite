#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIGHTGBM_LIB="${ROOT_DIR}/.venv/lib/python3.12/site-packages/lightgbm/lib/lib_lightgbm.dylib"
SKLEARN_LIBOMP_DIR="${ROOT_DIR}/.venv/lib/python3.12/site-packages/sklearn/.dylibs"

if [[ ! -f "${LIGHTGBM_LIB}" ]]; then
  echo "LightGBM dylib not found: ${LIGHTGBM_LIB}" >&2
  exit 1
fi

if [[ ! -f "${SKLEARN_LIBOMP_DIR}/libomp.dylib" ]]; then
  echo "sklearn libomp.dylib not found: ${SKLEARN_LIBOMP_DIR}/libomp.dylib" >&2
  exit 1
fi

if otool -l "${LIGHTGBM_LIB}" | grep -q "${SKLEARN_LIBOMP_DIR}"; then
  echo "LightGBM already has sklearn libomp rpath."
  exit 0
fi

install_name_tool -add_rpath "${SKLEARN_LIBOMP_DIR}" "${LIGHTGBM_LIB}"
echo "Added LightGBM rpath: ${SKLEARN_LIBOMP_DIR}"
