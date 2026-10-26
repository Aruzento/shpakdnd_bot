#!/usr/bin/env bash
# Sourced through BASH_ENV only in a top-level Actions run step. Child shells
# (including protected PTY fixtures) must retain their original environment.
unset BASH_ENV
DIAGNOSTICS="$RUNNER_TEMP/release-diagnostics"
mkdir -p "$DIAGNOSTICS"
STEP_LOG="$DIAGNOSTICS/step-$(date -u +%Y%m%dT%H%M%S)-$$.log"
STEP_BEGAN=$SECONDS
exec > >(tee -a "$STEP_LOG") 2>&1
printf 'BEGIN %s action=%s sha=%s\n' "$(date -u +%FT%TZ)" "$GITHUB_ACTION" "$(git rev-parse HEAD)"
trap 'step_code=$?; printf "END %s exit=%s seconds=%s\n" "$(date -u +%FT%TZ)" "$step_code" "$((SECONDS-STEP_BEGAN))"; printf "%s\t%s\t%s\t%s\n" "$GITHUB_ACTION" "$step_code" "$((SECONDS-STEP_BEGAN))" "$STEP_LOG" >> "$DIAGNOSTICS/timings.tsv"; exit "$step_code"' EXIT
