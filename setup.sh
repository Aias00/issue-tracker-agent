#!/bin/bash

# Issue Tracker Agent - Quick Start Script

set -e

echo "🚀 Issue Tracker Agent - PostgreSQL + Vector Memory Setup"
echo "=========================================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating from .env.example...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✅ Created .env file. Please edit it with your configuration.${NC}"
    echo ""
    echo "Required configuration:"
    echo "  - DATABASE_URL: PostgreSQL connection string"
    echo "  - GITHUB_TOKEN: Your GitHub personal access token"
    echo "  - LLM_BASE_URL: Your LLM API endpoint"
    echo "  - REPO_PATHS: JSON mapping of repo names to local paths"
    echo ""
    read -p "Press Enter after editing .env to continue..."
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker is not running. Please start Docker and try again.${NC}"
    exit 1
fi

# Start PostgreSQL
echo ""
echo "📦 Starting PostgreSQL with pgvector..."
docker-compose up -d

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
sleep 5

until docker exec issue-tracker-postgres pg_isready -U issue_tracker > /dev/null 2>&1; do
    echo "   Still waiting..."
    sleep 2
done

echo -e "${GREEN}✅ PostgreSQL is ready!${NC}"

# Install Python dependencies
echo ""
echo "📚 Installing Python dependencies..."
pip install -r requirements.txt

# Check if migration is needed
if [ -f "data/issue_tracker.db" ]; then
    echo ""
    echo -e "${YELLOW}⚠️  Found existing SQLite database: data/issue_tracker.db${NC}"
    read -p "Do you want to migrate data to PostgreSQL? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🔄 Migrating data from SQLite to PostgreSQL..."
        python migrate_to_postgres.py \
            data/issue_tracker.db \
            "$(grep DATABASE_URL .env | cut -d '=' -f2)"
        echo -e "${GREEN}✅ Migration complete!${NC}"
    fi
fi

# Initialize database schema
echo ""
echo "🗄️  Initializing database schema..."
python -c "
from app.config import load_config_from_env
from app.storage.pg_store import PostgresStateStore

cfg = load_config_from_env()
store = PostgresStateStore(cfg.app.database_url)
store.init()
print('✅ Database schema initialized')
"

# Check if code indexing is needed
echo ""
read -p "Do you want to index a local code repository now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "Enter repository name (e.g., apache/hertzbeat): " repo_name
    read -p "Enter local path to repository: " repo_path
    
    if [ -d "$repo_path" ]; then
        echo "🔍 Indexing repository: $repo_name at $repo_path"
        python tools/index_code.py "$repo_path" --repo-name "$repo_name"
        echo -e "${GREEN}✅ Indexing complete!${NC}"
    else
        echo -e "${RED}❌ Path not found: $repo_path${NC}"
    fi
fi

# Start the application
echo ""
echo "🎉 Setup complete! Starting the application..."
echo ""
echo "Access the web interface at: http://localhost:8000"
echo "Press Ctrl+C to stop the server"
echo ""

uvicorn app.web.server:app --reload --host 0.0.0.0 --port 8000
