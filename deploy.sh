#!/data/data/com.termux/files/usr/bin/bash
# Butterfly Atlas — deploy helper for Termux.
#
# Usage:   ./deploy.sh file1 [file2 ...]        (names as they appear in Downloads)
# Example: ./deploy.sh app.js style.css
#
# Copies the named files from your Downloads folder into the right place in
# the repo, then commits and pushes. Every step is checked and reported, so a
# missing file or wrong folder can never look like a silent success.

set -u
REPO="$HOME/Butterfly-Atlas"
DL="$HOME/storage/downloads"
EXPECTED_REMOTE="Dusky-Nembrotha/Butterfly-Atlas"

say()  { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -ge 1 ] || fail "No files given. Example:  ./deploy.sh app.js style.css"
[ -d "$REPO/.git" ] || fail "No git repo at $REPO — clone it first (see notes)."
[ -d "$DL" ] || fail "Can't see $DL — run 'termux-setup-storage' and tap Allow."

cd "$REPO" || fail "Could not enter $REPO"

# Guard against pushing to the wrong (old) repository.
remote="$(git remote get-url origin 2>/dev/null || true)"
case "$remote" in
  *"$EXPECTED_REMOTE"*) say "Remote OK: $remote" ;;
  "") fail "This repo has no 'origin' remote set." ;;
  *) fail "Remote points at the WRONG repo: $remote
     Fix with: git remote set-url origin https://github.com/$EXPECTED_REMOTE.git" ;;
esac

# Work out where each file belongs, based on its extension/name.
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
for f in "$@"; do
  src="$DL/$f"
  if [ ! -f "$src" ]; then
    say "SKIP: $f not found in Downloads."
    say "      Files currently there:"
    ls "$DL" 2>/dev/null | sed 's/^/        /' | head -20
    fail "Nothing copied. Check the exact filename (downloads are often renamed, e.g. 'app(1).js')."
  fi
  dest="$(destination_for "$f")"
  [ -n "$dest" ] || fail "Don't know where '$f' belongs. Copy it manually."
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest" || fail "Copy failed for $f"
  say "Copied: $f  ->  $dest"
  copied=$((copied + 1))
done

say ""
say "Files staged for commit:"
git add -A
git --no-pager diff --cached --stat || true

if git diff --cached --quiet; then
  say ""
  say "Nothing actually changed — the files are identical to what's already"
  say "committed. (Did you download the newest version?) Nothing pushed."
  exit 0
fi

msg="${DEPLOY_MSG:-Update site ($(date +%Y-%m-%d\ %H:%M))}"
git commit -m "$msg" || fail "Commit failed."
say ""
say "Pushing..."
git push || fail "Push failed — check your GitHub login/token."

say ""
say "Done. $copied file(s) deployed."
say "Live in ~1 min:  https://dusky-nembrotha.github.io/Butterfly-Atlas/"
say "Cache-buster:    https://dusky-nembrotha.github.io/Butterfly-Atlas/?v=$(date +%s)"
