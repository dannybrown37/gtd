#!/bin/bash
set -euo pipefail

[[ -f "${HOME}/.env" ]] && { set -a; source "${HOME}/.env"; set +a; }

PA_USERNAME="${PA_USERNAME:?set PA_USERNAME}"
PA_DOMAIN="${PA_USERNAME}.pythonanywhere.com"
REPO_DIR="/home/${PA_USERNAME}/dotfiles"
PROJECT_DIR="${REPO_DIR}/gtd"
VENV_DIR="${PROJECT_DIR}/.venv"
WSGI_FILE="/var/www/${PA_USERNAME//./_}_pythonanywhere_com_wsgi.py"

command -v uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

if [[ ! -d "${REPO_DIR}" ]]; then
    git clone https://github.com/dannybrown37/dotfiles "${REPO_DIR}"
else
    git -C "${REPO_DIR}" pull
fi

cd "${PROJECT_DIR}"
[[ -d "${VENV_DIR}" ]] || uv venv --python 3.12
uv pip install -e ".[api]"

cat > "${WSGI_FILE}" <<EOF
import sys
import os

path = '${PROJECT_DIR}/src'
if path not in sys.path:
    sys.path.insert(0, path)

os.environ.setdefault('NOTION_NOTES_TOKEN', '${NOTION_NOTES_TOKEN:?}')
os.environ.setdefault('NOTION_PROJECTS_DB_ID', '${NOTION_PROJECTS_DB_ID:?}')
os.environ.setdefault('GTD_API_KEY', '${GTD_API_KEY:?}')

from gtd.api import app as application
EOF

echo "Deployed. Now click Reload on the Web tab for ${PA_DOMAIN}"
