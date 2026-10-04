#!/bin/bash
# FINK-G3A transfer wrapper. Runs the repository-generated command from release 1bb8e10.
# cwd is this evidence directory because finkctl --dump_schemas writes schema files to cwd.
set -o pipefail
umask 077
export PATH=/astro/store/shiren/mdarim/envs/fink-lsst-py311/bin:$PATH
export FINK_LSST_DATA_ROOT=/astro/store/shire/FINK
cd /astro/users/mdarim/opt/fink-lsst-analysis/releases/1bb8e109f4205ba963f0e8d41ec99ce439eb4f13
CMD=$(PYTHONPATH=src python scripts/print_data_transfer_download_command.py --run-config configs/runs/g3a_full_night_light_static_2026-02-25.yaml --nconsumers 4) || exit 90
LOG_FILE=$(PYTHONPATH=src python -c "from fink_lsst.bulk_transfer.topic_registry import transfer_log_path; print(transfer_log_path('/astro/store/shire/FINK', 'ftransfer_lsst_2026-10-04_177446'))") || exit 91
cd /astro/store/shire/FINK/manifests/ftransfer_lsst_2026-10-04_177446
{ echo "# G3A start_utc=$(date -u +%FT%TZ) release=1bb8e109f4205ba963f0e8d41ec99ce439eb4f13 cwd=$PWD"; echo "# command: $CMD"; } | tee -a "$LOG_FILE"
$CMD 2>&1 | tee -a "$LOG_FILE"
RC=${PIPESTATUS[0]}
echo "# G3A end_utc=$(date -u +%FT%TZ) exit_code=$RC" | tee -a "$LOG_FILE"
echo "$RC $(date -u +%FT%TZ)" > /astro/store/shire/FINK/manifests/ftransfer_lsst_2026-10-04_177446/transfer_exit.txt
