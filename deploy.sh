#!/data/data/com.termux/files/usr/bin/bash
# Butterfly Atlas — deploy helper for Termux (v2, version-aware).
#
# Files are handed over with a version in the name, e.g.  app-v13.js
# This script strips the "-v13" when placing the file, so:
#
#     app-v13.js    ->  js/app.js
#     style-v13.css ->  css/style.css
#     index-v13.html->  index.html
#     fetch_flickr-v13.py -> scraper/fetch_flickr.py
#
# Versioned names mean a stale download can never be picked up by mistake:
# every new file has a filename nothing else in Downloads shares.
#
# Usage:   ./deploy.sh app-v13.js style-v13.css
#          ./deploy.sh app.js            (unversioned still works)

set -u
REPO="$HOME/Butterfly-Atlas"
DL="$HOME/storage/downloads"
EXPECTED_REMOTE="Dusky-Nembrotha/Butterfly-Atlas"

say()  { printf '%s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ $# -ge 1 ] || fail "No files given. Example:  ./deploy.sh app-v13.js"
[ -d "$REPO/.git" ] || fail "No git repo at $REPO"
[ -d "$DL" ] || fail "Can't see $DL — run 'termux-setup-storage' and tap Allow."

cd "$REPO" || fail "Could not enter $REPO"

remote="$(git remote get-url origin 2>/dev/null || true)"
case "$remote" in
  *"$EXPECTED_REMOTE"*) say "Remote OK" ;;
  "") fail "No 'origin' remote set." ;;
  *) fail "WRONG repo: $remote
     Fix: git remote set-url origin https://github.com/$EXPECTED_REMOTE.git" ;;
esac

# ---- work out destination, stripping any -vNN version marker -------------
strip_version() {           # app-v13.js -> app.js
  printf '%s' "$1" | sed -E 's/-v[0-9]+(\.[A-Za-z0-9]+)$/\1/'
}
version_of() {              # app-v13.js -> 13   (empty if unversioned)
  printf '%s' "$1" | sed -nE 's/.*-v([0-9]+)\.[A-Za-z0-9]+$/\1/p'
}
destination_for() {
  case "$1" in
    *.js)   echo "js/$1" ;;
    *.css)  echo "css/$1" ;;
    *.html) echo "$1" ;;
    *.py)   echo "scraper/$1" ;;
    *)      echo "" ;;
  esac
}

copied=0
maxver=""
for f in "$@"; do
  src="$DL/$f"
  if [ ! -f "$src" ]; then
    say ""
    say "'$f' is not in Downloads. What IS there:"
    ls -t "$DL" 2>/dev/null | grep -Ei '\.(js|css|html|py)$' | sed 's/^/    /' | head -15
    fail "Nothing copied. Check the exact filename above."
  fi

  base="$(strip_version "$f")"
  ver="$(version_of "$f")"
  dest="$(destination_for "$base")"
  [ -n "$dest" ] || fail "Don't know where '$f' belongs."

  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest" || fail "Copy failed for $f"
  if [ -n "$ver" ]; then
    say "Copied: $f  ->  $dest   (version $ver)"
    maxver="$ver"
  else
    say "Copied: $f  ->  $dest   (no version marker)"
  fi
  copied=$((copied + 1))
done

# ---- auto-bump the cache-buster in index.html ----------------------------
# So browsers always fetch the new app.js / style.css. Uses the version from
# the filenames when present, otherwise a timestamp.
stamp="${maxver:-$(date +%s)}"
if [ -f index.html ]; then
  sed -i -E "s|(js/app\.js)(\?v=[0-9]+)?\"|\1?v=${stamp}\"|g; s|(css/style\.css)(\?v=[0-9]+)?\"|\1?v=${stamp}\"|g" index.html
  say "Cache marker in index.html set to ?v=${stamp}"
fi

say ""
git add -A
git --no-pager diff --cached --stat || true

if git diff --cached --quiet; then
  say ""
  say "Nothing changed — these files are identical to what's already committed."
  say "(Did you download the newest version? Check the version number.)"
  exit 0
fi

git commit -m "${DEPLOY_MSG:-Update site (v${stamp})}" || fail "Commit failed."
say ""
say "Pushing..."
git push || fail "Push failed. If it says 'fetch first', run:  git pull --rebase && git push"

say ""
say "Done — $copied file(s) deployed."
say "Live in ~1 min:  https://dusky-nembrotha.github.io/Butterfly-Atlas/"
say ""
say "Tidy up old downloads when convenient:"
say "  rm ~/storage/downloads/*-v*.js ~/storage/downloads/*-v*.css ~/storage/downloads/*-v*.html"
