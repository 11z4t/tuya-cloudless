#!/bin/bash
# Create and publish a new GitHub release for tuya-cloudless.
# Usage: ./scripts/release.sh v0.3.0
set -euo pipefail

TAG="${1:?Usage: $0 <tag>  e.g. v0.3.0}"
REPO="11z4t/tuya-cloudless"

# Verify tag matches manifest version
MANIFEST_VERSION=$(python3 -c "import json; print('v' + json.load(open('custom_components/tuya_cloudless/manifest.json'))['version'])")
if [ "$TAG" != "$MANIFEST_VERSION" ]; then
    echo "ERROR: tag $TAG does not match manifest version $MANIFEST_VERSION"
    exit 1
fi

# Create and push git tag
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Tag $TAG already exists locally"
else
    git tag "$TAG"
fi
git push github "$TAG"

# Generate changelog from git log since last tag
PREV=$(git tag --sort=-version:refname | grep '^v' | grep -v "^${TAG}$" | head -1)
PREV="${PREV:-$(git rev-list --max-parents=0 HEAD)}"
CHANGELOG=$(git log "${PREV}..${TAG}" --pretty=format:"- %s" --no-merges 2>/dev/null | grep -v "^- Merge" || echo "- Release ${TAG}")
VERSION="${TAG#v}"

NOTES="## Tuya Cloudless ${TAG}

### Changes since ${PREV}

${CHANGELOG}

### Installation
Install via [HACS](https://hacs.xyz) → Custom repositories → \`https://github.com/${REPO}\`

Full changelog: [CHANGELOG.md](https://github.com/${REPO}/blob/main/CHANGELOG.md)"

# Create published release
gh release create "$TAG" \
    --repo "$REPO" \
    --title "$TAG" \
    --notes "$NOTES" \
    --latest

echo "Done: https://github.com/${REPO}/releases/tag/${TAG}"
