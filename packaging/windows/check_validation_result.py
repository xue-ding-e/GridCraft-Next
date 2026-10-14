#!/usr/bin/env python3
"""Require all independent source and Windows validation jobs to succeed.

This is an aggregation guard, not permission to publish a package. The native
job separately enforces runtime evidence and exact reviewed legal eligibility.
"""
import argparse
import sys


def failed_layers(linux, windows_tests, windows_native):
    return [name for name, outcome in (
        ("Linux complete quality gate", linux),
        ("Windows packaging script tests", windows_tests),
        ("Windows native and distribution gate", windows_native),
    ) if outcome != "success"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux", required=True)
    parser.add_argument("--windows-tests", required=True)
    parser.add_argument("--windows-native", required=True)
    args = parser.parse_args()
    failed = failed_layers(args.linux, args.windows_tests, args.windows_native)
    if failed:
        print("Validation is incomplete or failed: " + "; ".join(failed), file=sys.stderr)
        return 1
    print("All source and Windows validation jobs passed for this workflow commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
