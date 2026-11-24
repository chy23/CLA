#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 scripts/build_data.py
