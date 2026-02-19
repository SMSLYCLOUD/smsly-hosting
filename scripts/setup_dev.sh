#!/bin/bash
set -e

echo ">>> Setting up Development Environment..."

# Backend
echo ">>> Setting up Backend (.venv)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Created .venv"
fi

# Activate and Install
source .venv/bin/activate
pip install --upgrade pip
if [ -f "backend/requirements.txt" ]; then
    pip install -r backend/requirements.txt
else
    pip install -r requirements.txt
fi

echo ">>> Backend dependencies installed."

# Frontend
echo ">>> Setting up Frontend (npm)..."
if [ -d "frontend" ]; then
    cd frontend
    npm install
    cd ..
    echo ">>> Frontend dependencies installed."
fi

echo ">>> Setup Complete!"
echo "To activate backend: source .venv/bin/activate"
