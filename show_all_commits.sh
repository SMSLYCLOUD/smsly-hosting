echo "All commits (HEAD back 18):"
git log --oneline --graph --decorate HEAD~18..HEAD
echo ""
echo "HEAD hash: $(git rev-parse --short HEAD)"
echo "Modified (untracked=no): $(git status --short --untracked-files=no | wc -l) lines"
echo "Untracked: $(git status --short --untracked-files=all | grep '^??' | wc -l) files"
echo "Changed files last commit: $(git diff --name-only HEAD~1..HEAD | wc -l)"
