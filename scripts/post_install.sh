#!/usr/bin/env bash
# Post-install fixup for the synthetic document generator environment.
#
# Run this after EVERY `conda env create` or `conda env update`.
#
# Why it exists: `augraphy` declares `opencv-python` (the full GUI build) as a
# hard dependency, so conda/pip installs it and it displaces the
# `opencv-python-headless` build pinned in environment.yml -- both provide
# `cv2`, and the last one installed wins. On a locked-down host the GUI build
# also drags in system libraries that may not be present.
#
# A conda YAML cannot express a per-package `--no-deps`, so the fix has to live
# here. Idempotent: safe to re-run.
#
# Usage:
#     bash scripts/post_install.sh                # uses the active env
#     CONDA_ENV=synthetic bash scripts/post_install.sh

set -euo pipefail

AUGRAPHY_VERSION="8.2.6"
HEADLESS_VERSION="4.13.0.92"

if [[ -n "${CONDA_ENV:-}" ]]; then
    PIP=(conda run -n "${CONDA_ENV}" pip)
    PY=(conda run -n "${CONDA_ENV}" python)
    echo "Target environment: ${CONDA_ENV}"
else
    if [[ -z "${CONDA_DEFAULT_ENV:-}" ]]; then
        echo "ERROR: no conda environment is active." >&2
        echo "  Fix:  conda activate synthetic" >&2
        echo "  Or:   CONDA_ENV=synthetic bash scripts/post_install.sh" >&2
        exit 1
    fi
    PIP=(pip)
    PY=(python)
    echo "Target environment: ${CONDA_DEFAULT_ENV} (active)"
fi

# 1. Remove the full GUI OpenCV if conda/pip pulled it in as an augraphy dep.
if "${PIP[@]}" show opencv-python >/dev/null 2>&1; then
    echo "Removing the full GUI opencv-python (it displaces the headless build)..."
    "${PIP[@]}" uninstall -y opencv-python
else
    echo "Full opencv-python not present -- nothing to remove."
fi

# 2. Make sure the headless build is intact; uninstalling its GUI sibling can
#    leave the shared cv2 files in an inconsistent state.
echo "Ensuring opencv-python-headless==${HEADLESS_VERSION} is intact..."
"${PIP[@]}" install --quiet --no-deps --force-reinstall \
    "opencv-python-headless==${HEADLESS_VERSION}"

# 3. Reinstall augraphy without deps, so it never re-pulls the GUI build.
echo "Reinstalling augraphy==${AUGRAPHY_VERSION} with --no-deps..."
"${PIP[@]}" install --quiet --no-deps --force-reinstall "augraphy==${AUGRAPHY_VERSION}"

# 4. Verify, and fail loudly rather than leaving a subtly broken env.
echo
echo "Verifying..."
if "${PIP[@]}" list 2>/dev/null | grep -qiE "^opencv-python[[:space:]]"; then
    echo "FAILED: the full opencv-python is still installed." >&2
    echo "  Fix:  pip uninstall -y opencv-python && bash scripts/post_install.sh" >&2
    exit 1
fi

# Passed with -c rather than on stdin: `conda run` does not forward stdin, so a
# heredoc here would silently execute nothing and the check would always pass.
"${PY[@]}" -c '
import sys, cv2, augraphy, numpy
pkg = cv2.__file__.split("site-packages/")[-1].split("/")[0]
print(f"  cv2       {cv2.__version__}  ({pkg})")
print(f"  augraphy  {augraphy.__version__}")
print(f"  numpy     {numpy.__version__}")
if tuple(int(p) for p in numpy.__version__.split(".")[:2]) > (2, 4):
    sys.exit(f"FAILED: numpy {numpy.__version__} exceeds numba ceiling 2.4. Fix: pip install numpy==2.3.5")
'

echo
echo "Environment OK."
